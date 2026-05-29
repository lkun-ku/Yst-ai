"""把结构化好的真题与解析，汇成**答案库**（`data/answer_bank/`，方案 B，独立于知识库）。

## 它做什么

- **单选**：从 `eval/datasets/真题单选/真题单选.json` 读（题干/选项/答案）→ 条目；
- **主观题**：从 `eval/datasets/真题主观/真题主观.json` 读（题干/标准答案/采分点）→ 条目（还没产出时会自动跳过）。

输出 `data/answer_bank/真题答卷库.json`，形态与 `app/services/answer_bank.py` 的加载器一致：

    {"note","authority","items":[{"id","type","stem","options","answer","points","reference","source"}]}

## 三条边界（写在这里，免得产物被误用）

1. **权威级别 = 半官方**：这是教辅整理的解析，**不是**官方发布（官方从不公布真题与评分细则）；
2. **只存答题/评测必需字段**：不复制解析正文，控制体积与版权暴露面；
3. **不进知识库**：落在 `data/answer_bank/`（在 `official_kb_dir` 之外），
   由 `answer_bank.retrieve_for_question()` **优先检索**，而非混进官方语料。

## 用法

    python scripts/build_answer_bank.py            # 默认写入 backend/data/answer_bank/
    python scripts/build_answer_bank.py --out DIR
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CHOICE = _ROOT / "eval" / "datasets" / "真题单选" / "真题单选.json"
_SUBJECTIVE = _ROOT / "eval" / "datasets" / "真题主观" / "真题主观.json"
_DEFAULT_OUT = _ROOT / "data" / "answer_bank"


def _read_items(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [it for it in (items or []) if isinstance(it, dict)]


def build(out_dir: pathlib.Path) -> dict:
    items: list[dict] = []

    for it in _read_items(_CHOICE):
        items.append({
            "id": it.get("id"),
            "type": it.get("type") or "single",
            "stage": it.get("stage"),
            "subject": it.get("subject"),
            "stem": it.get("stem"),
            "options": it.get("options") or [],
            "answer": it.get("answer") or [],
            "trap": it.get("trap"),  # 教辅标注的易错项（歧义候选，供批改/讲解参考）
            "source": {"file": _CHOICE.name, "authority": "半官方"},
        })

    n_sub = 0
    for it in _read_items(_SUBJECTIVE):
        items.append({
            "id": it.get("id"),
            "type": it.get("qtype") or "subjective",
            "stage": it.get("stage"),
            "subject": it.get("subject"),
            "stem": it.get("stem"),
            "reference": it.get("reference"),   # 标准/示范作答 = 满分卷
            "points": it.get("points") or [],   # 采分点（可由 split_reference_points 切出）
            "source": {"file": _SUBJECTIVE.name, "authority": "半官方"},
        })
        n_sub += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "真题答卷库.json"
    payload = {
        "note": (
            "教辅整理的历年真题与解析；**半官方**，非官方发布。仅用于答题/批改时优先参考，"
            "不进知识库（ADR-0003 真题原文不入库）。"
        ),
        "authority": "半官方",
        "counts": {"single": len(items) - n_sub, "subjective": n_sub, "total": len(items)},
        "items": items,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"out": str(out), **payload["counts"]}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(_DEFAULT_OUT))
    args = ap.parse_args()
    st = build(pathlib.Path(args.out))
    print(f"单选 {st['single']} · 主观 {st['subjective']} · 合计 {st['total']}")
    if not st["subjective"]:
        print("（主观题数据集尚未产出 —— OCR 完成后重跑本脚本即可补上）")
    print(f"已写入：{st['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
