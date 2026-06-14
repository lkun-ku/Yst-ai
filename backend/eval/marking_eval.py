"""主观题批改的**多次一致性**基准 —— 量「同一份作答反复批改，分数飘多少」。

## 为什么这个数字此前根本不存在

官方题库里**一道主观题都没有**（题型只有 单选/多选/判断/填空/简答）。也就是说
`marking.py` 的批改链路**只在测试夹具上跑过**，从未在像主观题的输入上跑过，
更没有真实模型下的稳定性数字 —— 而「AI 批改的分数稳不稳」恰恰是用户最直接的疑问，
也是主观题批改这个亮点最容易被一句话问倒的地方（「同一篇作文你打两次分吗？」）。

## 判据与量纲

计划 §6 的承诺原文是「**各维度评分标准差 ≤ 0.5（5 分制）**」，而本实现的维度是
**百分制** —— 按比例换算（0.5 ÷ 5 = 10%）后，百分制下的等价阈值是 **10.0**。
这是**把量纲写对，不是把承诺放宽**：相对严格度不变（第一次跑没换算时"全部超标"，
实测就是这么发现的）。

## 阈值**按题型分档**，且档位要由**分布**定而不是单点

单一阈值 10.0 会掩盖两件事：在辨析上过度苛刻、在写作上把"贴着线"误读成"稳"。
实测出明显的**题型梯度**（简答/辨析 很稳，写作最飘），所以判定改用
`marking.std_tolerance(qtype)` —— 且各档**一律不宽于**现行承诺。

⚠️ 但**第一版分档值来自单题单轮**（辨析只测过 1 道、1.89），下一轮复跑就出现 4.55 ——
于是"辨析未达标"到底是真实波动还是阈值定得过紧，**当时无法区分**。
本脚本因此加了 `--repeat`：把同一批题跑 R 轮，按题型汇总**分布**（样本数 / 最大 / 均值 /
p90），再由 `recommend_threshold()` 按**固定规则**给出建议档位。

## 规则（写进函数，免得将来数字来源说不清）

    建议阈值 = min(该题型实测最大标准差 + 1 分余量, 10.0)

- **一律不宽于 10.0**：若实测最大已经超过 10.0，说明该题型**达不成承诺** ——
  那就如实标记未达标，**不许上调阈值**把它变成达标（上调就是自欺）；
- 余量固定 1 分：与首版规则一致，便于对照；
- 样本很薄时（如某题型只有 1 题）分布只代表那几道题，报告里必须与数字一起读。

| 指标 | 读法 |
| --- | --- |
| `dim_std_ok_rate` | 各 (题 × 维度 × 轮) 落在**该题型阈值**内的比例 |
| `max_dim_std` / `min_margin` | 最差的一维 / **余量**（阈值 − 实测）；后者比"是否达标"更有用 |
| `agreement` | 各次总分落在均值 ±`AGREEMENT_TOLERANCE`(5 分) 内的比例；承诺 ≥ `AGREEMENT_MIN`(0.80) |
| `n_valid / n_items` | 有效批改；为 0 说明接口抖动或依据检索失败，**本表不可信** |

## 三处必须写清的边界

1. **样本是合成的**（`datasets/批改一致性/主观题样本.json`）：真题原文不入库是明确决策
   （ADR-0003）。一致性度量要的是**输入的稳定性**，与题目出自哪里无关；但题目数量很少，
   所以它证明的是「链路能跑、指标算得出、承诺达不达成」，**不是**「批改普遍很稳」。
2. **`ingest_official_corpus(embed=False)`**：与 `teacher_eval.py` 同口径 ——
   依据（rubric）召回走**关键词通道**；真实 embedding 下的召回质量不在本表范围。
3. **本表量的是重复性，不是准确性**：「同一输入两次分差多大」与「分数是否接近官方评分」
   是两件事，后者需要人工标分，不在本表能力范围内。

跑法：

    python eval/marking_eval.py                       # 默认 fake（不花钱）
    python eval/marking_eval.py --repeat 3            # 跑 3 轮，给按题型的分布
    $env:LLM_MODE='real'; python eval/marking_eval.py --repeat 3
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
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
from eval import resumable  # noqa: E402

_DATASET = pathlib.Path(__file__).resolve().parent / "datasets" / "批改一致性" / "主观题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"
_RESULTS = _OUT_DIR


def _row_key(rec: dict) -> str:
    """续跑的键：同一题在**多轮**实验里要跑多次，所以键必须带上轮次。

    只用题目 id 会让"第 2 轮"被当成"已经跑过"而直接跳过 —— 那一轮就永远跑不到。
    """
    return f"{rec.get('id')}#{rec.get('repeat', 1)}"

#: 计划 §6 的产品承诺：**各维度评分标准差 ≤ 0.5（5 分制）** → 百分制等价 **10.0**。
PROMISED_MAX_STD = 10.0
PROMISED_MAX_STD_5 = 0.5
#: 建议阈值的余量（与首版分档规则一致，便于对照）
RECOMMEND_MARGIN = 1.0


def recommend_threshold(max_observed: float, promise: float = PROMISED_MAX_STD) -> float:
    """按固定规则给出建议阈值：`min(实测最大 + 1, 承诺)`。

    **上调到承诺之上是不允许的** —— 若实测最大已超过承诺，说明该题型达不成承诺，
    应当如实标记未达标，而不是把阈值抬高让它"达标"。这条规则写成函数就是为了
    让"数字是哪来的、有没有被动过"随时可查。
    """
    return round(min(max_observed + RECOMMEND_MARGIN, promise), 2)


def aggregate_by_type(rows: list[dict]) -> dict:
    """按题型汇总**全部** (题 × 维度 × 轮) 的标准差样本 → 分布与建议阈值。

    为什么必须按"样本"而不是按"题"汇总：分档值当初就栽在这里 ——
    单题单轮得出的 1.89 被当成了辨析的真实水平，下一轮就翻到 4.55。
    """
    buckets: dict[str, list[float]] = {}
    agreements: dict[str, list[float]] = {}
    for r in rows:
        t = str(r.get("qtype") or "default")
        buckets.setdefault(t, []).extend(float(v) for v in (r["per_dim_std"] or {}).values())
        if r.get("n"):
            agreements.setdefault(t, []).append(float(r["agreement"]))

    out: dict[str, dict] = {}
    for t, vals in buckets.items():
        if not vals:
            continue
        srt = sorted(vals)
        p90 = srt[min(len(srt) - 1, int(round(0.9 * (len(srt) - 1))))]
        mx = srt[-1]
        out[t] = {
            "n_samples": len(vals),
            "max_std": round(mx, 2),
            "mean_std": round(statistics.fmean(vals), 2),
            "p90_std": round(p90, 2),
            "current_tolerance": std_tolerance(t),
            "recommended": recommend_threshold(mx),
            # 实测已超承诺 → 该题型**达不成承诺**，不许靠上调阈值掩盖
            "meets_promise": mx <= PROMISED_MAX_STD,
            "min_agreement": round(min(agreements.get(t, [0.0])), 4),
        }
    return dict(sorted(out.items(), key=lambda kv: kv[1]["max_std"], reverse=True))


def _metrics(rows: list[dict], by_type: dict) -> dict:
    """按 (条目 × 维度 × 轮) 汇总，且**每条目用自己题型的当前阈值**判定。"""
    pairs = [
        (r["id"], r.get("qtype", ""), dim, std, r.get("tolerance", PROMISED_MAX_STD))
        for r in rows
        for dim, std in (r["per_dim_std"] or {}).items()
    ]
    ok = [p for p in pairs if p[3] <= p[4]]
    valid_rows = [r for r in rows if r["n"] > 0]
    margins = [(p[4] - p[3]) for p in pairs]
    return {
        "n_rows": len(rows),
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
        f"- 生成时间：{meta['time']}　题数 {m['n_rows']}（有效 {m['n_valid']}）"
        f"　每次批改 {meta['votes']} 遍　重复轮数 {meta['repeat']}",
        f"- 模型：LLM={meta['llm']}　依据检索：{meta['rubric']}",
        f"- 数据：`eval/datasets/批改一致性/主观题样本.json`（**合成**样本，非真题原文）",
        "",
        f"> **量纲**：承诺原文是「各维度标准差 ≤ {PROMISED_MAX_STD_5}（**5 分制**）」，",
        f"> 本实现是百分制 → 换算后等价阈值 **{PROMISED_MAX_STD}**。判定按题型分档"
        "（`marking.std_tolerance`），各档**一律不宽于**该值。",
        "",
        "| 指标 | 值 | 读法 |",
        "| --- | --- | --- |",
        f"| **dim_std_ok_rate** | **{m['dim_std_ok_rate']}** | 各 (题 × 维度 × 轮) 落在该题型阈值内 |",
        f"| max_dim_std | {m['max_dim_std']} | 最差的那一维；平均值会掩盖「有一维特别飘」 |",
        f"| **min_margin** | **{m['min_margin']}** | **余量**（阈值 − 实测）；越小越危险 |",
        f"| min_agreement | {m['min_agreement']} | 总分落在均值 ±{AGREEMENT_TOLERANCE} 分内的最低比例（承诺 ≥ {AGREEMENT_MIN}） |",
        f"| max_total_std | {m['max_total_std']} | 总分离散度上界 |",
        "",
        "## 按题型：分布与建议档位（**这才是定阈值的依据**）",
        "",
        "| 题型 | 样本数 | 均值 | p90 | **最大** | 当前阈值 | **建议阈值** | 达承诺 | 最低一致率 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t, s in (m["by_type"] or {}).items():
        lines.append(
            f"| {t} | {s['n_samples']} | {s['mean_std']} | {s['p90_std']} | **{s['max_std']}** | "
            f"{s['current_tolerance']} | **{s['recommended']}** | "
            f"{'✅' if s['meets_promise'] else '❌ **达不成**'} | {s['min_agreement']} |"
        )
    lines += [
        "",
        f"> 建议阈值规则：`min(实测最大 + {RECOMMEND_MARGIN}, {PROMISED_MAX_STD})` ——",
        "> **不许上调到承诺之上**。若实测最大已超承诺，就该如实标「达不成」，",
        "> 而不是抬高阈值让它看起来达标。样本很薄时（某题型只有 1 题）分布只代表那几道题。",
        "",
        "> ⚠️ **一致率是独立判据**：写作在维度标准差上可能达标，但一致率若低于 0.80，",
        "> **两列必须成对看** —— 只看一列会得出相反结论。",
        "",
        "## 逐行（题 × 轮）",
        "",
        "| 题 | 题型 | 轮 | 阈值 | 有效 | 均分 | 总分标准差 | 一致率 | 各维度标准差 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        dims = "　".join(
            f"{DIMENSION_LABELS.get(d, d)} {v}" for d, v in (r["per_dim_std"] or {}).items()
        )
        lines.append(
            f"| {r['id']} | {r.get('qtype', '')} | {r.get('repeat', 1)} | {r.get('tolerance', '')} | "
            f"{r['n']} | {r['mean_total']} | {r['total_std']} | {r['agreement']} | {dims or '（无）'} |"
        )
    lines += [
        "",
        "## 边界（必须与数字一起引用）",
        "",
        f"- **样本是合成的、且只有 {len({r['id'] for r in rows})} 道题**：证明的是",
        "  「链路能跑、指标算得出、承诺达不达成」，**不是**「批改在普遍情况下稳」；",
        "- 依据检索用 `embed=False`（关键词通道，与 `teacher_eval.py` 同口径）；",
        "- **本表量的是重复性，不是准确性** —— 「同一输入两次分差多大」与「分数是否接近官方评分」",
        "  是两件事，后者需要人工标分。",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(limit: int = 0, repeat: int = 1, tag: str = "run") -> dict:
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
    print(
        f"题数 {len(items)}　轮数 {repeat}　LLM={mode_llm}　每次批改 {settings.marking_votes} 遍",
        flush=True,
    )

    rows: list[dict] = []

    # ---- 逐题落盘 + 断点续跑（与 G3 同一套约定，见 `eval/resumable.py`）----
    # 批改一致性是 **3 倍开销**（同一作答独立批改 N 遍），中断一次等于白花一遍额度；
    # 落盘后重跑只补缺口。key = `题目 id + 第几轮`，因为多轮实验里同一题要跑多次。
    jsonl = resumable.result_path(_RESULTS, f"marking_consistency_{_DATASET.stem}", tag)
    done = resumable.load_done(jsonl, key_of=_row_key)
    if done:
        print(f"  {resumable.summarise(done, [])}（{jsonl.name}）", flush=True)

    for rnd in range(1, max(1, repeat) + 1):
        if repeat > 1:
            print(f"  --- 第 {rnd}/{repeat} 轮 ---", flush=True)
        for it in items:
            key = _row_key({"id": it["id"], "repeat": rnd})
            if key in done:
                rows.append(done[key])          # 已跑过：直接复用，不再花调用
                continue
            qtype = str(it.get("qtype") or "")
            rubric = retrieve_rubric(db, scope := Scope(namespace=NAMESPACE_OFFICIAL), qtype,
                                     k=settings.marking_rubric_k)
            rep = measure_consistency(
                client, it["stem"], it["answer"], rubric, n=settings.marking_votes
            )
            print(
                f"    [{rnd}-{it['id']}] {qtype}（阈值 {std_tolerance(qtype)}）依据 {len(rubric)} 片 · "
                f"有效 {rep.n} 次 · 均分 {rep.mean_total} · 各维标准差 {rep.per_dim_std}",
                flush=True,
            )
            rec = {
                "id": it["id"], "qtype": qtype, "repeat": rnd,
                "tolerance": std_tolerance(qtype), **rep.as_dict(),
            }
            rows.append(rec)
            resumable.append_record(jsonl, rec)  # **先落盘再继续**：中断也不丢这一条

    by_type = aggregate_by_type(rows)
    metrics = _metrics(rows, by_type)
    result = {
        "metrics": metrics,
        "items": rows,
        "metadata": {
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm": mode_llm,
            "votes": settings.marking_votes,
            "repeat": max(1, repeat),
            "rubric": "official 语料 · embed=False（关键词通道）",
            "dataset": _DATASET.name,
            "caveat": (
                "样本为**合成**且题数很少；LLM=fake 时本表只证明链路通、指标算得出，"
                "不是真实模型下的稳定性"
                if mode_llm == "fake"
                else "样本为**合成**且题数很少 —— 证明链路与指标，不足以代表普遍稳定性；"
                "本表量重复性而非准确性（后者需人工标分）"
            ),
        },
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "marking_baseline.md"
    # ⚠️ **一次失败的运行不该抹掉证据**。接口全挂时 `n_valid == 0`，写出来的是一张
    # 「0 有效 · 全 0」的表 —— 而它在文件名上与真基准**毫无区别**。
    # 这条是真踩过的：口径对齐后重跑，主通道正好挂了，把上一轮取满 3 遍的真实基准
    # 覆盖成了全 0 表，靠 git 才恢复。
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
    for t, s in (by_type or {}).items():
        flag = "达承诺" if s["meets_promise"] else "**达不成承诺**"
        print(f"    {t}: 最大 {s['max_std']} 均值 {s['mean_std']} p90 {s['p90_std']} "
              f"→ 建议阈值 {s['recommended']}（当前 {s['current_tolerance']}）{flag}")
    print(f"\n已写入：{md}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只用前 N 题（默认全部）")
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="重复轮数（默认 1）。按题型定阈值需要分布而非单点 —— 至少 3 轮",
    )
    ap.add_argument(
        "--tag",
        default="run",
        help="本次运行的标签（决定逐题 jsonl 文件名）。**换一个 tag = 从零跑**；"
        "同一个 tag 重跑 = 只补没跑过的题（断点续跑）",
    )
    args = ap.parse_args()
    main(limit=args.limit, repeat=args.repeat, tag=args.tag)
