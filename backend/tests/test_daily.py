"""票 10：每日任务与考期倒计时。"""

import json
from datetime import datetime, timedelta, timezone

from app.models import Candidate, DailyTask, Question, QuestionSource, QuestionType
from app.seed.import_questions import import_questions


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def _mk_items(kp: str, n: int = 1) -> list[dict]:
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


def test_set_exam_date_and_countdown(client, db_session):
    """可输入考期并展示倒计时（验收 1）。"""
    uid = _guest(client)
    r = client.post(
        "/api/daily/exam-date", json={"exam_date": "2026-06-16"}, headers={"X-Unionid": uid}
    )
    assert r.status_code == 200
    assert r.json()["exam_date"] == "2026-06-16"
    assert r.json()["countdown_days"] >= 0


def test_exam_date_invalid_format(client):
    uid = _guest(client)
    r = client.post(
        "/api/daily/exam-date", json={"exam_date": "not-a-date"}, headers={"X-Unionid": uid}
    )
    assert r.status_code == 400


def test_exam_date_requires_identity(client):
    assert client.post("/api/daily/exam-date", json={"exam_date": "2026-06-16"}).status_code == 401


def test_exam_date_content_safety_rejected(client, monkeypatch):
    """考期输入接入内容安全检测调用点（验收 5，真实拦截在票 13）。"""
    from app.routers import daily as daily_router

    class RejectClient:
        def check_input(self, text: str) -> bool:
            return False

    monkeypatch.setattr(daily_router, "get_content_safety", lambda: RejectClient())
    uid = _guest(client)
    r = client.post(
        "/api/daily/exam-date", json={"exam_date": "2026-06-16"}, headers={"X-Unionid": uid}
    )
    assert r.status_code == 400


def test_daily_task_contains_mistake_review_and_new_questions(client, db_session):
    """每日任务含错题复习（来自票 09）与新题（验收 2）。"""
    import_questions(db_session, items=_mk_items("学生观") + _mk_items("教师观"))
    uid = _guest(client)

    # 制造一条错题
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "学生观", "question_count": 1},
        headers={"X-Unionid": uid},
    )
    q = r.json()["questions"][0]
    client.post(
        "/api/sessions/submit",
        json={
            "session_id": r.json()["session_id"],
            "answers": [{"question_id": q["id"], "selected": ["B"]}],
        },
        headers={"X-Unionid": uid},
    )

    client.post(
        "/api/daily/exam-date", json={"exam_date": "2026-06-16"}, headers={"X-Unionid": uid}
    )
    d = client.get("/api/daily", headers={"X-Unionid": uid}).json()
    assert d["exam_date"] == "2026-06-16"
    assert d["countdown_days"] >= 0
    assert d["task"]["task_date"]  # 当日任务已生成
    assert any(i["question_id"] == q["id"] for i in d["task"]["items"]["mistake_review"])
    assert len(d["task"]["items"]["new_questions"]) >= 1
    assert d["task"]["valid_hours"] == 12  # 当日有效时限（验收 3）
    assert d["task"]["completed"] is False


def test_daily_task_idempotent_same_day(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    d1 = client.get("/api/daily", headers={"X-Unionid": uid}).json()
    d2 = client.get("/api/daily", headers={"X-Unionid": uid}).json()
    assert d1["task"]["task_id"] == d2["task"]["task_id"]


def test_complete_task_feedback(client, db_session):
    """完成后展示明确完成反馈（验收 4），且幂等。"""
    import_questions(db_session)
    uid = _guest(client)
    client.post(
        "/api/daily/exam-date", json={"exam_date": "2026-06-16"}, headers={"X-Unionid": uid}
    )
    d = client.get("/api/daily", headers={"X-Unionid": uid}).json()
    tid = d["task"]["task_id"]

    r = client.post("/api/daily/complete", json={"task_id": tid}, headers={"X-Unionid": uid})
    assert r.status_code == 200
    assert r.json()["completed"] is True
    assert r.json()["feedback"]  # 明确完成反馈

    r2 = client.post("/api/daily/complete", json={"task_id": tid}, headers={"X-Unionid": uid})
    assert r2.status_code == 200
    assert r2.json()["completed"] is True


def test_task_expired_cannot_complete(client, db_session):
    """任务当日有效时限 12 小时，超时未算完成（验收 3）。"""
    import_questions(db_session)
    uid = _guest(client)
    d = client.get("/api/daily", headers={"X-Unionid": uid}).json()
    tid = d["task"]["task_id"]

    # 直接把任务创建时间回拨 13 小时（模拟跨过 12h 时限）
    task = db_session.get(DailyTask, tid)
    task.created_at = datetime.now(timezone.utc) - timedelta(hours=13)
    db_session.add(task)
    db_session.commit()

    r = client.post("/api/daily/complete", json={"task_id": tid}, headers={"X-Unionid": uid})
    assert r.status_code == 410
    assert "超时" in r.json()["detail"]


def test_complete_requires_identity(client):
    assert client.post("/api/daily/complete", json={"task_id": 1}).status_code == 401
