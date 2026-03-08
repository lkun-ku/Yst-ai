from app.seed.import_questions import import_questions


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def test_start_requires_identity(client):
    r = client.post("/api/sessions/start", json={"module": "职业理念"})
    assert r.status_code == 401


def test_start_requires_module_or_kp(client, db_session):
    uid = _guest(client)
    r = client.post("/api/sessions/start", json={}, headers={"X-Unionid": uid})
    assert r.status_code == 400


def test_start_by_module(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start",
        json={"module": "职业理念", "question_count": 5},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["question_count"] == 5
    assert len(body["questions"]) == 5
    assert all(q["module"] == "职业理念" for q in body["questions"])
    ids = [q["id"] for q in body["questions"]]
    assert len(set(ids)) == 5  # 同局不重复
    q0 = body["questions"][0]
    assert "answer" in q0 and "explanation" in q0 and q0["aigc_flag"] is True


def test_start_by_knowledge_point(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "教育观", "question_count": 2},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200
    assert all(q["knowledge_point"] == "教育观" for q in r.json()["questions"])


def test_start_insufficient_pool(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    # 单考点只有 2 题，请求 10 => 409
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "教育观", "question_count": 10},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 409
