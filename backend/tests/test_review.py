import json

from app.models import Module
from app.seed.import_questions import import_questions


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def _start_and_submit(client, db_session, module="职业理念", count=3, all_correct=True):
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start",
        json={"module": module, "question_count": count},
        headers={"X-Unionid": uid},
    )
    sid = r.json()["session_id"]
    answers = []
    for q in r.json()["questions"]:
        if all_correct:
            answers.append({"question_id": q["id"], "selected": json.loads(q["answer"])})
        else:
            correct = json.loads(q["answer"])
            wrong = ["B"] if correct != ["B"] else ["C"]
            answers.append({"question_id": q["id"], "selected": wrong})
    client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    return uid, sid


def test_review_structure(client, db_session):
    uid, sid = _start_and_submit(client, db_session, count=3, all_correct=False)
    rev = client.get(f"/api/review/{sid}", headers={"X-Unionid": uid}).json()
    assert set(rev["mastery"].keys()) == {m.value for m in Module}  # 五维
    assert len(rev["weak_points"]) <= 3
    assert rev["next_step"]
    assert rev["aigc_flag"] is True
    assert "仅供参考" in rev["paragraph"]


def test_review_requires_identity(client):
    assert client.get("/api/review/1").status_code == 401


def test_review_not_found(client, db_session):
    uid = _guest(client)
    assert client.get("/api/review/99999", headers={"X-Unionid": uid}).status_code == 404
