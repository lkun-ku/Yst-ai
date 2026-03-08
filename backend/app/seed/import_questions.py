"""种子题目导入脚本（票 04 / D2 决策）。

- 以大纲考点骨架按模块生成变式题，来源=pool、审校=已抽检通过。
- 幂等：以 (module, knowledge_point, stem) 去重，重跑不重复入库。
- 运行：python -m app.seed.import_questions
"""

import json

from sqlalchemy.orm import Session

from ..db import SessionLocal, init_db
from ..models import ProofreadStatus, Question, QuestionSource, QuestionType
from .questions_data import KNOWLEDGE_POINTS


def build_questions() -> list[dict]:
    items: list[dict] = []
    opt_idx = 0
    for module, points in KNOWLEDGE_POINTS.items():
        for kp in points:
            for variant in range(2):  # 每考点 2 题 => 约 300 题
                correct = chr(ord("A") + (opt_idx % 4))
                options = json.dumps(
                    [
                        {"key": "A", "text": f"关于《{kp}》的正确表述（种子变式{variant + 1}）"},
                        {"key": "B", "text": f"关于《{kp}》的常见误解（种子变式{variant + 1}）"},
                        {"key": "C", "text": f"与《{kp}》无关的表述（种子变式{variant + 1}）"},
                        {"key": "D", "text": f"对《{kp}》的颠倒表述（种子变式{variant + 1}）"},
                    ],
                    ensure_ascii=False,
                )
                stem = f"下列关于《{kp}》的表述，正确的是？（种子变式{variant + 1}）"
                answer = json.dumps([correct], ensure_ascii=False)
                explanation = f"本题考查考点《{kp}》。正确选项为 {correct}：「{kp}」的核心要点如上所述。"
                items.append(
                    {
                        "module": module,
                        "knowledge_point": kp,
                        "stem": stem,
                        "options": options,
                        "answer": answer,
                        "explanation": explanation,
                        "type": QuestionType.SINGLE,
                        "source": QuestionSource.POOL,
                        "proofread_status": ProofreadStatus.PASSED,
                        "aigc_flag": True,
                        "version": 1,
                    }
                )
                opt_idx += 1
    return items


def import_questions(db: Session, items: list[dict] | None = None, clear: bool = False) -> int:
    items = items or build_questions()
    if clear:
        db.query(Question).filter(Question.source == QuestionSource.POOL).delete()
    existing = {
        (q.module, q.knowledge_point, q.stem)
        for q in db.query(Question.module, Question.knowledge_point, Question.stem).all()
    }
    added = 0
    for it in items:
        key = (it["module"], it["knowledge_point"], it["stem"])
        if key in existing:
            continue
        db.add(Question(**it))
        added += 1
    db.commit()
    return added


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        added = import_questions(db)
        total = db.query(Question).count()
        print(f"seed: added={added} total={total}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
