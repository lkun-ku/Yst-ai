"""票 11：历史闯关局回看（D3 决策：纳入 MVP）。"""

import json

from app.seed.import_questions import import_questions


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def _start_submit(client, uid: str, module: str = "职业理念", count: int = 3, correct: bool = True):
    r = client.post(
        "/api/sessions/start",
        json={"module": module, "question_count": count},
        headers={"X-Unionid": uid},
    )
    sid = r.json()["session_id"]
    answers = []
    for q in r.json()["questions"]:
        ans = json.loads(q["answer"])
        sel = ans if correct else (["B"] if ans != ["B"] else ["C"])
        answers.append({"question_id": q["id"], "selected": sel})
    client.post(
        "/api/sessions/submit",
        json={"session_id": sid, "answers": answers},
        headers={"X-Unionid": uid},
    )
    return sid


def test_history_list_basic(client, db_session):
    """返回已提交闯关局列表，含日期、模块、掌握度概览（验收 1）。"""
    import_questions(db_session)
    uid = _guest(client)

    _start_submit(client, uid, module="职业理念", count=3, correct=True)
    _start_submit(client, uid, module="职业道德", count=3, correct=False)

    h = client.get("/api/sessions/history", headers={"X-Unionid": uid}).json()
    assert len(h) == 2  # 数据随闯关局累积（验收 3）
    # 按时间倒序：最新在前
    assert h[0]["session_id"] > h[1]["session_id"]
    for item in h:
        assert item["created_at"]
        assert item["submitted_at"]
        assert item["module"] in ("职业理念", "职业道德")
        assert item["question_count"] == 3
        assert "mastery_overview" in item  # 掌握度概览
        assert set(item["mastery_overview"].keys()) <= {"职业理念", "职业道德"}


def test_history_correct_count(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    _start_submit(client, uid, count=3, correct=True)
    _start_submit(client, uid, count=3, correct=False)

    h = client.get("/api/sessions/history", headers={"X-Unionid": uid}).json()
    by_correct = sorted(h, key=lambda x: -x["correct_count"])
    assert by_correct[0]["correct_count"] == 3
    assert by_correct[1]["correct_count"] == 0


def test_history_excludes_unsubmitted(client, db_session):
    """未提交的局不算历史回看（复盘语义）。"""
    import_questions(db_session)
    uid = _guest(client)
    client.post(
        "/api/sessions/start",
        json={"module": "职业理念", "question_count": 3},
        headers={"X-Unionid": uid},
    )
    h = client.get("/api/sessions/history", headers={"X-Unionid": uid}).json()
    assert h == []


def test_history_scoped_to_candidate(client, db_session):
    import_questions(db_session)
    uid1 = _guest(client)
    uid2 = _guest(client)
    _start_submit(client, uid1, count=3)

    h = client.get("/api/sessions/history", headers={"X-Unionid": uid2}).json()
    assert h == []


def test_history_review_reuses_report(client, db_session):
    """回看任意一局完整复盘报告：复用票 08 报告结构（验收 2）。"""
    import_questions(db_session)
    uid = _guest(client)
    sid = _start_submit(client, uid, count=3, correct=False)

    rev = client.get(f"/api/review/{sid}", headers={"X-Unionid": uid}).json()
    assert set(rev["mastery"].keys())  # 五维掌握度
    assert "weak_points" in rev and "next_step" in rev
    assert "仅供参考" in rev["paragraph"]
    assert rev["aigc_flag"] is True


def test_history_requires_identity(client):
    assert client.get("/api/sessions/history").status_code == 401
