"""算清 G3' 安全性评测**还缺哪些题**，把缺口补跑掉，并自动汇总。

## 为什么要有它（2026-06-15）

`g3_per_option_eval.py` 支持 `--offset/--limit/--tag` 分片，但**分片跑完就散着**：

1. **缺口的答案只能靠人记**。分片报告原先**不写自己覆盖的是哪一段**，"合并起来是否覆盖了整份数据集"
   无法从产物看出来 —— 我上一轮就是靠**记忆里的区间**（p1 是 0–50、p2 是 51–101…）去核缺口的，
   而记忆是会被区间记错、被重跑覆盖掉的。
2. **补跑要手敲多条长命令**，且要自己算 offset。多行 shell 在本机环境里反复出问题
   （且 `--offset 30 --limit 21` 这种数字一旦算错，跑出来的是一片**看似正常**的错区间结果）。

所以本脚本：**从产物反推覆盖 → 算出缺口 → 只补缺口 → 自动汇总**。

## 覆盖从哪来（三条来源，缺一不可）

- `results/g3_safety_<数据集>_<tag>.jsonl` 的 `idx` 字段（新写入的都有）；
- 老 jsonl 没有 `idx` 时，用题目 `id` 在数据集里的位置反查（s1 那片就是这种情况）；
- 分片报告头部的 `第 a–b 道`（新写入的都有）。

## 老分片为什么还要手写区间

`p2 / p4 / p5` 三片是在"逐题落盘"和"报告写区间"**之前**跑的，产物里**没有任何位置信息** ——
只能按它们当时的 `--offset/--limit` 记在下面的 `_LEGACY_SLICES` 里。
⚠️ 这样做**不理想但诚实**：脚本会**先确认那份报告真的存在且是 real 模式**，才把它算进覆盖；
文件不在就不算，于是"凭记忆claim覆盖"最坏也只会**多跑一遍**，不会漏跑。

## 用法

    cd backend
    python eval/fill_g3_gaps.py --dataset eval/datasets/真题单选/真题单选.json          # 只看缺口
    python eval/fill_g3_gaps.py --dataset eval/datasets/真题单选/真题单选.json --run    # 补跑并汇总

补跑会自己设好 `LLM_MODE=real` 与 `NO_PROXY=*`（本机出站要走直连，见 ADR-0020），
tag 取 `g<起点>`，所以**同一条命令可反复执行**：跑完的题不会重复花调用（逐题落盘 + 按 id 续跑）。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
_RESULTS = _ROOT / "results"
_EVAL = _ROOT / "g3_per_option_eval.py"

#: 「逐题落盘」与「报告写区间」之前跑过的分片 —— 产物里没有位置信息，只能按当时命令记。
#: 键 = tag，值 = (含首含尾的数据集序号)。**只在对应报告存在且为 real 时才计入覆盖。**
#:
#: ⚠️ **`p1b` / `p3b` 刻意不在此表里**：它们撞上额度耗尽，只测出 18/51 道与 0/51 道，
#: 若按"区间已覆盖"记账会把缺口**少算**（看起来跑完了、其实没有）——
#: 这正是本脚本要消灭的失效方式。0–29 段已由 `s1` 的逐题产物精确覆盖。
_LEGACY_SLICES: dict[str, tuple[int, int]] = {
    "p2": (51, 101),
    "p4": (153, 203),
    "p5": (204, 253),
}


def _dataset_indices(ds: pathlib.Path) -> tuple[list[str], dict[str, int]]:
    items = json.loads(ds.read_text(encoding="utf-8")).get("items") or []
    ids = [str(it.get("id")) for it in items]
    return ids, {k: i for i, k in enumerate(ids)}


def _covered_from_jsonl(ds_stem: str, index: dict[str, int]) -> tuple[set[int], int]:
    """逐题产物里能直接读到的覆盖。返回 (序号集合, 无法归属的条数)。"""
    got: set[int] = set()
    unknown = 0
    for f in _RESULTS.glob(f"g3_safety_{ds_stem}_*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001 — 一行坏了不该让整份覆盖作废
                continue
            if rec.get("idx") is not None:
                got.add(int(rec["idx"]))
            elif str(rec.get("id")) in index:
                got.add(index[str(rec["id"])])
            else:
                unknown += 1
    return got, unknown


def _covered_from_md_headers(ds_stem: str) -> set[int]:
    """分片报告头部写了 `第 a–b 道` 的，直接采信它自己的声明。"""
    got: set[int] = set()
    for f in _RESULTS.glob(f"g3_per_option_{ds_stem}_*.md"):
        m = re.search(r"第 (\d+)–(\d+) 道", f.read_text(encoding="utf-8"))
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            got.update(range(a, b + 1))
    return got


def _covered_from_legacy(ds_stem: str) -> tuple[set[int], list[str]]:
    got: set[int] = set()
    used: list[str] = []
    for tag, (a, b) in _LEGACY_SLICES.items():
        f = _RESULTS / f"g3_per_option_{ds_stem}_{tag}.md"
        if not f.exists() or "LLM=real" not in f.read_text(encoding="utf-8"):
            continue
        got.update(range(a, b + 1))
        used.append(f"{tag}({a}–{b})")
    return got, used


def _ranges(nums: set[int]) -> list[tuple[int, int]]:
    """把序号集合压成连续的 (起, 止) 区间，便于一次跑一段。"""
    out: list[tuple[int, int]] = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补齐 G3' 安全性评测的缺口并汇总")
    ap.add_argument("--dataset", required=True, help="评测集路径（如 eval/datasets/真题单选/真题单选.json）")
    ap.add_argument("--run", action="store_true", help="真的补跑（不加只打印缺口）")
    ap.add_argument("--fake", action="store_true", help="用 fake 模型跑（仅自测通路，数字无意义）")
    args = ap.parse_args(argv)

    ds = pathlib.Path(args.dataset)
    if not ds.exists():
        raise SystemExit(f"评测集不存在：{ds}")
    ids, index = _dataset_indices(ds)
    stem = ds.stem
    votes = int(os.environ.get("GATE_G3_VOTES", "3"))

    from_jsonl, unknown = _covered_from_jsonl(stem, index)
    from_md = _covered_from_md_headers(stem)
    from_legacy, used = _covered_from_legacy(stem)
    covered = from_jsonl | from_md | from_legacy
    missing = _ranges(set(range(len(ids))) - covered)

    print(f"数据集 {ds.name}：共 {len(ids)} 道")
    print(f"  已覆盖 {len(covered)} 道　（jsonl {len(from_jsonl)} · 报告头 {len(from_md)}"
          f" · 老分片区间 {len(from_legacy)}）")
    if used:
        print(f"  老分片区间采信：{'、'.join(used)}")
    if unknown:
        print(f"  ⚠️ 有 {unknown} 条逐题记录无法归属到本数据集（换了数据集？）—— 已忽略")
    if not missing:
        print("  ✅ 已覆盖全部，无需补跑")
    else:
        need = sum(b - a + 1 for a, b in missing)
        print(f"  缺口 {need} 道，分成 {len(missing)} 段：")
        for a, b in missing:
            print(f"    offset={a:<4} limit={b - a + 1:<4}（序号 {a}–{b}）"
                  f"　约需 {votes * (b - a + 1)} 次调用")

    if not args.run or not missing:
        if missing:
            print("\n（加 --run 即开始补跑；跑完会自动汇总）")
        else:
            print(f"\n汇总：python eval/sum_g3_slices.py {stem} <各分片 tag>")
        return 0

    env = dict(os.environ)
    env["LLM_MODE"] = "fake" if args.fake else "real"
    # 本机出站被本地代理拦过（ADR-0020），评测脚本一律走直连。
    env["NO_PROXY"] = env["no_proxy"] = "*"
    env.setdefault("LLM_TIMEOUT", "90")

    print(f"\n开始补跑（LLM_MODE={env['LLM_MODE']}，逐段串行；被打断就重跑本命令，已完成的会跳过）")
    for a, b in missing:
        tag = f"g{a}"
        cmd = [sys.executable, str(_EVAL), "--dataset", str(ds),
               "--offset", str(a), "--limit", str(b - a + 1), "--amb", "0", "--tag", tag]
        print(f"\n--- 序号 {a}–{b} → tag={tag}", flush=True)
        rc = subprocess.call(cmd, env=env)
        if rc != 0:
            print(f"    ⚠️ 该段返回码 {rc}（超时/额度等）—— 继续下一段；重跑本命令会续跑这一段")

    tags = sorted(f.stem[len(f"g3_per_option_{stem}_"):]
                  for f in _RESULTS.glob(f"g3_per_option_{stem}_*.md"))
    print(f"\n=== 汇总（{len(tags)} 个分片）===", flush=True)
    return subprocess.call([sys.executable, str(_ROOT / "sum_g3_slices.py"), stem, *tags], env=env)


if __name__ == "__main__":
    sys.exit(main())
