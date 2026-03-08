def test_guest_login_creates_guest(client):
    r = client.post("/api/identity/guest")
    assert r.status_code == 200
    body = r.json()
    assert body["is_guest"] is True
    assert body["unionid"].startswith("guest_")
    assert body["aigc_notice_acked"] is False


def test_wechat_login_upsert_and_idempotent(client):
    r = client.post("/api/identity/wechat-login", json={"unionid": "u_real_1"})
    assert r.status_code == 200
    assert r.json()["is_guest"] is False
    # 重复登录不新建
    r2 = client.post("/api/identity/wechat-login", json={"unionid": "u_real_1"})
    assert r2.json()["candidate_id"] == r.json()["candidate_id"]


def test_ack_aigc_sets_flag(client):
    gid = client.post("/api/identity/guest").json()["unionid"]
    r = client.post("/api/identity/ack-aigc", headers={"X-Unionid": gid})
    assert r.status_code == 200
    assert r.json()["aigc_notice_acked"] is True


def test_me_requires_header(client):
    assert client.get("/api/identity/me").status_code == 401


def test_delete_data_removes_candidate(client, db_session):
    gid = client.post("/api/identity/guest").json()["unionid"]
    from app.models import Candidate

    assert db_session.query(Candidate).filter(Candidate.unionid == gid).count() == 1
    r = client.delete("/api/identity/data", headers={"X-Unionid": gid})
    assert r.status_code == 200
    assert db_session.query(Candidate).filter(Candidate.unionid == gid).count() == 0
