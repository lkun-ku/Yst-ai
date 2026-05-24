"""主观题批改：**rubric-grounded** + 多次批改一致性度量。

## 为什么值得做

科目一（综合素质）的材料分析题 **42 分** + 写作题 **50 分** = 卷面 **61%**，
而市面产品几乎只做客观题。"会背不会写"是跨考生最真实的痛点。
这是 **RAG for Evaluation**（检索增强生成 → 用于评估），三者（QA / Generation / Evaluation）里最稀有的一种。

## rubric-grounded 的含义 —— 以及它**不**承诺什么

批改依据必须来自**可引用的官方文本**（`data/official/rubrics/` 的教师职业道德规范 +
教育法条），模型不得自创评分标准。每条批改意见都带 `quote`，并复用
`citation.verify_payloads` 做**子串硬校验** —— 与出题链路同一个判定。

⚠️ **但 rubrics 语料里的文件本身已经声明过、这里再强调一次**：
教师资格考试主观题的**官方评分细则从未公开发布**，本项目用的是**可引用的采分点来源**，
**不是**阅卷细则。所以产品能承诺的是"依据可查"，**不能**承诺"与官方评分一致"。
这不是免责话术，是事实边界 —— 任何声称"AI 批改与官方一致"的说法都不成立。

## 四维度是**产品自定**的，不是官方口径

`切题度 / 论据 / 结构 / 语言` 是**我们定义的**评分维度，用于给考生结构化反馈。
官方没有公布过这样的四维划分。任何对外表述都必须保留这个区分 ——
把自定维度说成官方口径，就变成了编造。

## 一致性度量：给"AI 批改不可信"一个数字

LLM 主观评分不稳定是公认难题。做法不是辩解，而是**量出来**：
同一份作答独立批改 N 次，输出各维度的标准差与总分一致率。
这让"稳不稳"从抱怨变成指标 —— 也让它成为一个**可以持续改进的目标**。

**N 倍成本**（默认 3），必须由开关控制。且它在 `LLM_MODE=fake` 下**必然恒等于 0**
（确定性替身每次给同样的分）—— 那个 0 **不是**"批改很稳"的证据，只是"替身很稳"。
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field

from ..config import settings
from .citation import split_by_citation

logger = logging.getLogger(__name__)

#: 供 Fake 实现分发的标记
MARK_MARKING = "【主观题批改】"

#: **产品自定**的评分维度（非官方口径，见模块 docstring）
DIMENSIONS: tuple[str, ...] = ("relevance", "evidence", "structure", "language")
DIMENSION_LABELS: dict[str, str] = {
    "relevance": "切题度",
    "evidence": "论据",
    "structure": "结构",
    "language": "语言",
}

#: 各题型检索 rubric 用的查询词。**不用考生原答案**去检索 ——
#: 那会按答案内容找"支持它的依据"，是确认偏误的机器版。
RUBRIC_QUERY: dict[str, str] = {
    "material": "材料分析 教师职业道德规范 关爱学生 教书育人 为人师表 依法执教 评价要点",
    "writing": "写作 立意 结构 论证 教师角色 评价要点",
    "short": "简答 采分点 评价要点",
    "design": "教学设计 教学目标 教学过程 评价要点",
    # 辨析（2026-06-12 补）：数据集里确实用了 `analysis` 这个键，而原先**没有它**，
    # 于是辨析题会静默落到 `default` 的通用检索词 —— 检索到的是一堆泛泛的"评分要点"，
    # 而非辨析题需要的"判断正误 + 说明理由"类依据。辨析是科目一常见题型，不该走 default。
    "analysis": "辨析 判断正误 说明理由 概念区分 评价要点",
    "default": "评分要点 评价依据",
}

#: 一致性判定的容差（总分 ±5 分内视为"一致"）。
AGREEMENT_TOLERANCE = 5.0

#: 总分一致率的产品承诺（§6）：各次总分落在容差内的比例 ≥ 0.80。
AGREEMENT_MIN = 0.80

#: **按题型分档**的维度标准差容忍阈值（百分制）。
#:
#: 为什么不能一个阈值盖所有主观题：7 道样本 × 3 遍实测出明显的**题型梯度** ——
#: 简答 0~5.66 · 辨析 0~1.89 · 设计 1.25~4.9 · 材料分析 0.82~6.6 · **写作 3.4~9.09**。
#: 越主观越飘，这与直觉一致，但**阈值应当是分档的承诺，不是一个数**：
#: 统一阈值会在辨析上过度苛刻、在写作上把"贴着线"误读成"稳"。
#:
#: 取值规则（写清楚，免得将来说不清数字哪来的）：
#: 1. 以实测的**该题型最大维度标准差**为底，向上取整并留约 1 分余量；
#: 2. **一律不宽于**现行承诺换算值 `10.0`（§6 的 0.5/5 分制 → 百分制 10.0）——
#:    分档是让承诺变**具体**，不是给最差的一档开后门；
#: 3. 写作因此**保持 10.0**（实测 9.09，余量仅 0.91）：它是最薄的一档，
#:    应当被标出来盯着，而不是靠调阈值把它变成"达标"。
STD_TOLERANCE_BY_TYPE: dict[str, float] = {
    "analysis": 3.0,     # 实测 1.89
    "design": 6.0,       # 实测 4.9
    "short": 7.0,        # 实测 5.66
    "material": 8.0,     # 实测 6.6
    "writing": 10.0,     # 实测 9.09 —— **余量最薄的一档**，不放宽
    "default": 10.0,     # 未知题型回落到现行承诺换算值
}


def std_tolerance(qtype: str) -> float:
    """按题型取维度标准差的容忍阈值；未知题型回落到 `default`（= 现行承诺换算值）。"""
    return STD_TOLERANCE_BY_TYPE.get(
        str(qtype or "").strip().lower(), STD_TOLERANCE_BY_TYPE["default"]
    )


@dataclass(frozen=True)
class MarkingResult:
    """一次批改的结果。"""

    dimensions: dict[str, float] = field(default_factory=dict)
    total: float = 0.0
    comments: dict[str, str] = field(default_factory=dict)
    deductions: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    citations: tuple[dict, ...] = ()
    grounded: bool = False          # 依据是否可查（引用校验通过）
    refusal_reason: str = ""

    @property
    def refused(self) -> bool:
        return bool(self.refusal_reason)

    def as_dict(self) -> dict:
        return {
            "dimensions": self.dimensions,
            "total": self.total,
            "comments": self.comments,
            "deductions": list(self.deductions),
            "suggestions": list(self.suggestions),
            "citations": list(self.citations),
            "grounded": self.grounded,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
        }


@dataclass(frozen=True)
class ConsistencyReport:
    """同一作答独立批改 N 次的一致性。"""

    n: int
    per_dim_std: dict[str, float]
    total_std: float
    mean_total: float
    agreement: float  # 各次总分落在 均值 ±AGREEMENT_TOLERANCE 内的比例

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "per_dim_std": self.per_dim_std,
            "total_std": self.total_std,
            "mean_total": self.mean_total,
            "agreement": self.agreement,
        }


# ---------------- 依据检索 ----------------

def rubric_query(qtype: str) -> str:
    """按题型取检索词。"""
    return RUBRIC_QUERY.get(str(qtype or "").strip().lower(), RUBRIC_QUERY["default"])


def retrieve_rubric(db, scope, qtype: str, k: int = 6, embed_fn=None) -> list[dict]:
    """检索批改依据（采分点来源）。

    **刻意不用考生答案去检索** —— 那会按答案内容去找"支持它的依据"，
    是确认偏误的机器版。依据应当由**题目类型**决定，与考生答得好不好无关。
    """
    from .kb_retrieval import retrieve

    return retrieve(db, rubric_query(qtype), scope, k=k, embed_fn=embed_fn)


# ---------------- 提示词（与 contract 放在一起，便于对照） ----------------

def _rubric_block(rubric: list[dict]) -> str:
    lines: list[str] = []
    for i, r in enumerate(rubric or [], 1):
        lines.append(f"[依据 #{r.get('id')}｜{r.get('heading_path') or '未分章'}]")
        lines.append(str(r.get("content") or ""))
        lines.append("")
    return "\n".join(lines) or "（没有可引用的依据）"


def marking_prompt(stem: str, answer: str, rubric: list[dict]) -> str:
    dims = "、".join(f"{k}={v}" for k, v in DIMENSION_LABELS.items())
    return (
        "你是教资主观题批改老师。**只能依据下方【评分依据】评判**，"
        "禁止使用依据之外的标准或你自己的偏好。只输出 JSON，不要解释文字或代码块围栏。\n\n"
        f"题目：{stem}\n\n"
        f"考生作答：{answer}\n\n"
        f"【评分依据】\n{_rubric_block(rubric)}\n"
        "输出字段：\n"
        f"1. dimensions：四个维度的百分制分数，键为 {dims}。\n"
        "   —— 这四个维度是**本产品的结构化反馈口径，不是官方评分维度**，按你的专业判断给分。\n"
        "2. comments：每个维度一句评语（键同上）。\n"
        "3. deductions：扣分缘由列表（每条说清「扣在哪、为什么」）。\n"
        "4. suggestions：改进建议列表（要可操作，不要「多练习」这类空话）。\n"
        '5. citations：数组，每项 {"quote": "评分依据里的原文片段"}。\n'
        "   - quote 必须与依据**一字不差**，长度不少于 6 字；程序会逐字比对；\n"
        "   - **每条扣分缘由都应当有对应依据**；若某条判断找不到依据，就不要写它。\n"
        '输出格式：{"dimensions": {...}, "comments": {...}, "deductions": [...], '
        '"suggestions": [...], "citations": [...]}\n'
        f"{MARK_MARKING}"
    )


# ---------------- 解析 ----------------

def parse_marking(text: str) -> dict | None:
    """解析批改输出。无法解析返回 None（调用方转拒批，而不是编一个结果）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        import re

        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    try:
        obj = json.loads(t[t.index("{") : t.rindex("}") + 1])
    except Exception:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("dimensions"), dict):
        return None
    dims: dict[str, float] = {}
    for name in DIMENSIONS:
        raw = obj["dimensions"].get(name)
        if raw is None:
            continue
        try:
            dims[name] = max(0.0, min(100.0, float(raw)))  # 分数一律夹到 0~100
        except (TypeError, ValueError):
            continue
    if not dims:
        return None
    comments = {k: str(v) for k, v in (obj.get("comments") or {}).items() if isinstance(v, str)}
    citations = [
        {"quote": str(c.get("quote") or "")} if isinstance(c, dict) else {"quote": str(c)}
        for c in (obj.get("citations") or [])
    ]
    return {
        "dimensions": dims,
        "comments": comments,
        "deductions": [str(x) for x in (obj.get("deductions") or [])],
        "suggestions": [str(x) for x in (obj.get("suggestions") or [])],
        "citations": citations,
    }


