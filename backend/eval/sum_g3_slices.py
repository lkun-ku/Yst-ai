"""把 `g3_per_option_eval.py --tag` 的分片结果汇总成一份证据。

## 为什么需要它

单次调用要 15~40 秒，254 道串行得两个多小时；所以 `g3_per_option_eval.py` 支持
`--offset/--tag` 分成几片并行跑（只改吞吐，不改逐题判定）。
但分片之后总得合起来看 —— 这个脚本就干这件事，并且**如实区分**：

- **干净分片**：正常跑完的；
- **被污染分片**：中途撞上额度/超时导致的失败（`n` 明显偏小）—— 混进主数字会失真。

用法：

    python eval/sum_g3_slices.py "真题单选" p1 p2 p3

不传 tag 时，自动扫 `results/g3_per_option_<数据集>_p*.md` 里存在的那些。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import Counter

_ROOT = pathlib.Path(__file__).resolve().parent
_RESULTS = _ROOT / "results"


def _parse(path: pathlib.Path) -> dict | None:
    """从分片报告里读出 n / 被拦 / 易错项误判 / 归因。"""
    if not path.exists():
        return None
    t = path.read_text(encoding="utf-8")
    n = re.search(r"官方好题（n=(\d+)）", t)
    blocked = re.search(r"有效判定 (\d+) 道，其中被拦 (\d+) 道", t)
    trap = re.search(r"易错项也判成立\*\*的有 \*\*(\d+)\*\*", t)
    return {
        "name": path.stem,
        "n": int(n.group(1)) if n else 0,
        "blocked": int(blocked.group(2)) if blocked else 0,
        "trap": int(trap.group(1)) if trap else 0,
        "reasons": re.findall(r"^\s+- \*\*(.+?)\*\* × (\d+)", t, flags=re.M),
        "real": "LLM=real" in t,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="汇总 G3' 分片结果")
    ap.add_argument("dataset", help="数据集名（出现在文件名里，如 真题单选）")
    ap.add_argument("tags", nargs="*", help="分片 tag；不传则自动扫")
    args = ap.parse_args(argv)

    if args.tags:
        paths = [_RESULTS / f"g3_per_option_{args.dataset}_{t}.md" for t in args.tags]
    else:
        paths = sorted(_RESULTS.glob(f"g3_per_option_{args.dataset}_p*.md"))
    rows = [r for r in (_parse(p) for p in paths) if r]

    if not rows:
        print(f"没有找到 {args.dataset} 的分片报告（{_RESULTS}）")
        return 1

    print(f"{'分片':<34}{'n':>5}{'被拦':>6}{'误杀率':>9}{'易错项误判':>10}  模式")
    clean_n = clean_b = clean_t = 0
    all_n = all_b = all_t = 0
    for r in rows:
        rate = round(r["blocked"] / r["n"], 4) if r["n"] else "—"
        print(f"{r['name']:<34}{r['n']:>5}{r['blocked']:>6}{str(rate):>9}{r['trap']:>10}"
              f"  {'real' if r['real'] else 'fake'}")
        all_n += r["n"]; all_b += r["blocked"]; all_t += r["trap"]
        if r["real"]:
            clean_n += r["n"]; clean_b += r["blocked"]; clean_t += r["trap"]

    def line(label: str, n: int, b: int, t: int) -> str:
        return f"  {label}：n={n} 被拦={b} **误杀率={round(b / n, 4) if n else '—'}** 易错项误判={t}"

    print()
    print(line("仅 real 分片", clean_n, clean_b, clean_t))
    if (clean_n, clean_b) != (all_n, all_b):
        print(line("全部分片", all_n, all_b, all_t))
        print("  ⚠️ 含非 real 分片 —— 上面的主数字只算 real 的那些")
    reasons = Counter()
    for r in rows:
        if r["real"]:
            for why, cnt in r["reasons"]:
                reasons[why] += int(cnt)
    print("  拦截归因（仅 real）：", dict(reasons) or "（无）")
    return 0


if __name__ == "__main__":
    if str(_ROOT.parent) not in sys.path:
        sys.path.insert(0, str(_ROOT.parent))
    sys.exit(main())
