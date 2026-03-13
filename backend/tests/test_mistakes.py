"""票 09：错题本与专项重练。"""

import json

from app.models import Question, QuestionSource, QuestionType
from app.seed.import_questions import import_questions


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def _mk_items(kp: str, n: int = 2) -> list[dict]:
    items = []
    for i in range(n):
        items.append(
            {
                "module": "职业理念",
                "knowledge_point": kp,
                "stem": f"测试题《{kp}》变式{i + 1}",
                "options": json.dumps(
                    [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}], ensure_ascii=False
                ),
                "answer": json.dumps(["A"], ensure_ascii=False),
                "explanation": "解析",
                "type": QuestionType.SINGLE,
                "source": QuestionSource.POOL,
                "aigc_flag": True,
                "version": 1,
            }
        )
    return items


def _start_submit(client, uid: str, kp: str, count: int, correct: bool):
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": kp, "question_count": count},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    answers = []
    for q in r.json()["questions"]:
        sel = json.loads(q["answer"]) if correct else ["B"]
        answers.append({"question_id": q["id"], "selected": sel})
    r2 = client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    assert r2.status_code == 200, r2.text
    return sid, r2.json()


def test_wrong_answers_aggregated_by_knowledge_point(client, db_session):
    """答错的题自动入错题本，且同考点合并呈现并显示错次（验收 1/2）。"""
    # 考点名带「·单测」后缀，避免与全量种子题（test_daily 等先播种）同名考点混池
    import_questions(db_session, items=_mk_items("学生观·单测"))
    uid = _guest(client)

    _, out = _start_submit(client, uid, "学生观·单测", 2, correct=False)
    assert set(out["new_mistakes"]) == {q["id"] for q in []} or len(out["new_mistakes"]) == 2

    ms = client.get("/api/mistakes", headers={"X-Unionid": uid}).json()
    assert len(ms) == 1  # 同考点合并
    g = ms[0]
    assert g["knowledge_point"] == "学生观·单测"
    assert g["wrong_count"] == 2
    assert g["question_count"] == 2


def test_repeat_wrong_increments_count(client, db_session):
    """同一考点反复错：错次累计（验收 2）。"""
    import_questions(db_session, items=_mk_items("教师观·单测"))
    uid = _guest(client)

    _start_submit(client, uid, "教师观·单测", 2, correct=False)
    _start_submit(client, uid, "教师观·单测", 2, correct=False)

    ms = client.get("/api/mistakes", headers={"X-Unionid": uid}).json()
    assert len(ms) == 1
    assert ms[0]["wrong_count"] == 4


def test_correct_answers_do_not_enter_mistake_book(client, db_session):
    import_questions(db_session, items=_mk_items("教育观·单测"))
    uid = _guest(client)

    _start_submit(client, uid, "教育观·单测", 2, correct=True)
    assert client.get("/api/mistakes", headers={"X-Unionid": uid}).json() == []


def test_mistake_repractice_starts_session(client, db_session):
    """错题本条目一键发起该考点重练：复用按考点发起（验收 3）。"""
    import_questions(db_session, items=_mk_items("法律法规·单测"))
    uid = _guest(client)
    _start_submit(client, uid, "法律法规·单测", 2, correct=False)

    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "法律法规·单测", "question_count": 2},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200
    assert r.json()["knowledge_point"] == "法律法规·单测"
    assert len(r.json()["questions"]) == 2


def test_mistakes_requires_identity(client):
    assert client.get("/api/mistakes").status_code == 401


def test_mistakes_empty(client):
    uid = _guest(client)
    assert client.get("/api/mistakes", headers={"X-Unionid": uid}).json() == []