# ---------------- 批改 ----------------

def mark_answer(client, stem: str, answer: str, rubric: list[dict]) -> MarkingResult:
    """按维度批改。

    两条**硬**规则（与出题链路的引用校验同一个哲学）：

    1. **没有依据就不批**：`rubric` 为空 → 直接拒批。
       拿无关材料当依据的"有据批改"，比不批更糟 —— 它会让考生以为意见有出处。
    2. **引用必须能定位**：任一 `quote` 无法在依据里逐字找到 → 整条批改转拒批。
       "句句有出处"不能因为"其余都对"而打折。
    """
    if not rubric:
        return MarkingResult(refusal_reason="没有可引用的评分依据（rubric 语料为空或检索无结果）")
    text = client.ask(marking_prompt(stem, answer, rubric))
    parsed = parse_marking(text)
    if not parsed:
        return MarkingResult(refusal_reason="批改结果无法解析（模型输出不符合 JSON 契约）")

    quotes = [c.get("quote") for c in parsed["citations"]]
    _, blocked, report = split_by_citation(
        [{"source_quotes": quotes}], rubric, min_chars=settings.citation_min_quote_chars
    )
    if report.n_fabricated > 0 or blocked:
        return MarkingResult(
            refusal_reason=f"批改引用了依据里找不到的原文（{report.n_fabricated} 条），已拒绝给出结论"
        )

    dims = parsed["dimensions"]
    total = round(sum(dims.values()) / len(dims), 1) if dims else 0.0
    return MarkingResult(
        dimensions=dims,
        total=total,
        comments=parsed["comments"],
        deductions=tuple(parsed["deductions"]),
        suggestions=tuple(parsed["suggestions"]),
        citations=tuple(parsed["citations"]),
        grounded=True,
    )


