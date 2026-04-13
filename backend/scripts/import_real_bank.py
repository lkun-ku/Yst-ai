# -*- coding: utf-8 -*-
"""一次性入库：real_questions.json（管线产出 418 题）替换官方池占位假题。在 backend/ 下运行。"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())  # 在 backend/ 下运行

from app.db import SessionLocal, init_db
from app.models import ProofreadStatus, Question, QuestionSource, QuestionType
from app.seed.import_questions import import_questions

SRC = os.path.join(os.getcwd(), "app", "seed", "real_questions.json")

d = json.load(open(SRC, encoding="utf-8"))
raw = d["questions"]

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
        }
    )

init_db()
db = SessionLocal()
try:
    before = db.query(Question).filter(Question.source == QuestionSource.POOL).count()
    added = import_questions(db, items=items, clear=True)
    after = db.query(Question).filter(Question.source == QuestionSource.POOL).count()
    total = db.query(Question).count()
    print(f"POOL before={before} -> after={after} (added={added}) | 全库总数={total}")
    sample = db.query(Question).filter(Question.source == QuestionSource.POOL).first()
    print("抽样:", sample.module, "|", sample.knowledge_point, "|", sample.stem[:40])
finally:
    db.close()
