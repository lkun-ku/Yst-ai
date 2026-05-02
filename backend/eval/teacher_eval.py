"""问答老师的度量：**有据 vs 无据**对照 + 引用首次通过率 + 拒答率区间。

## 这张表回答什么

计划要的三组数字（「引用首次通过率」「拒答率区间」「无据版对照」）在这里：

| 指标 | 口径 | 为什么这么定 |
| --- | --- | --- |
| 引用首次通过率 | 有引用的回答里，`exact + normalized / 有引用` | 「首次」很重要：校验失败会转拒答，只看最终输出恒为 1.0、没有信息量 |
| **正确拒答率** | 不可答的问题里被拒答的比例 | 拒答是本产品的**正确行为**，不是失败 |
| **误拒率** | 可答的问题里被拒答的比例 | 与上一条**必须分开** —— 合成一个"拒答率"会掩盖方向 |
| 工具调用均值 | agent 模式的 `tool_calls` | 成本代理 |
| 无据对照 | `mode=plain` 的引用通过率 | 应为 0：它不检索，也就没有可核对的依据 |

**为什么拒答率必须拆成两个方向**：只报一个"拒答率 18%"是没用的 ——
它可能是"该拒的都拒了"（好），也可能是"把能答的也拒了"（坏），
两者的产品含义完全相反。拆开后才知道该收紧提示词还是该放松判据。

## 两个刻意的设定

1. **`RERANK_IMPL=off`**：本脚本测的是"有据答疑"这一层，检索质量已由
   `retrieval_eval.py` 单独测量。关掉精排让本表**不受检索侧改动影响**（可归因），
   也快得多（否则每题要过 20 次 ONNX 前向）。
2. **`LLM_MODE` 由环境决定，但结果里必须标出来**：
   `fake` 下这张表只证明「链路通、指标算得出、拒答判据生效」，
   **不是**"真实模型下答得准"的证据。

运行：
    python backend/eval/teacher_eval.py
    python backend/eval/teacher_eval.py --limit 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
os.environ["DATABASE_URL"] = os.environ.get("EVAL_DATABASE_URL") or "sqlite:///" + os.path.join(
    _ROOT, "backend", "eval", "eval_kb.db"
)
# 必须在**在导入 app 之前**设：settings 在 import 时就读取环境。
os.environ.setdefault("RERANK_IMPL", "off")
# LLM 默认 fake：本仓的 `.env` 是 `LLM_MODE=real`，直接跑会**逐题打真实接口** ——
# 要么花钱，要么（像本机这样）连不上而每题重试到超时。
# 要测真实模型下的表现，显式 `LLM_MODE=real` 运行，并在结果里核对 llm 字段。
os.environ.setdefault("LLM_MODE", "fake")

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.kb_corpus import ingest_official_corpus  # noqa: E402
from app.services.kb_retrieval import load_chunks_for_scope  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.scope import NAMESPACE_OFFICIAL, Scope  # noqa: E402
from app.services.teacher_agent import ask  # noqa: E402

try:
    from .retrieval_eval import build_law_labels
except ImportError:  # pragma: no cover
    from eval.retrieval_eval import build_law_labels  # noqa: E402

#: 库里**没有**的问题：正确答案是拒答。用来测"该拒的拒了吗"。
#: 刻意选"看起来很像真的"的问法（都用了真实的法名），否则模型靠"这名字没听过"就能蒙对。
_UNANSWERABLE = (
    "《民法典》第一千二百六十条对高空抛物是怎么规定的？",
    "《教师法》第九十九条关于教师申诉是怎么规定的？",
    "《义务教育法》第两百条对教材选用是怎么规定的？",
    "《未成年人保护法》关于未成年人上网时长的具体小时数是怎么规定的？",
    "教育部2027年新发布的教师编制改革文件是怎么说的？",
)


@dataclass(frozen=True)
class TeacherMetrics:
    n_answerable: int
    n_unanswerable: int
    answered: int          # 可答问题里给出了回答的
    cited_ok: int          # 回答里引用数是 0 的会被单独看，这里只统计"有引用且可定位"
    cited_given: int       # 回答里有引用的条数
    refused_answerable: int
    refused_unanswerable: int
    tool_calls: int

    @property
    def cite_first_pass_rate(self) -> float:
        """引用首次通过率 = 有引用且可定位 / 有引用（**不看最终输出**）。"""
        return round(self.cited_ok / self.cited_given, 4) if self.cited_given else 0.0

    @property
    def false_refusal_rate(self) -> float:
        """误拒率：可答却拒答。过高说明判据太紧或提示词没让模型用上材料。"""
        return round(self.refused_answerable / self.n_answerable, 4) if self.n_answerable else 0.0

    @property
    def correct_refusal_rate(self) -> float:
        """正确拒答率：不可答而拒答。过低说明在硬编。"""
        if not self.n_unanswerable:
            return 0.0
        return round(self.refused_unanswerable / self.n_unanswerable, 4)

    @property
    def avg_tool_calls(self) -> float:
        total = self.n_answerable + self.n_unanswerable
        return round(self.tool_calls / total, 2) if total else 0.0

    def as_row(self) -> dict:
        return {
            "n_answerable": self.n_answerable,
            "n_unanswerable": self.n_unanswerable,
            "answered": self.answered,
            "cite_first_pass_rate": self.cite_first_pass_rate,
            "false_refusal_rate": self.false_refusal_rate,
            "correct_refusal_rate": self.correct_refusal_rate,
            "avg_tool_calls": self.avg_tool_calls,
        }


def _collect(db, questions: list[str], mode: str, client) -> tuple[TeacherMetrics, list[dict]]:
    scope = Scope(namespace=NAMESPACE_OFFICIAL)
    n_ans = len(questions) - len(_UNANSWERABLE)
    fields = dict(
        n_answerable=n_ans,
        n_unanswerable=len(_UNANSWERABLE),
        answered=0,
        cited_ok=0,
        cited_given=0,
        refused_answerable=0,
        refused_unanswerable=0,
        tool_calls=0,
    )
    samples: list[dict] = []
    for i, q in enumerate(questions):
        if i and i % 10 == 0:
            # 进度输出：真实模式下每题都要等接口，静默期太长会被当成卡死
            print(f"    [{mode}] {i}/{len(questions)} …", flush=True)
        r = ask(db, 0, q, scope, mode=mode, client=client)
        fields["tool_calls"] += r["tool_calls"]
        answerable = i < n_ans
        if r["refused"]:
            fields["refused_answerable" if answerable else "refused_unanswerable"] += 1
        else:
            fields["answered"] += 1
            if r["citations"]:
                fields["cited_given"] += 1
                if r["citation"].get("fabricated", 0) == 0 and r["citation"].get("exact", 0) + r["citation"].get(
                    "normalized", 0
                ) > 0:
                    fields["cited_ok"] += 1
        if len(samples) < 5 or (answerable and r["refused"]):
            samples.append(
                {
                    "q": q,
                    "answerable": answerable,
                    "refused": r["refused"],
                    "reason": r["refusal_reason"],
                    "quote": (r["citations"][0]["quote"][:40] if r["citations"] else ""),
                }
            )
    return TeacherMetrics(**fields), samples


def main(limit: int = 40) -> dict:
    db_file = os.path.join(_ROOT, "backend", "eval", "eval_kb.db")
    if os.path.exists(db_file):
        os.remove(db_file)
    init_db()
    out_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_dir, exist_ok=True)

    db = SessionLocal()
    ingest_official_corpus(db, embed=False)
    chunks = load_chunks_for_scope(db, Scope(namespace=NAMESPACE_OFFICIAL))

    # 可答问题：由法条语料程序化生成（编号类，与 retrieval_eval 同一套思路）
    labels = build_law_labels(chunks, limit=limit)
    answerable = [f"{x['query'].split(' ')[0]} 是怎么规定的？" for x in labels]
    questions = answerable + list(_UNANSWERABLE)

    client = get_llm_client()
    mode_llm = f"LLM={os.environ.get('LLM_MODE', 'fake')}（{type(client).__name__}）"
    print(f"问题数 {len(questions)}（可答 {len(answerable)} + 不可答 {len(_UNANSWERABLE)}）｜{mode_llm}")

    rows: dict[str, dict] = {}
    detail: dict[str, list] = {}
    for mode in ("grounded", "agent", "plain"):
        m, samples = _collect(db, questions, mode, client)
        rows[mode] = m.as_row()
        detail[mode] = samples
        r = rows[mode]
        print(
            f"  {mode:>8} | 引用首过 {r['cite_first_pass_rate']} | 误拒 {r['false_refusal_rate']}"
            f" | 正确拒答 {r['correct_refusal_rate']} | 工具均值 {r['avg_tool_calls']}"
        )

    result = {
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "n_questions": len(questions),
            "n_answerable": len(answerable),
            "n_unanswerable": len(_UNANSWERABLE),
            "llm": mode_llm,
            "rerank": os.environ.get("RERANK_IMPL", "off"),
            "caveat": "LLM=fake 时本表只证明链路通与判据生效，不是真实模型下答得准的证据",
        },
        "rows": rows,
        "samples": detail,
    }
    _write_markdown(os.path.join(out_dir, "teacher_baseline.md"), result)
    with open(os.path.join(out_dir, "teacher_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已写入：{os.path.join(out_dir, 'teacher_baseline.md')}")
    return result


def _write_markdown(path: str, result: dict) -> None:
    meta, rows = result["metadata"], result["rows"]
    lines = [
        "# 问答老师基准（有据答疑）",
        "",
        f"- 生成时间：{meta['time']}　问题 {meta['n_questions']} 条"
        f"（可答 {meta['n_answerable']} + 不可答 {meta['n_unanswerable']}）",
        f"- 模型：{meta['llm']}　精排：{meta['rerank']}",
        "",
        f"> ⚠️ {meta['caveat']}。",
        "",
        "> **拒答率必须拆两个方向看**：只报一个「拒答率 18%」没有信息量 ——",
        "> 它可能是「该拒的都拒了」（好），也可能是「把能答的也拒了」（坏），产品含义相反。",
        "",
        "| 模式 | 引用首次通过率 | 误拒率（可答却拒答） | 正确拒答率（不可答而拒答） | 工具调用均值 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for mode, r in rows.items():
        lines.append(
            f"| {mode} | {r['cite_first_pass_rate']} | {r['false_refusal_rate']}"
            f" | {r['correct_refusal_rate']} | {r['avg_tool_calls']} |"
        )
    lines += ["", "## 误拒样本（可答却被拒答，最值得看的一类）", ""]
    for mode, samples in result["samples"].items():
        bad = [s for s in samples if s["answerable"] and s["refused"]]
        for s in bad[:3]:
            lines.append(f"- [{mode}] {s['q']} → {s['reason']}")
    if not any(s["answerable"] and s["refused"] for ss in result["samples"].values() for s in ss):
        lines.append("- （无）")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="可答问题条数")
    args = ap.parse_args()
    main(limit=args.limit)
