import json

from app.models import Module, Question, QuestionSource
from app.seed.import_questions import build_questions, import_questions


def test_build_questions_coverage_and_count():
    items = build_questions()
    assert len(items) >= 300
    assert set(Module) <= {it["module"] for it in items}
    # 每模块都有题
    per_module = {m: 0 for m in Module}
    for it in items:
        per_module[it["module"]] += 1
    assert all(v > 0 for v in per_module.values())


def test_seed_idempotent_and_schema_valid(db_session):
    items = build_questions()
    added1 = import_questions(db_session, items)
    assert added1 >= 300
    total1 = db_session.query(Question).count()
    # 重跑幂等
    added2 = import_questions(db_session, items)
    assert added2 == 0
    assert db_session.query(Question).count() == total1

    # schema 校验：答案唯一、选项 4 个、解析非空、考点存在、来源=pool
    for q in db_session.query(Question).filter(Question.source == QuestionSource.POOL).all():
        assert q.source == QuestionSource.POOL
        ans = json.loads(q.answer)
        assert len(ans) == 1
        opts = json.loads(q.options)
        assert len(opts) == 4
        assert q.explanation.strip()
        assert q.knowledge_point
