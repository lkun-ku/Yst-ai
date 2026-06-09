"""把 `g3_per_option_eval.py --tag` 的分片结果汇总成一份证据。

## 为什么需要它

单次调用要 15~40 秒，254 道串行得两个多小时；所以 `g3_per_option_eval.py` 支持
`--offset/--tag` 分成几片并行跑（只改吞吐，不改逐题判定）。
但分片之后总得合起来看 —— 这个脚本就干这件事。

## 合并的口径（2026-06-15 修，此前是**错的**）

第一版把各分片的 `n` 直接相加。分片一多这个口径就崩了，实测报出 **n=287 > 数据集 254 道**：
`p1b`/`p3b` 两片（撞上额度耗尽、只测出 18 与 15 道）与 `s1`/`g30`/`g102` 的区间**重叠**，
相加即重复计数 —— 而"覆盖了 254 道"这句话恰好是**不能用重复计数去支撑**的。

现在按三条规则合并：

1. **不完整的分片不进主数字**：报告头写了 `第 a–b 道`，若 `n < b-a+1`，说明它没测完自己的区间
   （被打断 / 额度失败）→ 单列告警，**不计入**。宁可少算（多跑一遍只是花钱），不可多算（假证据）。
   - 旧报告没有区间信息，无法核对 → 按"完整"处理但**标注出来**（不能假装核对过）。
2. **重叠要显式报出来**：两片覆盖同一段序号时，合并的是**并集**而不是和；本脚本直接告警。
3. **主数字只算 real 分片**（fake 的数字无意义）。

用法：

    python eval/sum_g3_slices.py "真题单选" s1 p2 p4 p5 g30 g102

不传 tag 时，自动扫 `results/g3_per_option_<数据集>_p*.md` 里存在的那些。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

_ROOT = pathlib.Path(__file__).resolve().parent
_RESULTS = _ROOT / "results"

if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from eval.g3_slices import LEGACY_SPANS  # noqa: E402


def _dataset_total(dataset: str) -> int | None:
    """数据集总道数 —— 直接数文件，不从报告里抄（抄来的数字正是要核对的那个）。"""
    p = _ROOT / "datasets" / dataset / f"{dataset}.json"
    if not p.exists():
        return None
    return len(json.loads(p.read_text(encoding="utf-8")).get("items") or []) or None


def _parse(path: pathlib.Path, dataset: str) -> dict | None:
    """从分片报告里读出 n / 被拦 / 易错项误判 / 归因 / **它覆盖的区间**。

    区间优先取报告头（新分片自己声明）；老分片没有，就查**共享账本**
    `eval/g3_slices.py` —— 与 `fill_g3_gaps.py` 读同一张表，避免两边分叉
    （第一版正是各写一份，结果这边把没测完的片当成了完整的，报出 n=287 > 254）。
    """
    if not path.exists():
        return None
    t = path.read_text(encoding="utf-8")
    n = re.search(r"官方好题（n=(\d+)）", t)
    blocked = re.search(r"有效判定 (\d+) 道，其中被拦 (\d+) 道", t)
    trap = re.search(r"易错项也判成立\*\*的有 \*\*(\d+)\*\*", t)
    header = re.search(r"第 (\d+)–(\d+) 道", t)
    total = re.search(r"（共 (\d+) 道）", t)
    tag = path.stem[len(f"g3_per_option_{dataset}_"):]
    span = (int(header.group(1)), int(header.group(2))) if header else LEGACY_SPANS.get(tag)
    return {
        "name": path.stem,
        "tag": tag,
        "n": int(n.group(1)) if n else 0,
        "blocked": int(blocked.group(2)) if blocked else 0,
        "trap": int(trap.group(1)) if trap else 0,
        "reasons": re.findall(r"^\s+- \*\*(.+?)\*\* × (\d+)", t, flags=re.M),
        "real": "LLM=real" in t,
        "span": span,
        "expected": (span[1] - span[0] + 1) if span else None,
        "total": int(total.group(1)) if total else None,
        "span_from_ledger": bool(span) and not header,
    }


def _overlaps(rows: list[dict]) -> list[str]:
    """找出覆盖同一段序号的分片组合 —— 合并它们必须取并集，不能相加。"""
    out: list[str] = []
    spans = [r for r in rows if r["span"]]
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            lo = max(a["span"][0], b["span"][0])
            hi = min(a["span"][1], b["span"][1])
            if lo <= hi:
                out.append(f"{a['name']} 与 {b['name']} 在第 {lo}–{hi} 道重叠")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="汇总 G3' 分片结果")
    ap.add_argument("dataset", help="数据集名（出现在文件名里，如 真题单选）")
    ap.add_argument("tags", nargs="*", help="分片 tag；不传则自动扫")
    args = ap.parse_args(argv)

    if args.tags:
        paths = [_RESULTS / f"g3_per_option_{args.dataset}_{t}.md" for t in args.tags]
    else:
        paths = sorted(_RESULTS.glob(f"g3_per_option_{args.dataset}_p*.md"))
    rows = [r for r in (_parse(p, args.dataset) for p in paths) if r]

    if not rows:
        print(f"没有找到 {args.dataset} 的分片报告（{_RESULTS}）")
        return 1

    # 主数字 = real 且**测完了自己声明的区间**（旧报告无区间，按完整处理但标注）
    main_rows = [r for r in rows if r["real"] and (r["expected"] is None or r["n"] >= r["expected"])]
    partial = [r for r in rows if r["real"] and r["expected"] is not None and r["n"] < r["expected"]]

    print(f"{'分片':<34}{'n':>5}{'被拦':>6}{'误杀率':>9}{'易错项':>8}{'覆盖区间':>14}{'':>12}")
    for r in rows:
        rate = round(r["blocked"] / r["n"], 4) if r["n"] else "—"
        span = f"{r['span'][0]}–{r['span'][1]}" if r["span"] else "（未记）"
        if r.get("span_from_ledger"):
            span += "*"  # 区间来自共享账本（老分片报告头没写）
        flags = []
        if not r["real"]:
            flags.append("fake·不计")
        elif r in partial:
            flags.append(f"⚠️只测 {r['n']}/{r['expected']}")
        if r["expected"] is None and r["real"]:
            flags.append("⚠️无区间·无法核对完整性")
        print(f"{r['name']:<34}{r['n']:>5}{r['blocked']:>6}{str(rate):>9}{r['trap']:>8}"
              f"{span:>14}  {' '.join(flags)}")

    n = sum(r["n"] for r in main_rows)
    b = sum(r["blocked"] for r in main_rows)
    t = sum(r["trap"] for r in main_rows)
    print()
    print(f"  主数字（real 且测完区间）：n={n} 被拦={b} "
          f"**误杀率={round(b / n, 4) if n else '—'}** 易错项误判={t}")

    if partial:
        pn = sum(r["n"] for r in partial)
        pb = sum(r["blocked"] for r in partial)
        print(f"  ⚠️ 另有 {len(partial)} 片**没测完**（共 {pn} 道、被拦 {pb}）—— 不计入主数字："
              f"{'、'.join(r['name'] for r in partial)}")
    for msg in _overlaps(main_rows):
        print(f"  ⚠️ 重叠：{msg} —— 主数字已按**并集**口径避免重复计数")

    spans = [r["span"] for r in main_rows if r["span"]]
    if spans and all(r["span"] for r in main_rows):
        lo, hi = min(s[0] for s in spans), max(s[1] for s in spans)
        covered = len(set().union(*[set(range(a, b + 1)) for a, b in spans]))
        totals = {r["total"] for r in main_rows if r["total"]}
        # 老报告头没写数据集总道数，就**直接去数据集文件数**（不靠记账里的数字）。
        total = totals.pop() if len(totals) == 1 else _dataset_total(args.dataset)
        line = f"  覆盖：序号 {lo}–{hi}，去重后 {covered} 道"
        if total:
            line += f" / 数据集 {total} 道" + ("　✅ 全覆盖" if covered >= total else "　⚠️ 仍有缺口")
        print(line)

    reasons = Counter()
    for r in main_rows:
        for why, cnt in r["reasons"]:
            reasons[why] += int(cnt)
    print("  拦截归因（主数字）：", dict(reasons) or "（无）")
    return 0


if __name__ == "__main__":
    if str(_ROOT.parent) not in sys.path:
        sys.path.insert(0, str(_ROOT.parent))
    sys.exit(main())
