"""「稳定性该不该单独触发拦截」——**离线反算 A/B，不花一分额度**。

## 为什么可以不重跑

落盘数据里每题都有：多数表决结果 `judged`、是否一致 `stable`、官方答案键 `expect`。
而 `passed = stable and matches`（`matches` = judged == expect）。所以：

    关掉"稳定性单独拦截"后，被拦 ⇔ ¬matches

被释放的题**按定义**就是「多数表决结果 == 官方答案键、只是几次判定不一致」。
于是新规则下的误杀数是**可精确反算**的，不需要再调一次模型。

## 为什么必须做双边

只比"误杀率"是**单边比较**：关掉稳定性，误杀必然下降（那是定义使然，不是证据）。
真正的代价在另一半 —— 歧义题的**拦截力**会不会跟着掉。所以两边都算。
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

#: 本文件就在 `backend/eval/` 下，结果目录是同级的 `results/`。
#: 不写死相对 repo 根的层级 —— 挪目录时这里不会悄悄指错。
RES = pathlib.Path(__file__).resolve().parent / "results"

#: 与 `eval/g3_slices.py` 的账本一致：这两片没测完（撞上额度耗尽），不参与统计。
POLLUTED = {"p1b", "p3b"}

BLOCK_LINE = re.compile(
    r"^- #(\S+)　期望 (\[.*?\])　判定 (\[.*?\])　易错项 (\S+)　(.+)$"
)
AMB_LINE = re.compile(
    r"^- #(\S+)　期望 (\[.*?\])　判定 (\[.*?\])　稳定 (True|False)　(放行|\*\*拦截\*\*)$"
)
N_ROW = re.compile(r"\| 官方好题（n=(\d+)） \|")


def _keys(raw: str) -> set[str]:
    try:
        return {str(x).strip().upper() for x in (ast.literal_eval(raw) or [])}
    except Exception:  # noqa: BLE001
        return set()


def load_from_jsonl() -> tuple[list[dict], int]:
    """有逐题落盘的分片（s1 / g30 / g102）。"""
    recs: list[dict] = []
    for p in sorted(RES.glob("g3_safety_真题单选_*.jsonl")):
        tag = p.stem.split("_")[-1]
        if tag in POLLUTED:
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if int(r.get("n") or 0) == 0:      # 投票无效的不计入
                continue
            r["_src"] = f"jsonl:{tag}"
            recs.append(r)
    return recs, len({r["_src"] for r in recs})


def load_from_md(already: set[str]) -> list[dict]:
    """只有报告的分片（p2 / p4 / p5）：从「被拦的题」段重建**被拦**的那些。

    未被拦的题：passed ⇒ stable 且 matches ⇒ 在新旧规则下都放行，无需逐题数据。

    ⚠️ `already` 是**已经有逐题落盘的分片**（优先用 jsonl，它带 `stable`）——
    否则同一批题既被 jsonl 算一遍、又被 md 算一遍，n 会虚高（实测踩到：356 > 254）。
    """
    out: list[dict] = []
    for p in sorted(RES.glob("g3_per_option_真题单选_*.md")):
        tag = p.stem.split("_")[-1]
        if tag in POLLUTED or tag in already:
            continue
        text = p.read_text(encoding="utf-8")
        n = int(N_ROW.search(text).group(1)) if N_ROW.search(text) else 0
        blocked: list[dict] = []
        for line in text.splitlines():
            m = BLOCK_LINE.match(line)
            if m:
                blocked.append({
                    "id": m.group(1),
                    "expect": sorted(_keys(m.group(2))),
                    "judged": sorted(_keys(m.group(3))),
                    "trap": m.group(4) if m.group(4) != "（无）" else "",
                    "reason": m.group(5),
                })
        out.append({"tag": tag, "n": n, "blocked": blocked})
    return out


def main() -> None:
    jsonl_recs, _n_tags = load_from_jsonl()
    jsonl_tags = {r["_src"].split(":")[1] for r in jsonl_recs}
    md_slices = load_from_md(jsonl_tags)
    print(f"[覆盖] 逐题落盘分片 {sorted(jsonl_tags)}；仅报告分片 "
          f"{[s['tag'] for s in md_slices]}（重叠的已跳过，避免重复计数）\n")

    # ---------- 安全性：官方好题 ----------
    n_total = len(jsonl_recs) + sum(s["n"] for s in md_slices)

    cur_blocked = 0
    cf_blocked = 0
    released: list[dict] = []

    for r in jsonl_recs:
        expect = {str(x).strip().upper() for x in (r.get("expect") or [])}
        judged = {str(x).strip().upper() for x in (r.get("judged") or [])}
        matches = judged == expect
        stable = bool(r.get("stable"))
        if not (stable and matches):
            cur_blocked += 1
        if not matches:
            cf_blocked += 1
        elif not stable:
            released.append({
                "id": r.get("id"), "expect": sorted(expect), "judged": sorted(judged),
                "trap": r.get("trap") or "", "reason": r.get("reason") or "",
                "src": r["_src"],
            })

    for s in md_slices:
        for b in s["blocked"]:
            cur_blocked += 1
            if set(b["judged"]) != set(b["expect"]):
                cf_blocked += 1          # 多数表决与答案键不符 → 新规则下**仍然**被拦
            else:
                released.append({**b, "src": f"md:{s['tag']}"})

    print("=== A) 安全性：官方好题上的误杀率（越低越好）===")
    print(f"  覆盖 {n_total} 道（逐题落盘 {len(jsonl_recs)} 道 + 仅报告 {sum(s['n'] for s in md_slices)} 道；"
          f"已排除未测完的 {sorted(POLLUTED)}）")
    print(f"  现状（稳定性可单独拦）　：被拦 {cur_blocked} → 误杀率 {cur_blocked / n_total:.2%}")
    print(f"  反事实（稳定性不单独拦）：被拦 {cf_blocked} → 误杀率 {cf_blocked / n_total:.2%}")
    print(f"  释放 {len(released)} 道 —— 它们的**多数表决结果都等于官方答案键**（否则不会被释放）：")
    for r in released:
        print(f"    · #{r['id']}　期望 {r['expect']}　判定 {r['judged']}　易错项 {r['trap'] or '（无）'}"
              f"　[{r['src']}]")
    trap_leak = [r for r in released if r["trap"] and r["trap"].upper() in {x.upper() for x in r["judged"]}]
    print(f"  其中**判了易错项**的：{len(trap_leak)} 道（>0 则不是纯收益）")

    # ---------- 有效性：歧义题 ----------
    print("\n=== B) 有效性：歧义题上的拦截率（越高越好）===")
    amb: list[dict] = []
    seen_ids: set[tuple] = set()
    for p in sorted(RES.glob("g3_*.md")):
        # 不再按文件名排除「真题单选」：歧义变体**也可以**建在真题数据集上
        # （`--dataset 真题单选.json --limit 254`），此时报告名就叫 `g3_n4_limited_真题单选.md`。
        # 改用**行格式**识别（只有歧义逐题行能匹配 AMB_LINE），并按 (id, judged) 去重 ——
        # 同一批变体可能同时出现在多个报告里，不去重会让分母虚高。
        for line in p.read_text(encoding="utf-8").splitlines():
            m = AMB_LINE.match(line)
            if m:
                key = (m.group(1), tuple(sorted(_keys(m.group(3)))))
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                amb.append({
                    "id": m.group(1),
                    "expect": sorted(_keys(m.group(2))),
                    "judged": sorted(_keys(m.group(3))),
                    "stable": m.group(4) == "True",
                    "blocked": "拦截" in m.group(5),
                    "file": p.name,
                })
    if not amb:
        print("  （没解析到逐题歧义样本 —— 需检查 md 格式）")
        return
    cur_amb = sum(1 for a in amb if a["blocked"])
    cf_amb = sum(1 for a in amb if set(a["judged"]) != set(a["expect"]))
    print(f"  样本 {len(amb)} 道（来自 {sorted({a['file'] for a in amb})}）")
    print(f"  现状拦截　：{cur_amb}/{len(amb)} = {cur_amb / len(amb):.2%}")
    print(f"  反事实拦截：{cf_amb}/{len(amb)} = {cf_amb / len(amb):.2%}")
    lost = [a for a in amb if a["blocked"] and set(a["judged"]) == set(a["expect"])]
    print(f"  因关掉稳定性而**漏放**的：{len(lost)} 道（这就是代价）")
    for a in lost[:10]:
        print(f"    · #{a['id']}　期望 {a['expect']}　判定 {a['judged']}　稳定 {a['stable']}")

    print("\n=== 结论口径 ===")
    print("  误杀率下降是**定义使然**；唯一有信息量的是 B 栏：")
    print("  若 B 栏拦截率不掉（或只掉零点几个点），则稳定性单独拦截**没有换来安全性**，该改；")
    print("  若 B 栏掉得多，那是为「不确定就拦」付的真实代价，需要权衡后决定。")


if __name__ == "__main__":
    sys.exit(main())
