from app.models import (
    Candidate,
    Mastery,
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
    Session,
    SessionStatus,
)


def test_six_core_tables_create_and_persist(db_session):
    # 考生
    cand = Candidate(unionid="u_test", is_guest=False)
    db_session.add(cand)
    db_session.commit()
    db_session.refresh(cand)
    assert cand.id is not None

    # 题目
    q = Question(
        module=Module.CULTURE_LITERACY,
        knowledge_point="kp1",
        stem="题干",
        options='[{"key":"A","text":"a"},{"key":"B","text":"b"}]',
        answer='["A"]',
        explanation="解析",
        type=QuestionType.SINGLE,
        source=QuestionSource.POOL,
        proofread_status=ProofreadStatus.PASSED,
    )
    db_session.add(q)
    db_session.commit()
    db_session.refresh(q)
    assert q.id is not None

    # 闯关局
    s = Session(candidate_id=cand.id, module=Module.CULTURE_LITERACY, question_ids="[1]", status=SessionStatus.STARTED)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    assert s.id is not None

    # 错题本
    from app.models import MistakeBook

    m = MistakeBook(candidate_id=cand.id, question_id=q.id, knowledge_point="kp1")
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    assert m.id is not None

    # 每日任务
    from app.models import DailyTask

    d = DailyTask(candidate_id=cand.id, task_date="2026-06-16", items="[]")
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    assert d.id is not None

    # 掌握度
    mastery = Mastery(candidate_id=cand.id, module=Module.CULTURE_LITERACY, score=0.0)
    db_session.add(mastery)
    db_session.commit()
    db_session.refresh(mastery)
    assert mastery.id is not None
