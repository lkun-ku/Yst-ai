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
    # #27：单考点只有 2 题，请求 10 => 按可用量降级开局（不再 409 硬失败）
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "教育观", "question_count": 10},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["question_count"] == 2
    assert len(body["questions"]) == 2


def test_start_empty_pool_409(client, db_session):
    """#27：范围内一题都没有时仍 409。"""
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "不存在的考点", "question_count": 2},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 409


def test_start_accepts_personal_questions(client, db_session):
    """#27：按考点抽题包含个人题（此前 source==POOL 把个人题全排除，重练个人考点必然 409）。"""
    from app.models import (
        Candidate,
        Document,
        Module,
        Question,
        QuestionSource,
        QuestionType,
    )

    uid = _guest(client)
    cand = db_session.query(Candidate).filter_by(unionid=uid).first()
    doc = Document(
        candidate_id=cand.id,
        title="测试资料",
        file_type="pdf",
        char_count=100,
        page_count=1,
        chunk_count=1,
        status="parsed",
        storage_path="tmp/a.pdf",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        Question(
            owner_candidate_id=cand.id,
            doc_id=doc.id,
            source=QuestionSource.DOC,
            module=Module.PERSONAL,
            knowledge_point="1.1 隐式转换",
            type=QuestionType.SINGLE,
            stem="题干",
            options='[{"key":"A","text":"a"},{"key":"B","text":"b"}]',
            answer='["A"]',
            explanation="解析",
        )
    )
    db_session.commit()

    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "1.1 隐式转换", "question_count": 2},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 200
    assert len(r.json()["questions"]) == 1  # 降级：仅 1 题可用


def test_start_excludes_others_personal_questions(client, db_session):
    """#27 越权防护：别人的个人题不可被按考点抽到。"""
    from app.models import (
        Candidate,
        Module,
        Question,
        QuestionSource,
        QuestionType,
    )

    uid = _guest(client)
    other = Candidate(unionid="other-user-unionid")
    db_session.add(other)
    db_session.flush()
    db_session.add(
        Question(
            owner_candidate_id=other.id,
            source=QuestionSource.DOC,
            module=Module.PERSONAL,
            knowledge_point="1.9 他人的考点",
            type=QuestionType.SINGLE,
            stem="他人题干",
            options='[{"key":"A","text":"a"},{"key":"B","text":"b"}]',
            answer='["A"]',
            explanation="解析",
        )
    )
    db_session.commit()

    r = client.post(
        "/api/sessions/start",
        json={"knowledge_point": "1.9 他人的考点", "question_count": 2},
        headers={"X-Unionid": uid},
    )
    assert r.status_code == 409  # 对当前用户不可见，池为空


def test_get_session_repull(client, db_session):
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start",
        json={"module": "职业理念", "question_count": 3},
        headers={"X-Unionid": uid},
    )
    body = r.json()
    sid = body["session_id"]
    r2 = client.get(f"/api/sessions/{sid}", headers={"X-Unionid": uid})
    assert r2.status_code == 200
    repull = r2.json()
    assert [q["id"] for q in repull["questions"]] == [q["id"] for q in body["questions"]]


def test_get_session_not_found(client, db_session):
    uid = _guest(client)
    assert client.get("/api/sessions/99999", headers={"X-Unionid": uid}).status_code == 404
