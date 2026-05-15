"""主观题批改的**多次一致性**基准 —— 量「同一份作答反复批改，分数飘多少」。

## 为什么这个数字此前根本不存在

官方题库里**一道主观题都没有**（题型只有 单选/多选/判断/填空/简答）。也就是说
`marking.py` 的批改链路**只在测试夹具上跑过**，从未在像主观题的输入上跑过，
更没有真实模型下的稳定性数字 —— 而「AI 批改的分数稳不稳」恰恰是用户最直接的疑问，
也是主观题批改这个亮点最容易被一句话问倒的地方（「同一篇作文你打两次分一样吗？」）。

## 判据（直接对齐计划里写的那条）

第 9 项的产出证据原文是「**各维度评分标准差 ≤ 0.5**」。所以本表的核心一行是
`dim_std_le_0_5_rate`：四维度里落在 0.5 以内（含）的比例。**低于 1.0 就是没达到承诺**，
要如实写出来，不能只报一个"平均标准差 0.4 看起来还行"。

| 指标 | 读法 |
| --- | --- |
| `dim_std_le_0_5_rate` | **产品承诺的达成率**（各维度标准差 ≤ 0.5） |
| `max_dim_std` | 最差的那一维 —— 平均值会掩盖"有一维特别飘" |
| `agreement` | 各次总分落在均值 ±`AGREEMENT_TOLERANCE`(5 分) 内的比例 |
| `n_valid / n_items` | 有效批改的条目数；为 0 说明接口抖动或依据检索失败，**本表不可信** |

## 两处必须写清的边界

1. **样本是合成的**（`datasets/批改一致性/主观题样本.json`）：真题原文不入库是明确决策
   （ADR-0003）。一致性度量要的是**输入的稳定性**，不是题目的真实性 ——
   同一份作答反复批改的离散度，与题目出自哪里无关。但**样本量只有 2 题**，
   所以它证明的是「这条链路能跑、指标算得出、承诺达不达成」，**不是**「批改在普遍情况下稳」。
2. **`ingest_official_corpus(embed=False)`**：与 `teacher_eval.py` 同口径 ——
   批改依据（rubric）的召回走**关键词通道**。真实 embedding 下的 rubric 召回质量
   不在本表范围内。

跑法：

    python eval/marking_eval.py              # 默认 fake（不花钱）
    $env:LLM_MODE='real'; python eval/marking_eval.py
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

#: 与其它评测一致：**默认 fake**，要测真实模型必须显式指定。
#: 批改还是 N 倍成本（`marking.marking_votes` 默认 3），不默认花钱。
os.environ.setdefault("LLM_MODE", "fake")

#: ⚠️ **用独立的库，不覆盖 `eval_kb.db`** —— 那是别的基准的槽位。
#: 这条是从「一次 fake 冒烟把 30 题的 real 基准覆盖成 6 题的 fake 表」学的：
#: 共享可变状态迟早互相污染，而**文件名就是产物的身份**。
_DB = pathlib.Path(__file__).resolve().parent / "marking_eval.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB.as_posix()}")

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services.kb_corpus import ingest_official_corpus  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.marking import (  # noqa: E402
    AGREEMENT_TOLERANCE,
    DIMENSION_LABELS,
    DIMENSIONS,
    measure_consistency,
    retrieve_rubric,
)
from app.services.scope import NAMESPACE_OFFICIAL, Scope  # noqa: E402

_DATASET = pathlib.Path(__file__).resolve().parent / "datasets" / "批改一致性" / "主观题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"

#: 计划里写明的产品承诺：各维度评分标准差 ≤ 0.5
PROMISED_MAX_STD = 0.5


def _metrics(rows: list[dict]) -> dict:
    """按 (条目 × 维度) 汇总 —— 承诺是按维度给的，就不该先平均掉再判。"""
    pairs = [(r["id"], dim, std) for r in rows for dim, std in (r["per_dim_std"] or {}).items()]
    ok = [p for p in pairs if p[2] <= PROMISED_MAX_STD]
    valid_rows = [r for r in rows if r["n"] > 0]
    return {
        "n_items": len(rows),
        "n_valid": len(valid_rows),
        "n_dim_pairs": len(pairs),
        "dim_std_le_0_5_rate": round(len(ok) / len(pairs), 4) if pairs else 0.0,
        "max_dim_std": max((p[2] for p in pairs), default=0.0),
        "mean_dim_std": round(sum(p[2] for p in pairs) / len(pairs), 2) if pairs else 0.0,
        "min_agreement": min((r["agreement"] for r in valid_rows), default=0.0),
        "max_total_std": max((r["total_std"] for r in valid_rows), default=0.0),
    }


def _write_markdown(path: pathlib.Path, result: dict) -> None:
    m, meta, rows = result["metrics"], result["metadata"], result["items"]
    lines = [
        "# 主观题批改一致性基准（同一作答独立批改 N 次）",
        "",
        f"- 生成时间：{meta['time']}　题数 {m['n_items']}（有效 {m['n_valid']}）"
        f"　每次批改 {meta['votes']} 遍",
        f"- 模型：LLM={meta['llm']}　依据检索：{meta['rubric']}",
        f"- 数据：`eval/datasets/批改一致性/主观题样本.json`（**合成**样本，非真题原文）",
        "",
        f"> **判据**：计划第 9 项的产出证据是「各维度评分标准差 ≤ **{PROMISED_MAX_STD}**」。",
        f"> 下面 `dim_std_le_0_5_rate` 就是它的达成率 —— **低于 1.0 即未达承诺，不许只报平均值**。",
        "",
        "| 指标 | 值 | 读法 |",
        "| --- | --- | --- |",
        f"| **dim_std_le_0_5_rate** | **{m['dim_std_le_0_5_rate']}** | 承诺达成率（各维度标准差 ≤ {PROMISED_MAX_STD}） |",
        f"| max_dim_std | {m['max_dim_std']} | 最差的那一维；平均值会掩盖「有一维特别飘」 |",
        f"| mean_dim_std | {m['mean_dim_std']} | 只看它容易误判 → 与上一行成对看 |",
        f"| min_agreement | {m['min_agreement']} | 各次总分落在均值 ±{AGREEMENT_TOLERANCE} 分内的最低比例 |",
        f"| max_total_std | {m['max_total_std']} | 总分离散度上界 |",
        "",
        "## 逐题",
        "",
        "| 题 | 有效次数 | 均分 | 总分标准差 | 一致率 | 各维度标准差 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        dims = "　".join(
            f"{DIMENSION_LABELS.get(d, d)} {v}" for d, v in (r["per_dim_std"] or {}).items()
        )
        lines.append(
            f"| {r['id']} | {r['n']} | {r['mean_total']} | {r['total_std']} | "
            f"{r['agreement']} | {dims or '（无）'} |"
        )
    lines += [
        "",
        "## 边界（必须与数字一起引用）",
        "",
        f"- **样本是合成的、且只有 {m['n_items']} 题**：证明的是「链路能跑、指标算得出、承诺达不达成」，",
        "  **不是**「批改在普遍情况下稳」；",
        "- 依据检索用 `embed=False`（关键词通道，与 `teacher_eval.py` 同口径），",
        "  真实 embedding 下的 rubric 召回质量不在本表范围；",
        "- `DIMENSIONS` 的四维度是**产品自定口径**，不是官方评分维度 —— 本表量的是",
        "  「模型对同一输入的重复性」，与「分数是否接近官方评分」是两件事。",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(limit: int = 0) -> dict:
    from datetime import datetime

    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    items = (data.get("items") or [])[: limit or None]
    if not items:
        raise SystemExit("样本集为空")

    # 每次运行重建，保证可复现（与 teacher_eval 同做法；库文件是本评测专用的）
    if _DB.exists():
        _DB.unlink()
    init_db()
    db = SessionLocal()
    ingest_official_corpus(db, embed=False)

    client = get_llm_client()
    mode_llm = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    scope = Scope(namespace=NAMESPACE_OFFICIAL)
    print(f"题数 {len(items)}　LLM={mode_llm}　每次批改 {settings.marking_votes} 遍", flush=True)

    rows: list[dict] = []
    for it in items:
        rubric = retrieve_rubric(db, scope, it.get("qtype", ""), k=settings.marking_rubric_k)
        rep = measure_consistency(
            client, it["stem"], it["answer"], rubric, n=settings.marking_votes
        )
        print(
            f"    [{it['id']}] 依据 {len(rubric)} 片 · 有效 {rep.n} 次 · "
            f"均分 {rep.mean_total} · 各维标准差 {rep.per_dim_std}",
            flush=True,
        )
        rows.append({"id": it["id"], **rep.as_dict()})

    metrics = _metrics(rows)
    result = {
        "metrics": metrics,
        "items": rows,
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm": mode_llm,
            "votes": settings.marking_votes,
            "rubric": "official 语料 · embed=False（关键词通道）",
            "dataset": _DATASET.name,
            "caveat": (
                "样本为**合成**且仅 2 题；LLM=fake 时本表只证明链路通、指标算得出，"
                "不是真实模型下的稳定性"
                if mode_llm == "fake"
                else "样本为**合成**且仅 2 题 —— 证明链路与指标，不足以代表普遍稳定性；"
                "四维度是产品自定口径，与官方评分一致性是两件事"
            ),
        },
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "marking_baseline.md"
    # 与 g3_eval 同一条守卫：**不许用 fake 覆盖真实基准**（文件名就是产物的身份）
    if mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = _OUT_DIR / "marking_fake.md"
        print("    已有真实基准，本次 fake 结果改写到 marking_fake.md（不覆盖真基准）")
    _write_markdown(md, result)
    (md.with_suffix(".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"    承诺达成率 {metrics['dim_std_le_0_5_rate']} | 最大维度标准差 {metrics['max_dim_std']} | "
        f"最低一致率 {metrics['min_agreement']}"
    )
    print(f"\n已写入：{md}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只用前 N 题（默认全部）")
    args = ap.parse_args()
    main(limit=args.limit)
