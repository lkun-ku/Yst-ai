"""G3'（逐选项判定）的**成对评测** —— 安全性与有效性必须同时拿数。

## 为什么这两半缺一不可

新判据来自一个负面结论：旧 G3（盲答「选哪个」）在**同义改写型**歧义题上只拦下 15%
（`eval/g3_ambiguity.py`）。把判据换成「逐项判对不对」针对性很强，但针对性不等于有效：

- **有效性**（歧义题上拦住多少）不够 → 换了也没用；
- **安全性**（官方好题上误杀多少）变差 → 换来的是产题量崩掉。

只报一半都会得出错误结论，所以本脚本一次跑完两半，并放在同一张表里。

## 两种歧义构造（`--amb-mode`）

| 模式 | 造的是什么 | 真值 | 需要模型调用来构造？ |
| --- | --- | --- | --- |
| `synonym`（默认，N3） | 把正确选项**同义改写**后顶替一个错误选项 → 两个选项都说得通 | 强（改写是否等价已逐条人工复核过） | **是** |
| `limited`（N4） | 去掉题干的**限定词**（主要/根本/最…）→ 题干失去限定，多个选项都成立 | **构造推定，需人工复核** | **否**（纯字符串） |

真题里的歧义更常是后者（题干缺限定），只测前者会让结论偏向"措辞型"缺陷。
两者的构造手法互补，所以都要跑，但**读法不同**：N4 的绝对数字含推定成分，
**新旧判据在同一批题上的对比**才是硬结论（同一批题、同一把尺子）。

## 成本

- `synonym` 模式：安全性 30×3 + 歧义 20×(1 改写 + 3 判定)。旧判据两格直接读
  **既有证据文件**，不重复消耗额度。
- `limited` 模式：歧义 20×(3 新判定 + 3 旧判定) —— 这批题没有历史数字可比，
  **必须两条判据都现场跑**；安全性沿用既有记录（0.0，n=30）。

## 用法

    python eval/g3_per_option_eval.py                                  # fake（免费）
    $env:LLM_MODE='real'; python eval/g3_per_option_eval.py --amb-mode limited --amb 20
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("LLM_MODE", "fake")

from app.config import settings  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.quality_gates import vote_per_option, vote_uniqueness  # noqa: E402
from eval.g3_ambiguity import build_variant, drop_qualifier, paraphrase_option  # noqa: E402

_DATASET = pathlib.Path(__file__).resolve().parent / "datasets" / "g3_唯一性" / "官方题样本.json"
_OUT_DIR = pathlib.Path(__file__).resolve().parent / "results"


@dataclass
class Half:
    """一半评测的结果。"""

    n: int = 0
    n_blocked: int = 0

    @property
    def rate(self) -> float:
        return round(self.n_blocked / self.n, 4) if self.n else 0.0

    def as_dict(self) -> dict:
        return {"n": self.n, "n_blocked": self.n_blocked, "rate": self.rate}


def _tally(passed: bool, h: Half) -> None:
    h.n += 1
    if not passed:
        h.n_blocked += 1


def _old_number(path: pathlib.Path, key: str) -> str:
    """读既有证据里的旧判据数字（避免重复消耗额度）。"""
    if not path.exists():
        return "（无）"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return str((d.get("metrics") or {}).get(key, "（无）"))
    except Exception:  # noqa: BLE001 — 读不出就如实显示"无"，不要伪造数字
        return "（无）"


def _build_ambiguous(client, items: list[dict], amb_mode: str) -> tuple[list[dict], list[str]]:
    """返回 `(变体列表, 逐条构造说明)`。构造不出来的一律跳过并说明原因。"""
    built: list[dict] = []
    notes: list[str] = []
    for it in items:
        if amb_mode == "limited":
            v, word = drop_qualifier(it)
            if v is None:
                notes.append(f"#{it.get('id')} 跳过：题干没有可去的限定词")
                continue
            built.append(v)
            notes.append(f"#{it.get('id')} 去掉限定词「{word}」")
            continue
        para, why = paraphrase_option(client, it)
        if not para:
            notes.append(f"#{it.get('id')} 跳过：{why}")
            continue
        v = build_variant(it, para)
        if v is None:
            notes.append(f"#{it.get('id')} 跳过：造不出变体")
            continue
        built.append(v)
        notes.append(f"#{it.get('id')} 同义改写：{para[:40]}")
    return built, notes


def main(
    limit: int = 30,
    amb: int = 20,
    amb_mode: str = "synonym",
    safety: bool = True,
    dataset: str | None = None,
    offset: int = 0,
    tag: str = "",
) -> dict:
    from datetime import datetime

    ds = pathlib.Path(dataset) if dataset else _DATASET
    if not ds.exists():
        raise SystemExit(f"评测集不存在：{ds}")
    all_items = json.loads(ds.read_text(encoding="utf-8")).get("items") or []
    # `--offset` + `--tag` 是为了**分片并行**：单次调用约 30 秒，254 道串行要两个多小时。
    # 切片跑不会改变每条样本的判定（判据逐题独立），只改吞吐；各片的 md 文件靠 tag 区分。
    items = all_items[offset : offset + limit] if limit else all_items[offset:]
    if not items:
        raise SystemExit(f"评测集为空（offset={offset} limit={limit} 共 {len(all_items)} 道）")

    client = get_llm_client()
    mode_llm = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    print(
        f"数据集 {ds.name}（{len(items)} 道）　歧义模式 {amb_mode}　目标 {amb}　"
        f"LLM={mode_llm}　投票 {settings.gate_g3_votes}",
        flush=True,
    )

    old_safe = _old_number(_OUT_DIR / "g3_baseline.json", "blocked_rate")
    old_syn = _old_number(_OUT_DIR / "g3_n3_ambiguity.json", "blocked_rate")

    safe_h = Half()
    blocks: list[dict] = []
    n_trap_hits = 0
    # 每道题**成功判定的次数**。接口超时/备用通道欠费时票数会不足 ——
    # 那时结论由更少的样本决定，**必须让报告自己说出来**，否则数字会被当成满票结果。
    vote_dist: Counter[int] = Counter()
    if safety:
        print("\n=== A) 安全性：好题上的误杀率（应**低**）===", flush=True)
        for i, it in enumerate(items):
            if i and i % 25 == 0:
                print(f"    [{i}/{len(items)}] …", flush=True)
            v = vote_per_option(client, it, settings.gate_g3_votes)
            if v.n == 0:
                continue
            _tally(v.passed, safe_h)
            vote_dist[v.n] += 1
            # `trap` = 教辅标注的**易错项**（学生容易误选的那个错误选项）。
            # 判据若把它也判成"成立"，就不是"模型拿不准"，而是**过度判定**：
            # 干扰项被当成了并列的正确答案 —— 这是本判据最需要盯的失效方式。
            trap = str(it.get("trap") or "").upper()
            hit = bool(trap) and trap in set(v.judged)
            n_trap_hits += int(hit)
            if not v.passed:
                expect = [str(x).upper() for x in (it.get("answer") or [])]
                if hit:
                    reason = f"把教辅标注的易错项 {trap} 也判成立（过度判定）"
                elif not v.stable:
                    reason = "多次判定不一致"
                elif not (set(v.judged) & set(expect)):
                    reason = "连答案键都没判成立"
                else:
                    reason = "判少了（与自带答案不符）"
                blocks.append(
                    {
                        "id": it.get("id"),
                        "expect": expect,
                        "judged": list(v.judged),
                        "stable": v.stable,
                        "trap": trap,
                        "trap_hit": hit,
                        "reason": reason,
                        "stem": (it.get("stem") or "")[:70],
                    }
                )

    print("\n=== B) 有效性：歧义题上的拦截率（应**高**）===", flush=True)
    built, notes = _build_ambiguous(client, items[:amb], amb_mode)
    for n in notes[:8]:
        print(f"    {n}", flush=True)

    new_h = Half()
    old_h = Half()
    samples: list[dict] = []
    for i, it in enumerate(built):
        if i and i % 5 == 0:
            print(f"    [{i}/{len(built)}] …", flush=True)
        v = vote_per_option(client, it, settings.gate_g3_votes)
        if v.n:
            _tally(v.passed, new_h)
        if amb_mode == "limited":
            # 这批题没有历史数字可比 → 旧判据必须**现场跑**，否则对比无从谈起
            o = vote_uniqueness(client, it, settings.gate_g3_votes)
            if o.n:
                _tally(o.passed, old_h)
        samples.append({
            "id": it.get("id"),
            "stem": (it.get("stem") or "")[:60],
            "expect": list(it.get("answer") or []),
            "judged": list(v.judged),
            "stable": v.stable,
            "new_passed": v.passed,
        })

    is_limited = amb_mode == "limited"
    old_cell = (old_h.rate if is_limited else old_syn)
    md_name = "g3_n4_limited.md" if is_limited else "g3_per_option.md"
    # ⚠️ **换数据集必须换文件名**（与 `g3_eval.py` 同一条纪律）：既有守卫只挡"fake 覆盖 real"，
    # 但一次**真实**运行若换了数据集，同样会把别的基准槽位占掉 —— 那份证据再也回不来。
    if ds.name != _DATASET.name:
        md_name = f"{pathlib.Path(md_name).stem}_{pathlib.Path(ds.stem).name}.md"
        print(f"    数据集不同（{ds.name}）→ 结果写到 {md_name}，不占用默认基准槽位")
    if tag:
        md_name = f"{pathlib.Path(md_name).stem}_{tag}.md"
        print(f"    分片 tag={tag} → 结果写到 {md_name}")
    title = (
        "G3'（逐选项判定）· N4 题干缺限定歧义"
        if is_limited
        else "G3'（逐选项判定）成对评测"
    )

    lines = [
        f"# {title}",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}　LLM={mode_llm}　"
        f"每次投票 {settings.gate_g3_votes} 次",
        "- 判据：对每个选项独立判「是否成立」，投 N 次 → 多数表决 → 与答案键比对",
        "- 成本：**与旧判据持平**（一次调用给出所有选项的是/否，不是每个选项一次调用）",
        f"- 歧义构造：`{amb_mode}`"
        + ("（**题干去限定词**；真值为构造推定，需人工复核）" if is_limited else "（同义改写；真值已逐条复核）"),
        "",
        "## 两半必须成对看",
        "",
        "| 数据集 | 该拦吗 | 旧判据（盲答选哪个） | **新判据（逐项判对不对）** | 读作 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if safety:
        lines.append(
            f"| 官方好题（n={safe_h.n}） | **不该** | {old_safe} | **{safe_h.rate}** | 越低越安全（误杀） |"
        )
    else:
        lines.append(f"| 官方好题 | **不该** | {old_safe} | **（沿用既有记录 0.0，n=30）** | 越低越安全 |")
    lines += [
        f"| 歧义变体（n={new_h.n}） | **该** | {old_cell} | **{new_h.rate}** | 越高越有效（拦得住） |",
        "",
    ]
    if not is_limited:
        lines += [
            "> 旧判据的两格取自既有证据文件（`g3_baseline.json` / `g3_n3_ambiguity.json`），",
            "> **不重复消耗额度** —— 同一套题、同一把尺子。",
            "",
        ]
    else:
        lines += [
            "> 本模式**两条判据都现场跑**：N4 这批题没有历史数字可比，只跑新的就无从对比。",
            "",
            "## 构造说明（真值需人工复核）",
            "",
            "> N4 去掉题干限定词后，是否**真的**多个选项都成立，取决于题目本身 —— 程序判不了。",
            "> 所以这里逐条列出被去掉的词；复核后若不成立，该条应从战绩中剔除。",
            "",
        ]
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    if safety and blocks:
        # 把「误杀」拆开看：整题被判不合格，可能来自四种**完全不同**的原因，
        # 处置也完全不同（判多了要收紧、判少了要放宽、不稳定要加票）。
        lines += ["", "## 拦截归因（把「误杀」拆开看）", ""]
        by_reason: dict[str, list[dict]] = {}
        for b in blocks:
            by_reason.setdefault(b["reason"], []).append(b)
        lines.append(f"- 有效判定 {safe_h.n} 道，其中被拦 {len(blocks)} 道：")
        for reason, grp in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  - **{reason}** × {len(grp)}")
        lines += [
            "",
            f"- 其中**把教辅标注的易错项也判成立**的有 **{n_trap_hits}** 道 —— "
            "这类不是「模型拿不准」，而是**过度判定**（干扰项被当成并列的正确选项）。",
            "  易错项正是学生最容易误选的错误项，判据对它过敏就会把大量**好题**拦掉。",
            "",
            "- 有效票数分布（每道题成功判定的次数）："
            + "、".join(f"{k} 票 {c} 道" for k, c in sorted(vote_dist.items())),
        ]
        if any(k < settings.gate_g3_votes for k in vote_dist):
            lines.append(
                "  ⚠️ **存在票数不足的题**（接口超时 / 备用通道不可用）—— 这类题的结论"
                "由更少的样本决定，读数字时必须带上这个前提，不能当成满票结果。"
            )
        lines += [
            "",
            "### 被拦的题（前 40 条）",
            "",
        ]
        for b in blocks[:40]:
            lines.append(
                f"- #{b['id']}　期望 {b['expect']}　判定 {b['judged']}　"
                f"易错项 {b['trap'] or '（无）'}　{b['reason']}"
            )
            lines.append(f"  - {b['stem']}")
        lines.append("")

    lines += ["## 逐题（歧义集）", ""]
    for s in samples:
        lines.append(
            f"- #{s['id']}　期望 {s['expect']}　判定 {s['judged']}　稳定 {s['stable']}　"
            f"{'放行' if s['new_passed'] else '**拦截**'}"
        )
        lines.append(f"  - {s['stem']}")
    lines += [
        "",
        "## 结论口径",
        "",
        "新判据要**同时**满足「官方题误杀率不比旧判据差」且「歧义题拦截率显著更高」，",
        "才算真的改进了 —— 只满足一条都不能据此切换（前者是产题量，后者才是本来的目的）。",
        "",
    ]

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / md_name
    if mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = md.with_name(f"{md.stem}_fake.md")
        print(f"\n    已有真实结果，本次 fake 改写到 {md.name}（不覆盖）")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"\n    **误杀 {safe_h.rate if safety else '(沿用 0.0)'}**（旧 {old_safe}）　|　"
        f"**歧义拦截 新 {new_h.rate} / 旧 {old_cell}**"
    )
    if safety:
        print(f"    拦截归因：把易错项判成立的 {n_trap_hits} 道 / 共拦 {len(blocks)} 道")
    print(f"\n已写入：{md}")
    return {
        "safety": safe_h.as_dict(),
        "safety_blocks": blocks,
        "safety_trap_hits": n_trap_hits,
        "effectiveness_new": new_h.as_dict(),
        "effectiveness_old": old_h.as_dict() if is_limited else {"from_file": old_syn},
        "mode": amb_mode,
        "dataset": ds.name,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="官方题数（安全性，默认 30）")
    ap.add_argument("--amb", type=int, default=20, help="歧义变体数（默认 20）")
    ap.add_argument(
        "--amb-mode",
        choices=("synonym", "limited"),
        default="synonym",
        help="歧义构造：synonym=同义改写(N3，需模型)；limited=题干去限定词(N4，纯字符串)",
    )
    ap.add_argument(
        "--no-safety",
        action="store_true",
        help="跳过安全性那一半（沿用既有记录；安全性已在 n=30 上测得 0.0）",
    )
    ap.add_argument(
        "--dataset",
        default=None,
        help="评测集路径（默认 30 道官方题样本）。可指向 `datasets/真题单选/真题单选.json`"
        "（254 道真题，带 `trap` = 教辅标注的易错项）—— 换数据集会把结果写到独立证据文件",
    )
    ap.add_argument("--offset", type=int, default=0, help="从第几道开始（分片并行用）")
    ap.add_argument("--tag", default="", help="分片标记，写进结果文件名（如 p1/p2…）")
    args = ap.parse_args()
    main(
        limit=args.limit,
        amb=args.amb,
        amb_mode=args.amb_mode,
        safety=not args.no_safety,
        dataset=args.dataset,
        offset=args.offset,
        tag=args.tag,
    )
