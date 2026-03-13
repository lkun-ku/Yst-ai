"""票 12：额度与 VIP 权限（Implementation 31：按题量限速，不对内容设限）。"""

import json
from datetime import datetime, timedelta, timezone

from app.models import Session as Sess
from app.seed.import_questions import import_questions


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def test_quota_accumulates_across_sessions(client, db_session):
    """每日 20 题跨局累计，用尽后友好提示（验收 1/2）。"""
    import_questions(db_session)
    uid = _guest(client)

    q = client.get("/api/quota", headers={"X-Unionid": uid}).json()
    assert q["free_daily_limit"] == 20
    assert q["used_today"] == 0 and q["remaining"] == 20

    client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 10}, headers={"X-Unionid": uid})
    q = client.get("/api/quota", headers={"X-Unionid": uid}).json()
    assert q["used_today"] == 10 and q["remaining"] == 10

    client.post("/api/sessions/start", json={"module": "职业道德", "question_count": 10}, headers={"X-Unionid": uid})
    q = client.get("/api/quota", headers={"X-Unionid": uid}).json()
    assert q["used_today"] == 20 and q["remaining"] == 0

    r = client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 1}, headers={"X-Unionid": uid})
    assert r.status_code == 429
    assert "明天" in r.json()["detail"]
    assert "VIP" in r.json()["detail"]


def test_quota_partial_within_limit_allowed(client, db_session):
    """未超限时可部分拉取（跨局累计而非按局限制）。"""
    import_questions(db_session)
    uid = _guest(client)
    client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 15}, headers={"X-Unionid": uid})
    r = client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 5}, headers={"X-Unionid": uid})
    assert r.status_code == 200  # 15+5=20 恰好用满


def test_quota_resets_next_day(client, db_session):
    """重置规则：按自然日（验收 1）。"""
    import_questions(db_session)
    uid = _guest(client)
    client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 20}, headers={"X-Unionid": uid})
    assert client.get("/api/quota", headers={"X-Unionid": uid}).json()["remaining"] == 0

    cid = client.get("/api/identity/me", headers={"X-Unionid": uid}).json()["candidate_id"]
    for sess in db_session.query(Sess).filter(Sess.candidate_id == cid).all():
        sess.created_at = datetime.now(timezone.utc) - timedelta(days=1)  # 回拨到昨天 → 额度重置
    db_session.commit()

    q = client.get("/api/quota", headers={"X-Unionid": uid}).json()
    assert q["used_today"] == 0 and q["remaining"] == 20


def test_vip_unlimited(client, db_session):
    """VIP 状态解锁不限量刷题（验收 3）。"""
    import_questions(db_session)
    uid = _guest(client)
    client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 20}, headers={"X-Unionid": uid})

    client.post("/api/quota/vip-activate", headers={"X-Unionid": uid})
    assert client.get("/api/quota", headers={"X-Unionid": uid}).json()["is_vip"] is True

    r = client.post("/api/sessions/start", json={"module": "职业理念", "question_count": 10}, headers={"X-Unionid": uid})
    assert r.status_code == 200  # VIP 不限量


def test_free_can_access_all_modules(client, db_session):
    """免费全内容可刷：所有模块均可发起（Implementation 31，只限速不设限）。"""
    import_questions(db_session)
    uid = _guest(client)
    for m in ["职业理念", "职业道德", "教育法律法规", "文化素养", "基本能力"]:
        r = client.post("/api/sessions/start", json={"module": m, "question_count": 2}, headers={"X-Unionid": uid})
        assert r.status_code == 200, f"模块 {m} 免费不可刷"


def test_vip_activate_requires_identity(client):
    assert client.post("/api/quota/vip-activate").status_code == 401


def test_quota_requires_identity(client):
    assert client.get("/api/quota").status_code == 401
