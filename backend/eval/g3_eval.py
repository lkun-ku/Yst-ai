"""G3 唯一性投票的真实模型评测 —— 量的是**误杀率**，不是拦截率。

## 为什么这个数字必须测，而且和 ADR-0018 的判据方向相反

ADR-0018 把「闸门拦截率 > 0」当成功判据（闸门得真的拦得住东西）。
但那句话是在"闸门对不对"的语境下说的，**换到已审校的题上就反了**：

这里喂给 G3 的是**官方池里 `proofread_status = PASSED`** 的题 —— 它们已经过了人工/流程审校。
G3 再拦掉它们，拦的是**好题**，所以在这个数据集上：

    拦截率 ≡ 误杀率

而 G3 是 **N 倍成本**、且默认关闭的闸门。它该不该开、`gate_g3_votes` 取几次，
取决于**误杀率有多高** —— 一个会把好题大量拦掉的闸门，开着只会让产题量崩掉。
这个数字此前没有过：G3 只在 `LLM_MODE=fake` 下验证过（见 ADR-0018 的已知边界）。

## 判据（成对，缺一不可）

| 指标 | 含义 | 太低说明 | 太高说明 |
| --- | --- | --- | --- |
| `agree_rate` | N 次盲答彼此一致 | 题的答案确实有歧义（也可能是模型不稳） | — |
| `matches_rate` | 盲答与题目自带答案相符 | 题目答案错 **或** 模型答错（两者无法只凭它区分）| — |
| **`blocked_rate`** | 被 G3 拦截 = **误杀** | 闸门形同虚设 | 开它会让产题量崩掉 |
| `invalid_rate` | 投票全无效（模型不可用） | — | 接口在抖，本次结果不可信 |

⚠️ **`matches_rate` 低不等于"题目答案错"**：模型答错也会让它低。要区分只能人工看样本，
所以本表把**每一道被拦的题连同它的盲答结果一起列出** —— 那是唯一能判断"该拦还是误杀"的材料。

## 数据来源与可复现性

样本是**随仓库走**的 `datasets/g3_唯一性/官方题样本.json`（从 `dev.db` 的官方池
`owner_candidate_id IS NULL AND proofread_status = 'PASSED'` 导出，脚本见下）。
不直接读 `dev.db`：它被 gitignore，基准表若依赖它就无法在新机器上复现 ——
而"基准表是证据"的前提是**证据能被重新算出来**。

导出命令（**只在需要换样本时跑**）：

    python -c "import sqlite3,json,pathlib; ..."   # 见本文件所在的提交信息

⚠️ 枚举列存的是**成员名**（`PASSED` / `SINGLE`），不是小写值 —— 按 `'passed'` 过滤会得 0 行
（这个坑我第一次就踩了）。

## 用法

    python eval/g3_eval.py --limit 30          # 默认 fake（不花钱）
    $env:LLM_MODE='real'; python eval/g3_eval.py --limit 30    # 真实模型
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 与 `teacher_eval.py` 同一条纪律：**默认 fake**，要测真实模型必须显式指定。
#: 否则"跑一下评测"会静默消耗额度 —— 而 G3 本身还是 N 倍成本。
os.environ.setdefault("LLM_MODE", "fake")

from dataclasses import dataclass  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.quality_gates import vote_uniqueness  # noqa: E402

_DATASET = pathlib.Path(__file__).resolve().parent / "datasets" / "g3_唯一性" / "官方题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"


@dataclass
class G3Metrics:
    n_questions: int = 0
    n_valid: int = 0          # 至少有一次有效盲答
    n_agree: int = 0
    n_matches: int = 0
    n_blocked: int = 0
    #: N1 负样本（答案键被改错）中被拦下的题数 —— 由**同一批盲答回放**得到，零额外调用
    n_neg_blocked: int = 0

    @property
    def agree_rate(self) -> float:
        return round(self.n_agree / self.n_valid, 4) if self.n_valid else 0.0

    @property
    def matches_rate(self) -> float:
        return round(self.n_matches / self.n_valid, 4) if self.n_valid else 0.0

    @property
    def blocked_rate(self) -> float:
        """**误杀率**：在已审校题上被 G3 拦截的比例（见模块 docstring）。"""
        return round(self.n_blocked / self.n_valid, 4) if self.n_valid else 0.0

    @property
    def neg_block_rate(self) -> float:
        """N1 负样本的**拦截率**（应接近 1.0）。它与 `blocked_rate` 是成对的：

        - `blocked_rate` 低 = 不误杀好题（闸门**安全**）；
        - `neg_block_rate` 高 = 拦得住坏题（闸门**有效**）。
        只报其中一个都会得出片面结论。
        """
        return round(self.n_neg_blocked / self.n_valid, 4) if self.n_valid else 0.0

    @property
    def invalid_rate(self) -> float:
        return round((self.n_questions - self.n_valid) / self.n_questions, 4) if self.n_questions else 0.0

    def as_row(self) -> dict:
        return {
            "n_questions": self.n_questions,
            "n_valid": self.n_valid,
            "agree_rate": self.agree_rate,
            "matches_rate": self.matches_rate,
            "blocked_rate": self.blocked_rate,
            "neg_block_rate": self.neg_block_rate,
            "invalid_rate": self.invalid_rate,
        }


def _corrupt_key(payload: dict) -> list[str]:
    """把答案键改成一个**错误选项** → N1 负样本。

    真值不依赖任何主观判断：题目的正确选项没动，只是**答案键被改错了**，
    所以"这道题该被拦"是确定的。找不到可用的错误选项时返回 `[]`（该题不计入负样本）。
    """
    keys = [str(o.get("key") or "").strip().upper() for o in (payload.get("options") or [])]
    right = {str(k).strip().upper() for k in (payload.get("answer") or [])}
    for k in keys:
        if k and k not in right:
            return [k]
    return []


def _neg_blocked(payload: dict, votes: tuple) -> bool:
    """用**同一批盲答**回放 N1 负样本，判定闸门是否会拦（与 `apply_uniqueness_gate` 同条件）。

    为什么回放是成立的：G3 的判定只依赖「盲答结果」与「题目自带答案」两者，
    与"模型是怎么答出这个结果的"无关。于是同一次盲答能同时回答两件事 ——
    对原答案键是**误杀**吗，对改错的答案键**拦得住**吗。这样不必再造一批题、再花一遍额度。
    """
    bad = _corrupt_key(payload)
    if not bad:
        return False
    agree = len(set(votes)) == 1
    matches = tuple(votes[0]) == tuple(sorted(k.upper() for k in bad))
    return not (agree and matches)


def _collect(items: list[dict], client) -> tuple[G3Metrics, list[dict]]:
    m = G3Metrics(n_questions=len(items))
    blocked_samples: list[dict] = []
    for i, it in enumerate(items):
        if i and i % 10 == 0:
            # 真实模式下每题要等 N 次调用，静默期很长 → 会被当成卡死
            print(f"    [{i}/{len(items)}] …", flush=True)
        result = vote_uniqueness(client, it, settings.gate_g3_votes)
        if result.n == 0:
            continue  # 全部投票无效 → 算"无法判定"，不计入任何比率
        m.n_valid += 1
        if result.agree:
            m.n_agree += 1
        if result.matches:
            m.n_matches += 1
        if not (result.agree and result.matches):
            m.n_blocked += 1
        # N1 负样本：**零额外调用**，用同一批盲答回放（见 _neg_blocked）
        if _neg_blocked(it, result.votes):
            m.n_neg_blocked += 1
        blocked_samples.append({
            "id": it.get("id"),
            "stem": (it.get("stem") or "")[:70],
            "expect": list(it.get("answer") or []),
            "votes": [list(v) for v in result.votes],
            "agree": result.agree,
            "matches": result.matches,
            "neg_blocked": _neg_blocked(it, result.votes),
        })
    return m, blocked_samples


def _write_markdown(path: pathlib.Path, result: dict) -> None:
    m = result["metrics"]
    meta = result["metadata"]
    lines = [
        "# G3 唯一性投票基准（真实模型下的**误杀率**）",
        "",
        f"- 生成时间：{meta['time']}　题数 {m['n_questions']}（有效 {m['n_valid']}）",
        f"- 模型：LLM={meta['llm']}　每次投票数：{meta['votes']}",
        f"- 数据：`eval/datasets/g3_唯一性/官方题样本.json`（官方池 PASSED 的题）",
        "",
        "> ⚠️ 本表喂的是**已审校的官方题** —— 所以 `blocked_rate` 是**误杀率**，不是拦截战绩。",
        "> G3 是 N 倍成本且默认关闭的闸门：它该不该开、投几次票，取决于它拦掉多少好题。",
        "",
        "| 指标 | 值 | 读法 |",
        "| --- | --- | --- |",
        f"| agree_rate（N 次盲答一致） | {m['agree_rate']} | 低 → 答案确实有歧义，或模型不稳 |",
        f"| matches_rate（与自带答案相符） | {m['matches_rate']} | 低 → 题目答案错**或**模型答错，二者无法只凭它区分 |",
        f"| **blocked_rate（误杀率）** | **{m['blocked_rate']}** | 低 = 闸门**安全**（不把好题拦掉） |",
        f"| **neg_block_rate（N1 负样本拦截率）** | **{m.get('neg_block_rate')}** | 高 = 闸门**有效**（拦得住改错的答案键） |",
        f"| invalid_rate（投票全无效） | {m['invalid_rate']} | 高 → 接口在抖，本表不可信 |",
        "",
        "> ⚠️ **前两行必须成对看**：`blocked_rate` 低只说明不误杀，`neg_block_rate` 高才说明拦得住。",
        "> 只报一个都会得出片面结论 —— 一个读成「闸门无害」，一个读成「闸门有用」。",
        "",
        "## N1 负样本：它证明了什么、没证明什么",
        "",
        "N1 把**答案键**改成一个错误选项，再用**同一批盲答回放**判定（零额外调用，见 `_neg_blocked`）。",
        "真值确定：题目的正确选项没动，只是键被改错了 —— 所以「该被拦」不含主观判断。",
        "",
        "⚠️ **它证明的是「能发现答案键与题意不符」，不是「能发现题干本身有歧义」。**",
        "后者是 G3 更值钱的那一半（题干有歧义时，答案键就算对，题目也不合格），",
        "需要**人工/半自动构造的歧义题**才能测 —— 本数据集不含，属未决（`改造计划.md` §5 第 12 项）。",
        "",
        "## 被拦截的题（含盲答结果）",
        "",
        "> `matches_rate` 低既可能是题错、也可能是模型错 —— **只有这些样本能区分**。",
        "> 全部题（含未被拦的）的逐题投票都记在同名 `.json` 里：只留被拦的等于把证据丢了。",
        "",
    ]
    samples = [
        s for s in (result.get("blocked_samples") or [])
        if not (s.get("agree") and s.get("matches"))
    ]
    if not samples:
        lines.append("- （无）")
    for s in samples:
        lines.append(
            f"- #{s['id']}　一致 {s['agree']}　相符 {s['matches']}　"
            f"期望 {s['expect']}　盲答 {s['votes']}"
        )
        lines.append(f"  - {s['stem']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(limit: int = 30) -> dict:
    from datetime import datetime

    if not _DATASET.exists():
        raise SystemExit(f"评测集不存在：{_DATASET}\n（见本文件 docstring 的导出命令）")
    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    items = (data.get("items") or [])[:limit]
    if not items:
        raise SystemExit("评测集为空")

    client = get_llm_client()
    mode_llm = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    print(f"题数 {len(items)}　LLM={mode_llm}　每次投票 {settings.gate_g3_votes} 次", flush=True)

    m, blocked = _collect(items, client)
    result = {
        "metrics": m.as_row(),
        "blocked_samples": blocked,
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm": mode_llm,
            "votes": settings.gate_g3_votes,
            "dataset": str(_DATASET.name),
            "caveat": (
                "数据是**已审校的官方题**，故 blocked_rate 是**误杀率**而非拦截战绩；"
                "LLM=fake 时本表只证明链路通、判据算得出，不是真实模型下的误杀率"
                if mode_llm == "fake"
                else "本表为**真实模型**（LLM=real）下实测；matches_rate 低无法区分"
                "'题错'与'模型错'，需人工看被拦样本"
            ),
        },
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "g3_baseline.md"
    # ⚠️ **不许用 fake 覆盖真实基准**。文件名就是基准表的身份 —— 混着写会让
    # "这是真实数字吗"变成一个必须翻 metadata 才能回答的问题，而"看一眼文件名就下结论"
    # 是人的默认行为。这条守卫是**踩出来的**：一次 fake 冒烟（--limit 8）把 30 题的 real
    # 基准覆盖成了 8 题的 fake 表，而它在文件名上与真基准毫无区别。
    if mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = _OUT_DIR / "g3_fake.md"
        print("    已有真实基准，本次 fake 结果改写到 g3_fake.md（不覆盖真基准）")
    _write_markdown(md, result)
    (md.with_suffix(".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    row = result["metrics"]
    print(
        f"    agree {row['agree_rate']} | matches {row['matches_rate']} | "
        f"**误杀 {row['blocked_rate']}** | **N1 拦截 {row['neg_block_rate']}** | "
        f"无效 {row['invalid_rate']}"
    )
    print(f"\n已写入：{md}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="用多少道题（默认 30）")
    args = ap.parse_args()
    main(limit=args.limit)
