"""票 14：实时生成管线接入。测试一律用 FakeLLMClient，不消耗 API 额度（测试决策 33/45）。

真实模式（用户决策 2026-03-04）下 fake 路径断言不成立，相关测试跳过。
"""

import json

import pytest

from app.config import settings

_skip_real = pytest.mark.skipif(
    settings.llm_mode == "real",
    reason="真实模式下默认客户端为 real（用户决策 2026-03-04），fake 路径断言跳过",
)

from app.models import (
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
    RealtimeUsage,
    Session as Sess,
)
from app.pipeline.batch_generate import batch_generate
from app.seed.import_questions import import_questions
from app.services.llm_client import FakeLLMClient, get_llm_client


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
                "options": json.dumps([{"key": "A", "text": "对"}, {"key": "B", "text": "错"}]),
                "answer": json.dumps(["A"]),
                "explanation": "解析",
                "type": QuestionType.SINGLE,
                "source": QuestionSource.POOL,
                "proofread_status": ProofreadStatus.PASSED,  # 已过审校，可作备用题
                "aigc_flag": True,
                "version": 1,
            }
        )
    return items


@_skip_real
def test_fake_client_is_default_no_api_quota():
    """模型调用统一收口，默认假实现，不消耗额度（测试决策 33）。"""
    assert isinstance(get_llm_client(), FakeLLMClient)


def test_variant_prefers_pool_spare(client, db_session):
    """错题变式优先取同考点备用题，不触发实时（Implementation 13）。"""
    import_questions(db_session, items=_mk_items("学生观·变式", n=2))
    uid = _guest(client)
    q = db_session.query(Question).filter(Question.knowledge_point == "学生观·变式").all()

    r = client.post(f"/api/mistakes/{q[0].id}/variant", headers={"X-Unionid": uid})
    assert r.status_code == 200
    assert r.json()["degraded"] is False
    assert r.json()["question"]["id"] == q[1].id  # 同考点另一题
    from datetime import date as _date

    from app.services.realtime import get_today_usage

    cid = client.get("/api/identity/me", headers={"X-Unionid": uid}).json()["candidate_id"]
    assert get_today_usage(db_session, cid, _date.today()) == 0  # 未消耗实时额度


def _reset_kp(db_session, kp: str, n: int = 1):
    """清掉该考点已有题目后重建 n 题（kp 必须在考纲白名单内，产物才能过结构化校验）。"""
    db_session.query(Question).filter(Question.knowledge_point == kp).delete()
    db_session.commit()
    import_questions(db_session, items=_mk_items(kp, n=n))


@_skip_real
def test_variant_realtime_when_pool_empty(client, db_session):
    """池空才触发实时生成，产物入库并通过结构化校验。"""
    _reset_kp(db_session, "学生观", n=1)
    uid = _guest(client)
    q = db_session.query(Question).filter(Question.knowledge_point == "学生观").one()

    r = client.post(f"/api/mistakes/{q.id}/variant", headers={"X-Unionid": uid})
    assert r.status_code == 200
    assert r.json()["degraded"] is False
    new_id = r.json()["question"]["id"]
    assert new_id != q.id
    db_session.expire_all()
    assert db_session.get(Question, new_id).knowledge_point == "学生观"
    from datetime import date as _date

    from app.services.realtime import get_today_usage

    cid = client.get("/api/identity/me", headers={"X-Unionid": uid}).json()["candidate_id"]
    assert get_today_usage(db_session, cid, _date.today()) == 1  # 消耗 1 次实时额度


@_skip_real
def test_realtime_daily_limit_3_degrade_silently(client, db_session):
    """每人每日实时上限 3 次，超出降级纯池化，考生无感（Implementation 14）。

    真实模式下依赖外部 LLM 服务可用性（服务波动即误报），与 fake 路径测试一致跳过。
    """
    """每人每日实时上限 3 次，超出降级纯池化，考生无感（Implementation 14）。"""
    _reset_kp(db_session, "学生观", n=1)
    import_questions(db_session, items=_mk_items("备用考点", n=2))
    uid = _guest(client)
    q = db_session.query(Question).filter(Question.knowledge_point == "学生观").one()

    for i in range(3):
        r = client.post(f"/api/mistakes/{q.id}/variant", headers={"X-Unionid": uid})
        assert r.status_code == 200 and r.json()["degraded"] is False

    r4 = client.post(f"/api/mistakes/{q.id}/variant", headers={"X-Unionid": uid})
    assert r4.status_code == 200
    assert r4.json()["degraded"] is True  # 第 4 次降级，不报错
    cid = client.get("/api/identity/me", headers={"X-Unionid": uid}).json()["candidate_id"]
    from datetime import date

    from app.services.realtime import get_today_usage

    assert get_today_usage(db_session, cid, date.today()) == 3  # 额度未超


