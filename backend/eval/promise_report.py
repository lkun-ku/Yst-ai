"""§6.3 承诺表的「实测」列 —— **一条命令产出**，不再靠人手工对齐。

## 为什么需要它

对齐审计（2026-06-15）发现三个问题，根因都是同一个：**承诺写的是一个口径、
证据文件里是另一个口径，而数字散在十几个文件里** —— 于是"某条承诺到底达标没有"
只能靠人翻。本轮修掉的三处（K 口径 / faithfulness 量纲 / 通过率分母）全是这么潜伏下来的。

## 两类读数必须分开标注

| 来源 | 例子 | 可信度 |
| --- | --- | --- |
| **硬机制** | 引用子串校验（零误判）、闸门判定、`max(k) < 语料规模` | 高，可复现 |
| **LLM judge** | faithfulness（`factuality` 打分）、评分一致性 | 低一档：它也是"模型说的" |

混在一张表里而不标来源，读数的人会把两者当同等硬 —— 所以本表有一列专门写来源。

## 两类数据，来源字段写清楚

- **读既有证据**（`backend/eval/results/*.json`）：那些测量需要 embedding / 大量 LLM 调用，
  重复跑代价高。表里会带上**证据文件与生成时间**，便于判断是否过期。
- **现场跑**（`--live-gate`）：唯一性通过率可以在当前 LLM 模式下当场算 —— fake 免费、
  real 花钱，所以默认只在 fake 下跑。

用法：

    python eval/promise_report.py                 # 读证据 + （fake 时）现场跑闸门
    python eval/promise_report.py --live-gate     # 显式现场跑闸门
    LLM_MODE=real python eval/promise_report.py   # 真实模式（注意调用成本与额度）
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass

_ROBOT = pathlib.Path(__file__).resolve()
_ROOT = _ROBOT.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("LLM_MODE", "fake")

_RESULTS = _ROBOT.parent / "results"

#: 各条证据文件（缺文件时该行标"待跑"，而不是编一个数字）
EVIDENCE = {
    "retrieval_law": _RESULTS / "retrieval_baseline_官方法条.json",
    "retrieval_edu": _RESULTS / "retrieval_baseline_教资.json",
    "teacher": _RESULTS / "teacher_baseline.json",
    "marking": _RESULTS / "marking_baseline.json",
    "g3_real": _RESULTS / "g3_真题单选_baseline.json",
    "faithfulness": _RESULTS / "faithfulness.json",
}

_OK, _BAD, _WARN, _NONE = "✅", "❌", "⚠️", "—"


@dataclass(frozen=True)
class Row:
    """一行实测。`source` 必须写明**是谁算出来的**（硬机制 / LLM judge / 哪份证据）。"""

    name: str
    target: str
    measured: str
    source: str
    verdict: str
    note: str = ""


def _load(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 证据坏了要如实说，不能静默当成"没跑过"
        return {"_broken": True}


def _stamp(data: dict | None, path: pathlib.Path) -> str:
    """证据出处 + 生成时间（过期与否由读的人判断，脚本不替他下结论）。"""
    if data is None:
        return "（无证据文件，待跑）"
    if data.get("_broken"):
        return f"⚠️ `{path.name}` 解析失败"
    when = (data.get("metadata") or {}).get("time") or ""
    return f"`{path.name}`" + (f"（{when}）" if when else "")


# ---------------- 检索：Recall@K / MRR ----------------


def _production_row(data: dict) -> tuple[str, dict] | None:
    """挑出**生产档**那一行（标签里带 `★生产`）；没有就退回最后一档。"""
    rows = (data or {}).get("rows") or {}
    for name, row in rows.items():
        if "★生产" in name:
            return name, row
    return (next(reversed(rows.items())) if rows else None)


def from_retrieval(path: pathlib.Path) -> list[Row]:
    data = _load(path)
    if not data or data.get("_broken"):
        return [Row("Recall@K / MRR", "≥0.80 @5 / ≥0.60", "（待跑）", _stamp(data, path), _NONE)]

    meta = data.get("metadata") or {}
    picks = _production_row(data)
    if picks is None:
        return [Row("Recall@K / MRR", "≥0.80 @5 / ≥0.60", "（证据里没有档位）", _stamp(data, path), _NONE)]
    name, row = picks
    ks = meta.get("ks") or []
    recall_txt = " ".join(f"@{k}={row.get(f'recall@{k}')}" for k in ks if f"recall@{k}" in row)
    has5 = "recall@5" in row

    recall_verdict = _WARN if not has5 else (_OK if float(row.get("recall@5") or 0) >= 0.80 else _BAD)
    recall_note = (
        "承诺写 K=1/5/10，这份证据只有 "
        + "/".join(str(k) for k in ks)
        + " —— 需按 `pick_ks` 重跑才有 @5（要 embedding 额度）"
        if not has5
        else ""
    )
    mrr = float(row.get("mrr") or 0)
    # 精排档单独看：达标与否**取决于精排开不开**
    rerank_mrr = None
    for rname, rrow in ((data.get("rows") or {}).items()):
        if "精排" in rname and "含标题" in rname:
            rerank_mrr = float(rrow.get("mrr") or 0)
    note = f"生产档「{name}」"
    if rerank_mrr is not None:
        note += f"；加精排 {rerank_mrr}（{'达标' if rerank_mrr >= 0.60 else '未达标'}）——**精排默认关闭**"

    return [
        Row("Recall@K", "≥ 0.80 @5", f"{recall_txt}（n={meta.get('n_queries')} 查询 / {meta.get('n_chunks')} 片）",
            _stamp(data, path) + "｜硬机制", recall_verdict, recall_note),
        Row("MRR", "≥ 0.60", f"{mrr}", _stamp(data, path) + "｜硬机制",
            _OK if mrr >= 0.60 else _BAD, note),
    ]


# ---------------- 问答：引用首次通过率 / 拒答率 / 成本 ----------------


def from_teacher(path: pathlib.Path) -> list[Row]:
    data = _load(path)
    if not data or data.get("_broken"):
        return [
            Row("引用首次通过率", "≥ 0.90", "（待跑）", _stamp(data, path), _NONE),
            Row("拒答率", "10% ~ 25%", "（待跑）", _stamp(data, path), _NONE),
        ]
    rows = data.get("rows") or {}
    ground = rows.get("grounded") or rows.get("agent") or {}
    n_ans = int(ground.get("n_answerable") or 0)
    n_un = int(ground.get("n_unanswerable") or 0)
    total = n_ans + n_un
    refused = total - int(ground.get("answered") or 0)
    rate = round(refused / total, 4) if total else 0.0

    cite = float(ground.get("cite_first_pass_rate") or 0)
    avgs = [float((rows.get(k) or {}).get("avg_tool_calls") or 0) for k in ("grounded", "agent")]
    avgs = [a for a in avgs if a]

    return [
        Row("引用首次通过率", "≥ 0.90", f"{cite}", _stamp(data, path) + "｜硬机制（子串校验）",
            _OK if cite >= 0.90 else _BAD, f"可答样本 n={n_ans}（小样本，证据自带 caveat）"),
        Row("拒答率", "10% ~ 25%", f"{rate}（{refused}/{total}）", _stamp(data, path) + "｜硬机制",
            _OK if 0.10 <= rate <= 0.25 else _BAD,
            f"不可答集 n={n_un} 是**人工设计**的，不等于真实分布"),
        Row("成本（工具调用）", "平均 ≤ 2，P95 ≤ 4",
            "平均 " + " / ".join(str(a) for a in avgs) if avgs else "（无）",
            _stamp(data, path) + "｜硬机制",
            _WARN, "**P95 没有任何记录** —— 均值达标不代表尾部安全"),
    ]


# ---------------- 批改：评分一致性 ----------------


def from_marking(path: pathlib.Path) -> Row:
    data = _load(path)
    if not data or data.get("_broken"):
        return Row("评分一致性", "一致率 ≥ 0.80；维度标准差 ≤ 10", "（待跑）", _stamp(data, path), _NONE)
    m = data.get("metrics") or {}
    agree = float(m.get("min_agreement") or 0)
    std = float(m.get("max_dim_std") or 0)
    ok = agree >= 0.80 and std <= 10
    worst = ""
    for qt, info in (m.get("by_type") or {}).items():
        if not info.get("meets_promise", True):
            worst += f"{qt}({info.get('max_std')}) "
    return Row(
        "评分一致性", "一致率 ≥ 0.80；维度标准差 ≤ 10",
        f"一致率 {agree}；最大维度标准差 {std}（n={m.get('n_rows')} 行）",
        _stamp(data, path) + "｜LLM judge（同输入多次调用）",
        _OK if ok else _BAD,
        f"未达标题型：{worst.strip() or '（无）'} —— 两个口径同时不过才判未达标",
    )


# ---------------- 唯一性通过率（可现场跑） ----------------


def from_gate_live(payloads: list[dict], client, mode: str) -> Row:
    from app.config import settings
    from app.services.quality_gates import apply_uniqueness_gate
    from eval.metrics import evaluate_gate_pass

    before = settings.gate_g3_enabled
    settings.gate_g3_enabled = True  # 闸门默认关闭，现场跑必须显式打开
    try:
        _kept, m = evaluate_gate_pass(payloads, lambda items: apply_uniqueness_gate(client, items))
    finally:
        settings.gate_g3_enabled = before

    note = (
        "分母 = **待过闸门的全部题**（与误杀率的分母「好题」不同）"
        if m.measurable
        else "⚠️ 分母为 0 → **不可测**，不是「全军覆没」"
    )
    if mode == "fake":
        note += "；fake 模式下数字**无参考价值**，此行只证明口径接好了"
    return Row(
        "唯一性通过率", "≥ 0.90",
        f"{m.pass_rate}（{m.n_passed}/{m.n_total}）",
        "**本次运行**｜硬机制（G3 闸门）",
        _NONE if mode == "fake" else (_OK if m.pass_rate >= 0.90 else _BAD),
        note,
    )


def from_faithfulness(path: pathlib.Path) -> Row:
    """事实性 faithfulness —— 读 `faithfulness.json`（由 `eval/faithfulness_eval.py` 产出）。

    口径：judge 的 `factuality` ≥ 4.0 的样本占比（`metrics.faithfulness_rate`）——
    这是**比例**口径，与 1–5 分的**均值**不是一回事（拿均值去比 0.90 是量纲错配，
    对齐审计里发现过这一类问题）。

    ⚠️ 来源是 **LLM judge**，比引用子串校验低一档 —— 报告里必须标出来，
    否则一个 0.92 会被读成与「编造率 0」同等硬。
    """
    data = _load(path)
    if not data or data.get("_broken"):
        return Row(
            "事实性 faithfulness", "≥ 0.90", "（待跑）",
            "口径已就位：`metrics.faithfulness_rate`｜LLM judge", _NONE,
            "产出它请跑 `python eval/faithfulness_eval.py`（需 LLM 额度）；"
            "它与引用校验**不是一回事**（后者才是零误判硬机制）",
        )
    m = data.get("metrics") or {}
    meta = data.get("metadata") or {}
    rate = float(m.get("faithfulness_rate") or 0)
    return Row(
        "事实性 faithfulness", "≥ 0.90",
        f"{rate}（{m.get('n')} 条判分；拒答 {meta.get('n_refused', 0)} 条不计入）",
        _stamp(data, path) + "｜LLM judge（factuality ≥ 4.0）",
        _OK if rate >= 0.90 else _BAD,
        str(meta.get("caveat") or ""),
    )


# ---------------- 渲染 ----------------


def render(rows: list[Row], *, mode: str) -> str:
    lines = [
        "# §6.3 承诺 · 实测对照（自动生成）",
        "",
        f"- 生成时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}",
        f"- LLM 模式：`{mode}`　Embedding：`{os.getenv('EMBEDDING_MODE', '默认')}`",
        "- **来源列必须读**：`硬机制` 可复现、零误判；`LLM judge` 是「模型说的」，低一档。",
        "",
        "| 指标 | 目标 | 实测 | 来源 | 判定 | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.name} | {r.target} | {r.measured} | {r.source} | {r.verdict} | {r.note} |"
        )
    lines += [
        "",
        "> 本表由 `eval/promise_report.py` 生成：能读既有证据的读证据（带文件名与生成时间），",
        "> 能当场算的当场算。**「待跑」与「未达标」是两件事** —— 前者是没测，后者是测了不行。",
    ]
    return "\n".join(lines) + "\n"


def gather(live_gate: bool, gate_n: int) -> tuple[list[Row], str]:
    from app.services.llm_client import get_llm_client

    client = get_llm_client()
    mode = "fake" if type(client).__name__ == "FakeLLMClient" else "real"

    rows: list[Row] = []
    rows += from_retrieval(EVIDENCE["retrieval_law"])
    rows += from_retrieval(EVIDENCE["retrieval_edu"])
    rows += from_teacher(EVIDENCE["teacher"])
    rows.append(from_marking(EVIDENCE["marking"]))
    rows.append(from_faithfulness(EVIDENCE["faithfulness"]))
    if live_gate or mode == "fake":
        rows.append(from_gate_live(_gate_sample(gate_n), client, mode))
    else:
        rows.append(
            Row("唯一性通过率", "≥ 0.90", "（未现场跑）", "—", _NONE,
                "real 模式下现场跑要花 N 倍调用 → 需显式 `--live-gate`")
        )
    return rows, mode


def _gate_sample(n: int) -> list[dict]:
    """从真题集取几条作为过闸门的样本（**不读 dev.db**，保证可复现）。"""
    path = _ROBOT.parent / "datasets" / "真题单选" / "真题单选.json"
    data = _load(path) or {}
    items = (data.get("items") or [])[:n]
    return [
        {"stem": it.get("stem"), "type": it.get("type") or "single",
         "options": it.get("options"), "answer": it.get("answer")}
        for it in items
    ]


def output_path(out: pathlib.Path, mode: str) -> tuple[pathlib.Path, str]:
    """fake 模式**不许覆盖真基准** —— 返回 `(落盘路径, 提示语)`。

    ## 为什么要有这条（2026-06-16 实测踩到）

    在 fake 下跑一次（本意是刷新"评分一致性"那一格），结果 `唯一性通过率` 那格里
    **真实的 `0.9（18/20）` 被 fake 的 `0.0（0/8）` 顶掉了** —— 而表格里那行的来源列
    仍写着「**本次运行**｜硬机制（G3 闸门）」，看起来就是一次真实测量。

    这与 `marking_eval` 的处置**必须是同一套**（那边早有："已有真实基准，本次 fake
    结果改写到 `marking_fake.md`，不覆盖真基准"）。两个脚本同类问题两种行为，
    正是本项目反复吃过的亏：**同一件事实在两处各有一份，就一定会有一处是错的**。

    `--out` 是用户显式指定的路径时**不改名** —— 那是他自己的选择，规则不该劫持它。
    """
    if mode == "fake" and out.name == "promise_report.md":
        return (
            out.with_name("promise_report_fake.md"),
            "⚠️ fake 模式：改写到 promise_report_fake.md（不覆盖真基准）",
        )
    return out, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="产出 §6.3 承诺表的实测列")
    ap.add_argument("--live-gate", action="store_true", help="real 模式下也现场跑唯一性通过率")
    ap.add_argument("--gate-n", type=int, default=8, help="现场跑闸门用几道题（默认 8）")
    ap.add_argument("--out", default=str(_RESULTS / "promise_report.md"))
    args = ap.parse_args(argv)

    rows, mode = gather(args.live_gate, args.gate_n)
    md = render(rows, mode=mode)
    out, warn = output_path(pathlib.Path(args.out), mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    if warn:
        print(warn)
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
