"""出题质量闸门 G2（事实一致性）与 G3（答案唯一性投票）。

出题链路现在有四道闸，各自回答一个**不同**的问题，且**顺序即成本顺序**：

| 闸 | 问题 | 成本 | 判定性质 |
| --- | --- | --- | --- |
| G1 结构校验（`validation.py`） | 格式对不对 | 零 | 确定性 |
| 引用校验（`citation.py`） | 模型**声明的**依据在不在材料里 | 零 | 确定性 |
| **G2 事实一致性**（本模块） | 题干/解析里**引用的法条**是否真存在 | 零（硬匹配）| 确定性 |
| LLM 自检（`kb_generate`） | 质量好不好 | 高 | 概率性 |
| **G3 唯一性投票**（本模块） | 答案唯一吗 | **N 倍** | 概率性 |

## G2 与"引用校验"为什么是两件事

引用校验只看模型**主动声明**的 `source_quote`；而模型完全可能**不在** `source_quote` 里
申报，却把一条法条写进题干（"根据《教师法》第八十七条规定……"）。
那句法条若是编的，引用校验一点也拦不住 —— 因为模型根本没把它当作"引用"申报。
G2 补的就是这个缺口：**扫题干/选项/解析里出现的所有「《X》第N条」，逐条核对是否真在依据里。**

**条号必须精确匹配**（与 `tools.lookup_law` 同一条纪律）：
`《教师法》第七十七条` 不能因为库里存在 `第七条` 就算命中 —— 那正是编造条文的一种形态。

## G3 为什么必须"盲答"

已有的 LLM 自检是**看着答案**打分（问"这个答案对吗"），它对「答案本身有歧义」完全不敏感：
一道模棱两可的题，答案写成哪个它都会说"对"。
所以 G3 采用**盲答投票**：不给模型看答案，让它独立推导 N 次。
- N 次结果**不一致** → 题目有歧义（该弃）；
- N 次一致但**与题目的答案不符** → 题目答案是错的（更该弃）。

**默认关闭，因为它是 N 倍成本**（`settings.gate_g3_votes` 默认 3）。
只在"高价值题"上开 —— 例如官方语料出的题。这与计划里那条成本纪律一致：
G3 与批改一致性度量都是 3 倍开销，必须用开关控制，不能默认全开。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

from ..config import settings

logger = logging.getLogger(__name__)

#: 「《法名》第N条」。法名限长 30 字（真实法名不超过这个量级），
#: 免得把一段带书名号的普通文本误当成法条引用。
_LAW_REF_RE = re.compile(r"《([^》]{1,30})》\s*(第[一二三四五六七八九十百零〇\d]+条)")

#: 参与 G2 扫描的字段：**题面与解析都要扫**，而不只是 `source_quote`。
_PAYLOAD_TEXT_FIELDS = ("stem", "explanation")

#: G3 只对有选项键的题型有意义（single / multiple / judge）。
_CHOICE_TYPES = ("single", "multiple", "judge")


@dataclass(frozen=True)
class LawRef:
    """一处法条引用（法名 + 条号）。"""

    law: str
    article: str

    def as_text(self) -> str:
        return f"《{self.law}》{self.article}"


def extract_law_refs(text: str) -> list[LawRef]:
    """提取文本里的全部法条引用（去重保序）。

    去重很重要：一道题的题干与解析可能各提一次同一法条，
    重复计数会让 G2 的报告看起来比实际严重。
    """
    seen: dict[str, LawRef] = {}
    for m in _LAW_REF_RE.finditer(str(text or "")):
        ref = LawRef(law=m.group(1).strip(), article=m.group(2).strip())
        seen.setdefault(ref.as_text(), ref)
    return list(seen.values())


def payload_text(payload: dict) -> str:
    """把题面与解析拼成一段供扫描（也含选项文本）。"""
    parts = [str(payload.get(f) or "") for f in _PAYLOAD_TEXT_FIELDS]
    for o in payload.get("options") or []:
        if isinstance(o, dict):
            parts.append(str(o.get("text") or ""))
        else:
            parts.append(str(o))
    return "\n".join(parts)


def _ref_locatable(ref: LawRef, chunks: Sequence[dict]) -> bool:
    """该法条是否真在这批依据里。

    法名允许互为子串（「教师法」与「中华人民共和国教师法」都算命中）——
    模型写简称是正常现象。但**条号必须精确**：`heading_path` 的最后一段
    要恰好等于这个条号，否则「第七十七条」会被「第七条」蒙对。
    """
    for c in chunks or []:
        hp = str(c.get("heading_path") or "")
        content = str(c.get("content") or "")
        hay = hp or content
        if ref.law not in hay and hay not in ref.law:
            continue
        tail = hp.rsplit(" / ", 1)[-1].strip() if hp else ""
        if tail == ref.article:
            return True
        # 个人资料类语料没有「法名 / 章 / 条」的 heading 结构，
        # 退化为"正文里是否出现该条号"。
        # 注意这里仍然要求**条号精确出现**：`第七十七条` 里不含 `第七条`
        # （第-七-十-七-条 不是连续子串），所以这条判据不会放松精度。
        if not hp and ref.article in content:
            return True
    return False


def check_facts(payload: dict, chunks: Sequence[dict]) -> list[str]:
    """G2 硬匹配：返回问题列表（空 = 通过）。**零模型调用。**"""
    problems: list[str] = []
    for ref in extract_law_refs(payload_text(payload)):
        if not _ref_locatable(ref, chunks):
            problems.append(f"引用的法条不在依据中：{ref.as_text()}")
    return problems


def apply_fact_gate(
    payloads: list[dict], chunks: list[dict], emit=None
) -> tuple[list[dict], list[dict]]:
    """G2 闸门：题干/解析里引用了不存在的法条 → 拦截。

    拦截是**硬**的（与引用校验同级）：编造法条是本产品唯一致命风险，
    不接受"配置开关"来放行。`gate_g2_enabled` 只控制闸门是否启用。
    """
    items = list(payloads or [])
    if not items or not settings.gate_g2_enabled:
        return items, []
    kept: list[dict] = []
    blocked: list[dict] = []
    for p in items:
        problems = check_facts(p, chunks)
        if problems:
            blocked.append({**p, "_gate": "G2", "_problems": problems})
        else:
            kept.append(p)
    if blocked and emit:
        emit(
            "gate_g2",
            f"G2 事实一致性 · 拦截 {len(blocked)} 题（题干/解析引用了不存在的法条）",
            {
                "blocked": [
                    {"stem": str(b.get("stem") or "")[:60], "problems": b["_problems"][:3]}
                    for b in blocked[:5]
                ]
            },
        )
    return kept, blocked


# ---------------- G3：答案唯一性投票 ----------------

@dataclass(frozen=True)
class VoteResult:
    """一次 G3 投票的结果。"""

    votes: tuple[tuple[str, ...], ...]
    agree: bool          # N 次盲答彼此一致
    matches: bool        # 盲答与题目自带答案一致
    n: int

    @property
    def passed(self) -> bool:
        return self.n > 0 and self.agree and self.matches

    def as_dict(self) -> dict:
        return {"n": self.n, "agree": self.agree, "matches": self.matches, "passed": self.passed}


def _normalize_keys(keys) -> tuple[str, ...]:
    """答案键归一化：排序 + 去空白（多选题的键序不该影响一致性判定）。"""
    return tuple(sorted({str(k).strip().upper() for k in (keys or []) if str(k).strip()}))


def vote_once(client, payload: dict) -> tuple[str, ...] | None:
    """**盲答一次**：不给模型看答案，让它独立作答。

    这是 G3 与「生成自检」的本质差别：自检看着答案评"对不对"，
    对"答案本身有歧义"完全不敏感；盲答才能测出歧义。
    模型不可用/解析失败返回 None（调用方按"这次投票无效"处理，不计入不一致）。
    """
    from .prompts_kb import blind_answer_prompt, parse_blind_answer

    text = client.ask(
        blind_answer_prompt(
            payload.get("stem"), payload.get("options"), str(payload.get("type") or "single")
        )
    )
    if not text:
        return None
    keys = parse_blind_answer(text)
    return _normalize_keys(keys) if keys else None


def vote_uniqueness(client, payload: dict, n: int | None = None) -> VoteResult:
    """对一道题盲答 `n` 次，判断答案是否唯一。

    无效投票（模型不可用）**直接丢弃**而不是算作"不一致" ——
    否则一次接口抖动就会把好题判成坏题。若全部投票无效，返回 `n=0`，
    调用方应视为"无法判定"而非"未通过"。
    """
    times = n if n is not None else settings.gate_g3_votes
    votes: list[tuple[str, ...]] = []
    for _ in range(max(1, times)):
        v = vote_once(client, payload)
        if v:
            votes.append(v)
    if not votes:
        return VoteResult((), False, False, 0)
    agree = len(set(votes)) == 1
    matches = votes[0] == _normalize_keys(payload.get("answer"))
    return VoteResult(tuple(votes), agree, matches, len(votes))


@dataclass(frozen=True)
class OptionVerdict:
    """G3' 逐选项判定的结果。

    与 `VoteResult` 的对应关系：`stable` ≈ `agree`（多次判定是否一致），
    `matches` 同义（判定结果与自带答案是否相符）；多出来的是 **`judged`** ——
    **被判成立的选项集合**，正是它把"并列正确"从不可见变成可见。
    """

    votes: tuple[tuple[str, ...], ...]  # 每次投票判「成立」的选项键集合（已归一化排序）
    judged: tuple[str, ...]             # 多数表决后成立的选项
    stable: bool
    matches: bool
    n: int

    @property
    def n_judged(self) -> int:
        """被判成立的选项个数 —— **> 答案键个数即存在并列正确答案**。"""
        return len(self.judged)

    @property
    def passed(self) -> bool:
        return self.n > 0 and self.stable and self.matches

    @property
    def passed_under_policy(self) -> bool:
        """按**配置的口径**判定（稳定性是否单独触发拦截）。

        ## 为什么要留两个口径（2026-06-16 离线 A/B）

        在 254 道官方好题 + 36 道歧义题上反算（`eval/g3_stability_ab.py`，零额度）：

        | | 现状（稳定性可单独拦） | 关掉稳定性单独拦 |
        | --- | --- | --- |
        | 官方好题误杀率 | 4.72%（12/254） | **3.54%**（9/254），释放 3 道且多数票都==答案键 |
        | 歧义题拦截率 | 61.11%（22/36） | 55.56%（20/36），漏放 2 道 |

        结论是**收益确定但很小（3 道），代价不确定且可能更大（2/36 ≈ 5.6pp，而 n=36 的
        置信区间宽到 ±16pp）** —— 数据不支持现在改默认口径，所以默认仍是严格口径。

        但把口径做成开关是有价值的：以后要在真实流量上比较，只需改一个环境变量，
        不必改代码。⚠️ 两个口径都**不放松 `matches`** —— 是否放行仍以"多数表决结果
        等于答案键"为准，稳定性只是**要不要额外要求它**。
        """
        if self.n <= 0 or not self.matches:
            return False
        return self.stable if settings.gate_g3_block_on_instability else True

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "stable": self.stable,
            "matches": self.matches,
            "judged": list(self.judged),
            "n_judged": self.n_judged,
            "passed": self.passed,
            "passed_under_policy": self.passed_under_policy,
        }


def judge_options_once(client, payload: dict) -> tuple[str, ...] | None:
    """逐选项判定一次 → 返回**成立的选项键集合**。

    `None` = 这次判定无效（模型不可用 / 解析不出任何一项），调用方按"投票无效"丢弃；
    **空元组** = 模型认为没有一个选项成立 —— 那是有效判定，只是与答案不符。
    两者必须分开：混同会把"接口抖了一下"变成"这道题不合格"。
    """
    from .prompts_kb import parse_per_option, per_option_prompt

    text = client.ask(
        per_option_prompt(
            payload.get("stem"), payload.get("options"), str(payload.get("type") or "single")
        )
    )
    if not text:
        return None
    verdicts = parse_per_option(text)
    if not verdicts:
        return None
    keys = [k for k, ok in verdicts.items() if ok]
    return _normalize_keys(keys)


def vote_per_option(client, payload: dict, n: int | None = None) -> OptionVerdict:
    """**G3'**：对每个选项独立判是否成立，投 `n` 次。

    ## 为什么要有它 —— 旧判据缺的那一步

    旧判据 `vote_uniqueness` 问的是「**选哪个**」，模型被迫选一个，
    于是「被舍弃的那个也同样正确」这件事**根本不会出现在投票结果里**。
    实测（`eval/g3_ambiguity.py`，2026-06-12）：20 道"两个选项都说得通"的歧义题，
    旧判据**只拦下 15%**，17 条被放行。

    这里改问「**每个选项对不对**」，并明确要求"不要因为已有选项成立就判其它不成立" ——
    于是并列正确会直接表现为**同时有多个选项被判成立**（`judged` 长度 > 答案键长度）。

    ## 成本与旧判据**持平**

    每次调用里**一次性**给出所有选项的是/否，而不是"每个选项一次调用" ——
    否则成本是 `选项数 × 投票数`（4×3=12 次/题，旧判据的 4 倍），贵到不可能上线。

    ## 无效判定的处理

    与旧判据同原则：**全部无效 → 视为"无法判定"而非"未通过"**，
    否则一次接口抖动就会把好题判成坏题。
    """
    times = n if n is not None else settings.gate_g3_votes
    votes: list[tuple[str, ...]] = []
    for _ in range(max(1, times)):
        v = judge_options_once(client, payload)
        if v is not None:
            votes.append(v)
    if not votes:
        return OptionVerdict((), (), False, False, 0)

    # 多数表决：某选项在**过半**投票里被判成立 → 判它成立。
    # 用多数而不是"全部"：单次判定被措辞扰动一下就全盘推翻，会让闸门过于敏感。
    need = len(votes) // 2 + 1
    counts: dict[str, int] = {}
    for v in votes:
        for k in v:
            counts[k] = counts.get(k, 0) + 1
    judged = tuple(sorted(k for k, c in counts.items() if c >= need))
    stable = len(set(votes)) == 1
    matches = judged == _normalize_keys(payload.get("answer"))
    return OptionVerdict(tuple(votes), judged, stable, matches, len(votes))


def apply_uniqueness_gate(client, payloads: list[dict], emit=None) -> tuple[list[dict], list[dict]]:
    """G3 闸门：答案不唯一的题拦截。**N 倍成本，默认关闭。**

    只处理有选项键的题型；其余题型原样放行（简答/填空没有"唯一答案"这个概念，
    对它们谈唯一性是概念错误，不是"没通过"）。
    """
    items = list(payloads or [])
    if not items or not settings.gate_g3_enabled:
        return items, []
    kept: list[dict] = []
    blocked: list[dict] = []
    for p in items:
        if str(p.get("type") or "single").strip().lower() not in _CHOICE_TYPES:
            kept.append(p)
            continue
        if settings.gate_g3_per_option:
            v = vote_per_option(client, p)
            if v.n == 0:
                # 同原则：无法判定 → 放行（把接口抖动当成题不合格会让欠产飙升）
                kept.append(p)
                continue
            if v.passed:
                kept.append(p)
                continue
            reasons = []
            if not v.stable:
                reasons.append(f"多次逐项判定不一致（{[list(x) for x in v.votes]}）")
            if not v.matches:
                expect = list(_normalize_keys(p.get("answer")))
                if v.n_judged > len(expect):
                    reasons.append(
                        f"被判成立的选项多于答案键（判定 {list(v.judged)} vs 答案 {expect}）："
                        f"存在并列正确答案"
                    )
                else:
                    reasons.append(f"逐项判定与自带答案不符（判定 {list(v.judged)} vs 答案 {expect}）")
            blocked.append({**p, "_gate": "G3", "_problems": reasons, "_votes": v.as_dict()})
            continue
        result = vote_uniqueness(client, p)
        if result.n == 0:
            # 无法判定 → 放行并把事实记清楚。把"接口抖动"当成"题不合格"会让欠产飙升。
            kept.append(p)
            continue
        if result.passed:
            kept.append(p)
        else:
            reasons = []
            if not result.agree:
                reasons.append("多次独立作答结果不一致（答案有歧义）")
            if not result.matches:
                reasons.append("独立作答与题目自带答案不符")
            blocked.append({**p, "_gate": "G3", "_problems": reasons, "_votes": result.as_dict()})
    if emit:
        emit(
            "gate_g3",
            f"G3 唯一性投票 · {len(items)} 题 × {settings.gate_g3_votes} 次 · "
            f"通过 {len(kept)} / 拦截 {len(blocked)}",
            {
                "blocked": [
                    {"stem": str(b.get("stem") or "")[:60], "problems": b["_problems"]}
                    for b in blocked[:5]
                ]
            },
        )
    return kept, blocked
