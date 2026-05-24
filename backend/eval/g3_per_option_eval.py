"""G3'（逐选项判定）的**成对评测** —— 安全性与有效性必须同时拿数。

## 为什么这两半缺一不可

新判据来自一个负面结论：旧 G3（盲答「选哪个」）在真实歧义题上**只拦下 15%**
（`eval/g3_ambiguity.py`）。把判据换成「逐项判对不对」针对性很强，但针对性不等于有效：

- **有效性**（歧义题上拦住多少）不够 → 换了也没用；
- **安全性**（官方好题上误杀多少）变差 → 换来的是产题量崩掉。

只报一半都会得出错误结论，所以本脚本**一次跑完两半**，并放在同一张表里。

## 与既有数字对齐（不重复花钱）

旧判据在两个数据集上的数字**已经存在**（`g3_baseline.json` 的误杀率、
`g3_n3_ambiguity.json` 的歧义拦截率），所以这里只跑**新判据**，旧数字直接读文件对比 ——
同一套题、同一把尺子，但不重复消耗额度。

## 用法

    python eval/g3_per_option_eval.py --limit 30 --amb 20     # fake（免费）
    $env:LLM_MODE='real'; python eval/g3_per_option_eval.py --limit 30 --amb 20
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("LLM_MODE", "fake")

from app.config import settings  # noqa: E402
from app.services.llm_client import get_llm_client  # noqa: E402
from app.services.quality_gates import vote_per_option  # noqa: E402
from eval.g3_ambiguity import build_variant, paraphrase_option  # noqa: E402

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


def _run(client, items: list[dict], collect_samples: bool = False) -> tuple[Half, list[dict]]:
    h = Half()
    samples: list[dict] = []
    for i, it in enumerate(items):
        if i and i % 5 == 0:
            print(f"    [{i}/{len(items)}] …", flush=True)
        v = vote_per_option(client, it, settings.gate_g3_votes)
        if v.n == 0:
            continue  # 无法判定 → 不计入（与闸门"放行"的处理一致）
        h.n += 1
        if not v.passed:
            h.n_blocked += 1
        if collect_samples:
            samples.append({
                "id": it.get("id"),
                "expect": list(it.get("answer") or []),
                "judged": list(v.judged),
                "stable": v.stable,
                "passed": v.passed,
                "votes": [list(x) for x in v.votes],
            })
    return h, samples


def _old_number(path: pathlib.Path, key: str) -> str:
    """读既有证据里的旧判据数字（避免重复消耗额度）。"""
    if not path.exists():
        return "（无）"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return str((d.get("metrics") or {}).get(key, "（无）"))
    except Exception:  # noqa: BLE001 — 读不出就如实显示"无"，不要伪造数字
        return "（无）"


def main(limit: int = 30, amb: int = 20) -> dict:
    from datetime import datetime

    if not _DATASET.exists():
        raise SystemExit(f"评测集不存在：{_DATASET}")
    items = (json.loads(_DATASET.read_text(encoding="utf-8")).get("items") or [])[:limit]
    if not items:
        raise SystemExit("评测集为空")

    client = get_llm_client()
    mode_llm = "fake" if type(client).__name__ == "FakeLLMClient" else "real"
    print(f"官方题 {len(items)}　歧义变体目标 {amb}　LLM={mode_llm}　每次投票 {settings.gate_g3_votes}", flush=True)

    print("\n=== A) 安全性：官方好题上的误杀率（应**低**）===", flush=True)
    safe_h, _ = _run(client, items)

    print("\n=== B) 有效性：歧义题上的拦截率（应**高**）===", flush=True)
    amb_items: list[dict] = []
    for it in items[:amb]:
        para, why = paraphrase_option(client, it)
        if not para:
            print(f"    #{it.get('id')} 跳过：{why}", flush=True)
            continue
        v = build_variant(it, para)
        if v:
            amb_items.append(v)
    amb_h, amb_samples = _run(client, amb_items, collect_samples=True)

    old_safe = _old_number(_OUT_DIR / "g3_baseline.json", "blocked_rate")
    old_amb = _old_number(_OUT_DIR / "g3_n3_ambiguity.json", "blocked_rate")

    lines = [
        "# G3'（逐选项判定）成对评测",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}　LLM={mode_llm}　"
        f"每次投票 {settings.gate_g3_votes} 次",
        f"- 判据：对每个选项独立判「是否成立」，投 N 次 → 多数表决 → 与答案键比对",
        f"- 成本：**与旧判据持平**（一次调用给出所有选项的是/否，不是每个选项一次调用）",
        "",
        "## 两半必须成对看",
        "",
        "| 数据集 | 该拦吗 | **旧判据**（盲答选哪个） | **新判据**（逐项判对不对） | 读作 |",
        "| --- | --- | --- | --- | --- |",
        f"| 官方好题（n={safe_h.n}） | **不该** | {old_safe} | **{safe_h.rate}** | 越低越安全（误杀） |",
        f"| 歧义变体（n={amb_h.n}） | **该** | {old_amb} | **{amb_h.rate}** | 越高越有效（拦得住） |",
        "",
        "> 旧判据的两格取自既有证据文件（`g3_baseline.json` / `g3_n3_ambiguity.json`），",
        "> **不重复消耗额度** —— 同一套题、同一把尺子。",
        "",
        "## 逐题（歧义集）",
        "",
    ]
    for s in amb_samples:
        lines.append(
            f"- #{s['id']}　期望 {s['expect']}　判定 {s['judged']}　稳定 {s['stable']}　"
            f"{'放行' if s['passed'] else '**拦截**'}"
        )
    lines += [
        "",
        "## 结论口径",
        "",
        "新判据要**同时**满足「官方题误杀率不比旧判据差」且「歧义题拦截率显著高于 0.15」，",
        "才算真的改进了 —— 只满足一条都不能据此切换（前者是产题量，后者才是本来的目的）。",
        "",
    ]

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = _OUT_DIR / "g3_per_option.md"
    if mode_llm == "fake" and md.exists() and "LLM=real" in md.read_text(encoding="utf-8"):
        md = md.with_name(f"{md.stem}_fake.md")
        print(f"\n    已有真实结果，本次 fake 改写到 {md.name}（不覆盖）")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"\n    **误杀 {safe_h.rate}**（旧 {old_safe}）　|　**歧义拦截 {amb_h.rate}**（旧 {old_amb}）"
    )
    print(f"\n已写入：{md}")
    return {
        "safety": safe_h.as_dict(),
        "effectiveness": amb_h.as_dict(),
        "old": {"safety_blocked_rate": old_safe, "ambiguity_blocked_rate": old_amb},
        "amb_samples": amb_samples,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="官方题数（安全性，默认 30）")
    ap.add_argument("--amb", type=int, default=20, help="歧义变体数（有效性，默认 20）")
    args = ap.parse_args()
    main(limit=args.limit, amb=args.amb)
