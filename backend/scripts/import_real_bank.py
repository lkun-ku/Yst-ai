# -*- coding: utf-8 -*-
"""一次性入库：real_questions.json（管线产出 418 题）替换官方池占位假题。在 backend/ 下运行。

P1 起会把 JSON 里本就存在、但此前被丢弃的 `difficulty` 一并带入，
并显式标注 `source_kind=ai_variant`（这是 AI 变式题，不是真题原文）。
`subject` / `stage` / `kp_id` 由 `import_questions._with_dims` 按知识点树自动补齐。
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())  # 在 backend/ 下运行

from app.db import SessionLocal, init_db
from app.models import (
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
    SOURCE_KIND_AI_VARIANT,
)
from app.seed.import_questions import import_questions

SRC = os.path.join(os.getcwd(), "app", "seed", "real_questions.json")

#: 合法难度取值（与 questions.difficulty 的约定一致）
DIFFICULTIES = ("easy", "medium", "hard")


def _difficulty_of(raw: dict) -> str | None:
    """取原始 JSON 的难度并校验取值；非法或缺失**留空**，不硬塞默认值。

    硬塞默认值会让「某考点全是 medium」看起来像统计结论，实际是数据缺失，
    后续自适应组卷会被直接误导。
    """
    value = str(raw.get("difficulty") or "").strip().lower()
    return value if value in DIFFICULTIES else None


def build_items(raw: list[dict]) -> list[dict]:
    items = []
    for q in raw:
        items.append(
            {
                "module": q["module"],
                "knowledge_point": q["knowledge_point"],
                "stem": q["stem"],
                "options": json.dumps(q["options"], ensure_ascii=False),
                "answer": q["answer"],  # 管线产物已是 json 字符串（如 '["B"]'）
                "explanation": q["explanation"],
                "type": QuestionType.SINGLE,
                "source": QuestionSource.POOL,
                "proofread_status": ProofreadStatus.PASSED,  # judge 逐题审过
                "aigc_flag": True,
                "version": 1,
                "difficulty": _difficulty_of(q),
                "source_kind": SOURCE_KIND_AI_VARIANT,
            }
        )
    return items


def main() -> int:
    with open(SRC, encoding="utf-8") as f:
        d = json.load(f)
    raw = d["questions"]
    items = build_items(raw)

    init_db()
    db = SessionLocal()
    try:
        before = db.query(Question).filter(Question.source == QuestionSource.POOL).count()
        added = import_questions(db, items=items, clear=True)
        after = db.query(Question).filter(Question.source == QuestionSource.POOL).count()
        total = db.query(Question).count()
        print(f"POOL before={before} -> after={after} (added={added}) | 全库总数={total}")

        # 维度覆盖自检：新增列若没被带上，这里会直接暴露（而不是等到出题时才发现）
        with_subject = db.query(Question).filter(Question.subject.isnot(None)).count()
        with_kp = db.query(Question).filter(Question.kp_id.isnot(None)).count()
        with_diff = db.query(Question).filter(Question.difficulty.isnot(None)).count()
        print(
            f"维度覆盖：subject={with_subject} kp_id={with_kp} difficulty={with_diff}"
            f"（源文件 {len(raw)} 条）"
        )

        sample = db.query(Question).filter(Question.source == QuestionSource.POOL).first()
        print("抽样:", sample.module, "|", sample.knowledge_point, "|", sample.stem[:40])
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