@_skip_real
def test_review_paragraph_realtime_and_degrade(client, db_session):
    """复盘个性化段落由模型写；缺失/超限时优雅降级为模板（票 08 协同）。"""
    import_questions(db_session)
    uid = _guest(client)
    r = client.post(
        "/api/sessions/start", json={"module": "职业理念", "question_count": 3},
        headers={"X-Unionid": uid},
    )
    sid = r.json()["session_id"]
    answers = [{"question_id": x["id"], "selected": json.loads(x["answer"])} for x in r.json()["questions"]]
    client.post("/api/sessions/submit", json={"session_id": sid, "answers": answers}, headers={"X-Unionid": uid})

    rev = client.get(f"/api/review/{sid}", headers={"X-Unionid": uid}).json()
    assert rev["paragraph"].startswith("[fake-paragraph]")  # 模型写的个性化段落
    assert "仅供参考" in rev["paragraph"]  # AIGC 标识不丢

    # 超限后降级为模板段落
    from datetime import date

    from app.services.realtime import get_today_usage, increment_usage

    cid = client.get("/api/identity/me", headers={"X-Unionid": uid}).json()["candidate_id"]
    for _ in range(3):
        increment_usage(db_session, cid, date.today())
    assert get_today_usage(db_session, cid, date.today()) >= 3  # 首次复盘已耗 1 次 + 补 3 次

    rev2 = client.get(f"/api/review/{sid}", headers={"X-Unionid": uid}).json()
    assert rev2["paragraph"].startswith("本次闯关已生成复盘")  # 模板降级


def test_pool_stats_and_alarm(client, db_session):
    """池化率可观测：实时占比 >5% 告警（Implementation 17）。"""
    import_questions(db_session)
    # 清空共享库中的当日用量与下发记录，使监控断言只受本测试数据影响
    db_session.query(RealtimeUsage).delete()
    db_session.query(Sess).delete()
    db_session.commit()

    stats = client.get("/api/admin/pool-stats", headers={"X-Admin-Token": "dev-admin"}).json()
    assert stats["alarm"] is False  # 纯池化，无告警
    assert stats["pool_ratio"] >= 0.95

    # 注入大量实时用量 → 占比超 5% → 告警
    from datetime import date

    from app.services.realtime import increment_usage

    uid = _guest(client)
    cid = client.get("/api/identity/me", headers={"X-Unionid": uid}).json()["candidate_id"]
    for _ in range(3):
        increment_usage(db_session, cid, date.today())

    stats2 = client.get("/api/admin/pool-stats", headers={"X-Admin-Token": "dev-admin"}).json()
    assert stats2["realtime_today"] == 3
    assert stats2["alarm"] is True


def test_batch_generate_expands_pool(db_session):
    """闲时批量生成脚本扩充题目池，校验不通过即弃。"""
    before = db_session.query(Question).count()
    stats = batch_generate(db_session, per_kp=1, client=FakeLLMClient())
    after = db_session.query(Question).count()

    assert stats["generated"] == 150  # 5 模块 × 30 考点
    assert stats["rejected"] == 0
    assert after - before == 150
    # 产物为池化供给、待审校状态
    sample = db_session.query(Question).order_by(Question.id.desc()).first()
    assert sample.source == QuestionSource.POOL
    assert sample.aigc_flag is True


def test_batch_generate_rejects_invalid(db_session, monkeypatch):
    """结构化校验不通过即弃（Implementation 19-20 在管线同样生效）。"""
    from app.services import llm_client as llm_mod

    class BadClient(FakeLLMClient):
        def generate(self, req):
            res = super().generate(req)
            if req.kind == "variant" and res.payload:
                res.payload["explanation"] = ""  # 解析为空 → 校验不通过
            return res

    monkeypatch.setattr(llm_mod, "get_llm_client", lambda: BadClient())
    stats = batch_generate(db_session, per_kp=1, client=BadClient())
    assert stats["generated"] == 0
    assert stats["rejected"] == 150


# ---------------- P0 止血：占位题不得进官方池 ----------------
#
# 背景：LLM_MODE 默认 fake，此模式下 FakeLLMClient 的变式题是「同模板占位题」
# （题干形如「（实时变式1）下列关于《XX》的表述，正确的是？」），一旦以
# proofread_status=PENDING 写入官方池会被抽题命中（routers/sessions.py 只过滤
# REJECTED），用户将直接刷到废题。故两个 CLI 入口都必须默认拒绝。

def test_batch_generate_cli_refuses_fake_mode(capsys, monkeypatch):
    """fake 模式下 CLI 必须拒绝执行，且不写入任何题目。"""
    from app.pipeline import batch_generate as bg

    monkeypatch.setattr(bg.settings, "llm_mode", "fake")
    assert bg.main(["--per-kp", "1"]) == 2
    out = capsys.readouterr().out
    assert "拒绝执行" in out
    assert "占位模板题" in out


def test_batch_generate_cli_allows_real_mode(monkeypatch):
    """安全闸不得误伤真实模式：LLM_MODE=real 时放行。

    用桩替换落库与 DB 会话，避免真调外部 API（本用例只验证闸门判定）。
    """
    from app import db as db_mod
    from app.pipeline import batch_generate as bg

    class _DummyQuery:
        def count(self) -> int:
            return 0

    class _DummyDB:
        def query(self, *args, **kwargs) -> _DummyQuery:
            return _DummyQuery()

        def close(self) -> None:
            pass

    monkeypatch.setattr(bg.settings, "llm_mode", "real")
    monkeypatch.setattr(bg, "batch_generate", lambda db, per_kp=1: {"generated": 0, "rejected": 0})
    monkeypatch.setattr(db_mod, "init_db", lambda: None)
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _DummyDB())

    assert bg.main(["--per-kp", "1"]) == 0


def test_seed_cli_refuses_placeholder(capsys):
    """种子脚本产物是占位模板题，CLI 必须默认拒绝导入。"""
    from app.seed import import_questions as iq

    assert iq.main([]) == 2
    out = capsys.readouterr().out
    assert "拒绝执行" in out
    assert "import_real_bank" in out  # 提示了正确的重建路径