def measure_consistency(
    client, stem: str, answer: str, rubric: list[dict], n: int | None = None
) -> ConsistencyReport:
    """同一作答**独立**批改 N 次，输出各维度标准差与总分一致率。

    只统计**成功**的批改次数（拒批的不计入）—— 否则"拒批"会被当成"分数为 0"，
    把一个"依据不足"的问题伪装成"打分不稳"。

    全部拒批时返回 `n=0`：调用方应视为"无法度量"而不是"不一致"。
    """
    times = n if n is not None else settings.marking_votes
    results = [mark_answer(client, stem, answer, rubric) for _ in range(max(1, times))]
    ok = [r for r in results if r.grounded]
    if not ok:
        return ConsistencyReport(n=0, per_dim_std={}, total_std=0.0, mean_total=0.0, agreement=0.0)

    per_dim: dict[str, float] = {}
    for name in DIMENSIONS:
        vals = [r.dimensions[name] for r in ok if name in r.dimensions]
        if len(vals) >= 2:
            per_dim[name] = round(statistics.pstdev(vals), 2)
        elif vals:
            per_dim[name] = 0.0
    totals = [r.total for r in ok]
    mean_total = round(statistics.fmean(totals), 1)
    within = sum(1 for t in totals if abs(t - mean_total) <= AGREEMENT_TOLERANCE)
    return ConsistencyReport(
        n=len(ok),
        per_dim_std=per_dim,
        total_std=round(statistics.pstdev(totals), 2) if len(totals) >= 2 else 0.0,
        mean_total=mean_total,
        agreement=round(within / len(totals), 4),
    )
