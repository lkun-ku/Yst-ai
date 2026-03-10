import json

from app.models import (
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
)
from app.seed.import_questions import import_questions


def _start(client, db_session, module="职业理念", count=3):
    import_questions(db_session)
    uid = client.post("/api/identity/guest").json()["unionid"]
    r = client.post(
        "/api/sessions/start",
        json={"module": module, "question_count": count},
        headers={"X-Unionid": uid},
    )
    return uid, r.json()


def test_submit_all_correct(client, db_session):
    uid, body = _start(client, db_session, count=3)
    sid = body["session_id"]
    answers = [
        {"question_id": q["id"], "selected": json.loads(q["answer"])}
        for q in body["questions"]
    ]
    r = client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["idempotent"] is False
    assert all(res["is_correct"] for res in out["results"])
    assert out["new_mistakes"] == []
    assert "职业理念" in out["mastery"]


def test_submit_wrong_creates_mistake(client, db_session):
    uid, body = _start(client, db_session, count=2)
    sid = body["session_id"]
    answers = []
    for q in body["questions"]:
        correct = json.loads(q["answer"])
        wrong = ["B"] if correct != ["B"] else ["C"]
        answers.append({"question_id": q["id"], "selected": wrong})
    r = client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    out = r.json()
    assert all(not res["is_correct"] for res in out["results"])
    assert len(out["new_mistakes"]) == 2
    assert out["results"][0]["positive_note"]


def test_submit_idempotent(client, db_session):
    uid, body = _start(client, db_session, count=2)
    sid = body["session_id"]
    answers = [
        {"question_id": q["id"], "selected": json.loads(q["answer"])}
        for q in body["questions"]
    ]
    r1 = client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    r2 = client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    assert r1.json()["idempotent"] is False
    assert r2.json()["idempotent"] is True
    assert r2.json()["new_mistakes"] == []


def test_submit_multiple_four_state(client, db_session):
    import_questions(db_session)
    q = Question(
        module=Module.CULTURE_LITERACY,
        knowledge_point="四书五经_多选测试",
        stem="多选示例",
        options=json.dumps(
            [
                {"key": "A", "text": "a"},
                {"key": "B", "text": "b"},
                {"key": "C", "text": "c"},
                {"key": "D", "text": "d"},
            ]
        ),
        answer=json.dumps(["A", "B"]),
        explanation="e",
        type=QuestionType.MULTIPLE,
        source=QuestionSource.POOL,
        proofread_status=ProofreadStatus.PASSED,
    )
    db_session.add(q)
    db_session.commit()
    db_session.refresh(q)

    uid = client.post("/api/identity/guest").json()["unionid"]
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "四书五经_多选测试", "question_count": 1},
        headers={"X-Unionid": uid},
    )
    sid = r.json()["session_id"]
    r2 = client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": [{"question_id": q.id, "selected": ["A"]}]},
        headers={"X-Unionid": uid},
    )
    res = r2.json()["results"][0]
    assert res["state"] == "partial"
    assert res["options_state"]["A"] == "correct_selected"
    assert res["options_state"]["B"] == "missed"
    assert res["options_state"]["C"] == "wrong_not_selected"
    assert res["options_state"]["D"] == "wrong_not_selected"
