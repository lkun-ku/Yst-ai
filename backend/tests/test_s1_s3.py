"""S1 连胜补签卡 + S3 模考 的集成测试。"""
import json
from datetime import date, datetime, timedelta

from app.models import DailyTask, StreakMakeup
from app.routers.daily import _required_ids
from app.routers.streak import get_or_create


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def _task_id(client, h):
    daily = client.get("/api/daily", headers=h).json()
    task = daily.get("task") or {}
    return task.get("task_id") or task.get("id")


def _mark_all_done(db_session, cid):
    """把今日任务要求的题目全部标记为已作答（模拟用户真的做完了）。"""
    task = (
        db_session.query(DailyTask)
        .filter(DailyTask.candidate_id == cid, DailyTask.task_date == date.today().isoformat())
        .first()
    )
    if task:
        task.done_ids = json.dumps(_required_ids(json.loads(task.items or "{}")))
        db_session.commit()


def _complete(client, h, db_session, cid):
    # 顺序要紧：先 GET /api/daily 让任务被创建，再标记进度，最后完成
    _task_id(client, h)
    _mark_all_done(db_session, cid)
    return client.post("/api/daily/complete", json={"task_id": _task_id(client, h)}, headers=h)


# ---------------- 每日任务完成校验（逻辑漏洞修复） ----------------

def test_complete_requires_actual_progress(client, db_session):
    """未做任何任务题直接点完成 → 拒绝。"""
    uid = _guest(client)
    h = {"X-Unionid": uid}
    tid = _task_id(client, h)
    assert tid

    r = client.post("/api/daily/complete", json={"task_id": tid}, headers=h)
    assert r.status_code == 400
    assert "未完成" in r.json()["detail"]


def test_can_complete_reflects_progress(client, db_session):
    """GET /api/daily 的 can_complete 随进度变化。"""
    uid = _guest(client)
    h = {"X-Unionid": uid}
    cid = client.get("/api/identity/me", headers=h).json()["candidate_id"]

    d = client.get("/api/daily", headers=h).json()
    task = d["task"]
    assert task["required_count"] >= 0
    assert task["done_count"] == 0
    # 无任务题时允许完成；有任务题时未完成应为 False
    assert task["can_complete"] == (task["required_count"] == 0)

    _mark_all_done(db_session, cid)
    d = client.get("/api/daily", headers=h).json()
    assert d["task"]["can_complete"] is True
    assert d["task"]["done_count"] == d["task"]["required_count"]


# ---------------- S1 连胜 ----------------

def test_complete_daily_advances_streak(client, db_session):
    uid = _guest(client)
    h = {"X-Unionid": uid}
    cid = client.get("/api/identity/me", headers=h).json()["candidate_id"]

    r = _complete(client, h, db_session, cid)
    assert r.status_code == 200, r.text

    s = client.get("/api/streak", headers=h).json()
    assert s["current"] == 1
    assert s["total_days"] == 1
    assert s["last_date"] == date.today().isoformat()


def test_completing_twice_same_day_is_idempotent(client, db_session):
    uid = _guest(client)
    h = {"X-Unionid": uid}
    cid = client.get("/api/identity/me", headers=h).json()["candidate_id"]

    assert _complete(client, h, db_session, cid).status_code == 200
    client.post("/api/daily/complete", json={"task_id": _task_id(client, h)}, headers=h)

    s = client.get("/api/streak", headers=h).json()
    assert s["current"] == 1


def test_makeup_requires_card(client, db_session):
    uid = _guest(client)
    h = {"X-Unionid": uid}
    cid = client.get("/api/identity/me", headers=h).json()["candidate_id"]
    _complete(client, h, db_session, cid)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = client.post("/api/streak/makeup", json={"date": yesterday}, headers=h)
    assert r.status_code == 400
    assert "补签卡" in r.json()["detail"]


def test_makeup_consumes_card_and_extends_streak(client, db_session):
    uid = _guest(client)
    h = {"X-Unionid": uid}
    cid = client.get("/api/identity/me", headers=h).json()["candidate_id"]
    _complete(client, h, db_session, cid)

    s = get_or_create(db_session, cid)
    s.cards = 1
    db_session.commit()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = client.post("/api/streak/makeup", json={"date": yesterday}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["current"] == 2
    assert r.json()["cards"] == 0


def test_makeup_same_date_twice_rejected(client, db_session):
    uid = _guest(client)
    h = {"X-Unionid": uid}
    cid = client.get("/api/identity/me", headers=h).json()["candidate_id"]
    _complete(client, h, db_session, cid)

    s = get_or_create(db_session, cid)
    s.cards = 3
    db_session.commit()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert client.post("/api/streak/makeup", json={"date": yesterday}, headers=h).status_code == 200
    r = client.post("/api/streak/makeup", json={"date": yesterday}, headers=h)
    assert r.status_code == 400
    assert "已补签" in r.json()["detail"]


# ---------------- S3 模考 ----------------

def test_start_mock_exam_returns_server_time_and_deadline(client, db_session):
    from app.seed.import_questions import import_questions

    import_questions(db_session)
    uid = _guest(client)
    h = {"X-Unionid": uid}

    r = client.post("/api/sessions/start", json={"mode": "mock", "question_count": 30}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "mock"
    assert d["duration_sec"] == 2700
    assert d["deadline_at"]
    assert d["server_now"]
    assert len(d["questions"]) > 0

    now = datetime.fromisoformat(d["server_now"])
    dl = datetime.fromisoformat(d["deadline_at"])
    delta = (dl - now).total_seconds()
    assert 2600 <= delta <= 2800


def test_mock_exam_covers_multiple_modules_by_weight(client, db_session):
    from app.seed.import_questions import import_questions

    import_questions(db_session)
    uid = _guest(client)
    h = {"X-Unionid": uid}

    r = client.post("/api/sessions/start", json={"mode": "mock", "question_count": 30}, headers=h)
    assert r.status_code == 200
    qs = r.json()["questions"]
    modules = {q["module"] for q in qs}
    assert len(modules) >= 2


def test_normal_session_has_no_deadline(client, db_session):
    from app.seed.import_questions import import_questions

    import_questions(db_session)
    uid = _guest(client)
    h = {"X-Unionid": uid}

    r = client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 3}, headers=h)
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "normal"
    assert d["duration_sec"] == 0
    assert d["deadline_at"] is None


def test_submit_mock_exam_ok(client, db_session):
    from app.seed.import_questions import import_questions

    import_questions(db_session)
    uid = _guest(client)
    h = {"X-Unionid": uid}

    r = client.post("/api/sessions/start", json={"mode": "mock", "question_count": 10}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()

    answers = [{"question_id": q["id"], "selected": ["A"]} for q in d["questions"]]
    r = client.post("/api/sessions/submit", json={"session_id": d["session_id"], "answers": answers}, headers=h)
    assert r.status_code == 200, r.text
