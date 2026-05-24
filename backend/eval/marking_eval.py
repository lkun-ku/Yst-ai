"""主观题批改的**多次一致性**基准 —— 量「同一份作答反复批改，分数飘多少」。

## 为什么这个数字此前根本不存在

官方题库里**一道主观题都没有**（题型只有 单选/多选/判断/填空/简答）。也就是说
`marking.py` 的批改链路**只在测试夹具上跑过**，从未在像主观题的输入上跑过，
更没有真实模型下的稳定性数字 —— 而「AI 批改的分数稳不稳」恰恰是用户最直接的疑问，
也是主观题批改这个亮点最容易被一句话问倒的地方（「同一篇作文你打两次分一样吗？」）。

## 判据与量纲

计划 §6 的承诺原文是「**各维度评分标准差 ≤ 0.5（5 分制）**」，而本实现的维度是
**百分制** —— 按比例换算（0.5 ÷ 5 = 10%）后，百分制下的等价阈值是 **10.0**。
这是**把量纲写对，不是把承诺放宽**：相对严格度不变（第一次跑没换算时"全部超标"，
实测就是这么发现的）。

## 阈值**按题型分档**（2026-06-12）

单一阈值 10.0 会掩盖两件事：在辨析上过度苛刻（实测只飘 1.89），在写作上把"贴着线"
误读成"稳"（实测 9.09，余量仅 0.91）。7 道样本 × 3 遍实测出**题型梯度**：

    简答 0~5.66 · 辨析 0~1.89 · 设计 1.25~4.9 · 材料分析 0.82~6.6 · 写作 3.4~9.09

所以判定改用 `marking.std_tolerance(qtype)` —— **阈值一律不宽于现行承诺**，
分档是让承诺变具体，不是给最差的一档开后门。

| 指标 | 读法 |
| --- | --- |
| `dim_std_ok_rate` | 各 (题 × 维度) 落在**该题型的阈值**内的比例 |
| `max_dim_std` / `margin` | 最差的一维；**余量**（阈值 − 实测）比"是否达标"更有用 |
| `agreement` | 各次总分落在均值 ±`AGREEMENT_TOLERANCE`(5 分) 内的比例；承诺 ≥ `AGREEMENT_MIN`(0.80) |
| `n_valid / n_items` | 有效批改的条目数；为 0 说明接口抖动或依据检索失败，**本表不可信** |

## 两处必须写清的边界

1. **样本是合成的**（`datasets/批改一致性/主观题样本.json`）：真题原文不入库是明确决策
   （ADR-0003）。一致性度量要的是**输入的稳定性**，不是题目的真实性 ——
   同一份作答反复批改的离散度，与题目出自哪里无关。但**样本量很小**，
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
    AGREEMENT_MIN,
    AGREEMENT_TOLERANCE,
    DIMENSION_LABELS,
    measure_consistency,
    retrieve_rubric,
    std_tolerance,
)
from app.services.scope import NAMESPACE_OFFICIAL, Scope  # noqa: E402

_DATASET = pathlib.Path(__file__).resolve().parent / "datasets" / "批改一致性" / "主观题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"

#: 计划 §6 的产品承诺：**各维度评分标准差 ≤ 0.5（5 分制）** → 百分制等价 **10.0**。
#: 报告里保留原始口径以写明换算过程；**实际判定走按题型分档的 `std_tolerance`**。
PROMISED_MAX_STD = 10.0
PROMISED_MAX_STD_5 = 0.5


def _metrics(rows: list[dict]) -> dict:
    """按 (条目 × 维度) 汇总，且**每条目用自己题型的阈值**判定。"""
    pairs = [
        (r["id"], r.get("qtype", ""), dim, std, r.get("tolerance", PROMISED_MAX_STD))
        for r in rows
        for dim, std in (r["per_dim_std"] or {}).items()
    ]
    ok = [p for p in pairs if p[3] <= p[4]]
    valid_rows = [r for r in rows if r["n"] > 0]
    # 余量 = 阈值 − 实测。它比"是否达标"更有用：写作贴着线（余量 0.91）就是靠它看出来的
    margins = [(p[4] - p[3]) for p in pairs]
    by_type: dict[str, dict] = {}
    for r in valid_rows:
        t = str(r.get("qtype") or "default")
        worst = max((r["per_dim_std"] or {}).values(), default=0.0)
        slot = by_type.setdefault(t, {"n": 0, "worst_std": 0.0, "ok": True, "min_agreement": 1.0})
        slot["n"] += 1
        slot["worst_std"] = round(max(slot["worst_std"], worst), 2)
        slot["ok"] = slot["ok"] and (worst <= r.get("tolerance", PROMISED_MAX_STD))
        slot["min_agreement"] = round(min(slot["min_agreement"], r["agreement"]), 4)
    return {
        "n_items": len(rows),
        "n_valid": len(valid_rows),
        "n_dim_pairs": len(pairs),
        "dim_std_ok_rate": round(len(ok) / len(pairs), 4) if pairs else 0.0,
        "max_dim_std": max((p[3] for p in pairs), default=0.0),
        "mean_dim_std": round(sum(p[3] for p in pairs) / len(pairs), 2) if pairs else 0.0,
        "min_margin": round(min(margins), 2) if margins else 0.0,
        "min_agreement": min((r["agreement"] for r in valid_rows), default=0.0),
        "max_total_std": max((r["total_std"] for r in valid_rows), default=0.0),
        "by_type": by_type,
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
        f"> **量纲**：承诺原文是「各维度标准差 ≤ {PROMISED_MAX_STD_5}（**5 分制**）」，",
        f"> 本实现是百分制 → 换算（{PROMISED_MAX_STD_5} ÷ 5 = 10%）后等价阈值 **{PROMISED_MAX_STD}**。",
        f"> **判定按题型分档**（`marking.std_tolerance`），且各档**一律不宽于** {PROMISED_MAX_STD} ——",
        "> 分档是让承诺变具体，不是给最差的一档开后门。",
        "",
        "| 指标 | 值 | 读法 |",
        "| --- | --- | --- |",
        f"| **dim_std_ok_rate** | **{m['dim_std_ok_rate']}** | 各 (题 × 维度) 落在该题型的阈值内 |",
        f"| max_dim_std | {m['max_dim_std']} | 最差的那一维；平均值会掩盖「有一维特别飘」 |",
        f"| **min_margin** | **{m['min_margin']}** | **余量**（阈值 − 实测）；越小越危险 |",
        f"| min_agreement | {m['min_agreement']} | 总分落在均值 ±{AGREEMENT_TOLERANCE} 分内的最低比例（承诺 ≥ {AGREEMENT_MIN}） |",
        f"| max_total_std | {m['max_total_std']} | 总分离散度上界 |",
        "",
        "## 按题型（**阈值分档**）",
        "",
        "| 题型 | 题数 | 阈值 | 实测最大维度标准差 | 维度达标 | 最低一致率 | 一致率达标 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t, slot in sorted((m["by_type"] or {}).items(), key=lambda kv: kv[1]["worst_std"], reverse=True):
        lines.append(
            f"| {t} | {slot['n']} | {std_tolerance(t)} | {slot['worst_std']} | "
            f"{'✅' if slot['ok'] else '❌'} | {slot['min_agreement']} | "
            f"{'✅' if slot['min_agreement'] >= AGREEMENT_MIN else '❌'} |"
        )
    lines += [
        "",
        "> 一致率那一列是**独立于维度标准差**的判据：写作在维度标准差上达标，但一致率只有 0.3333",
        "> （三次批改有两次偏离均值 5 分以上）—— **两列必须成对看**，只看一列会得出相反结论。",
        "",
        "## 逐题",
        "",
        "| 题 | 题型 | 阈值 | 有效次数 | 均分 | 总分标准差 | 一致率 | 各维度标准差 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        dims = "　".join(
            f"{DIMENSION_LABELS.get(d, d)} {v}" for d, v in (r["per_dim_std"] or {}).items()
        )
        lines.append(
            f"| {r['id']} | {r.get('qtype', '')} | {r.get('tolerance', '')} | {r['n']} | "
            f"{r['mean_total']} | {r['total_std']} | {r['agreement']} | {dims or '（无）'} |"
        )
    lines += [
        "",
        "## 边界（必须与数字一起引用）",
        "",
        f"- **样本是合成的、且只有 {m['n_items']} 题**：证明的是「链路能跑、指标算得出、承诺达不达成」，",
        "  **不是**「批改在普遍情况下稳」；分档阈值也只在这批样本上有实测依据；",
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
        qtype = str(it.get("qtype") or "")
        rubric = retrieve_rubric(db, scope, qtype, k=settings.marking_rubric_k)
        rep = measure_consistency(
            client, it["stem"], it["answer"], rubric, n=settings.marking_votes
        )
        print(
            f"    [{it['id']}] {qtype}（阈值 {std_tolerance(qtype)}）依据 {len(rubric)} 片 · "
            f"有效 {rep.n} 次 · 均分 {rep.mean_total} · 各维标准差 {rep.per_dim_std}",
            flush=True,
        )
        rows.append({"id": it["id"], "qtype": qtype, "tolerance": std_tolerance(qtype), **rep.as_dict()})

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
                "样本为**合成**且题数很少；LLM=fake 时本表只证明链路通、指标算得出，"
                "不是真实模型下的稳定性"
                if mode_llm == "fake"
                else "样本为**合成**且题数很少 —— 证明链路与指标，不足以代表普遍稳定性；"
                "四维度是产品自定口径，与官方评分一致性是两件事"
            ),
        },
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "marking_baseline.md"
    # ⚠️ **一次失败的运行不该抹掉证据**。接口全挂时 `n_valid == 0`，写出来的是一张
    # 「0 有效 · 全 0」的表 —— 而它在文件名上与真基准**毫无区别**。
    # 这条是真踩过的：口径对齐后重跑，主通道正好挂了，把上一轮取满 3 遍的真实基准
    # 覆盖成了全 0 表，靠 git 才恢复。
    # 与下面那条 fake 守卫同理：**基准表的槽位不能被无效产物占用**。
    if metrics["n_valid"] == 0:
        md = _OUT_DIR / "marking_failed.md"
        print("    ⚠️ 本次没有一次有效批改（接口故障？）→ 写到 marking_failed.md，不覆盖基准")
    # 与 g3_eval 同一条守卫：**不许用 fake 覆盖真实基准**（文件名就是产物的身份）
    elif mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = _OUT_DIR / "marking_fake.md"
        print("    已有真实基准，本次 fake 结果改写到 marking_fake.md（不覆盖真基准）")
    _write_markdown(md, result)
    (md.with_suffix(".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"    达标率 {metrics['dim_std_ok_rate']} | 最大维度标准差 {metrics['max_dim_std']} | "
        f"最小余量 {metrics['min_margin']} | 最低一致率 {metrics['min_agreement']}"
    )
    print(f"\n已写入：{md}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只用前 N 题（默认全部）")
    args = ap.parse_args()
    main(limit=args.limit)
