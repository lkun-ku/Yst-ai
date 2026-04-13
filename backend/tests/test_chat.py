"""#31 AI 模拟答全链路测试。

通过 app.dependency_overrides 注入 FakeLLMClient——无论 .env 是 fake 还是 real，
测试都跑 fake 分支，真实覆盖 start→追问→终评→finish→stats 全链路逻辑（零额度）。
"""

import json

import pytest

from app.deps import get_current_candidate
from app.main import app
from app.models import (
    Candidate,
    ChatSession,
    ChatTurn,
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
)
from app.routers.chat import get_llm_client_dep
from app.seed.import_questions import import_questions
from app.services.llm_client import FakeLLMClient, GenerationResult

CAND = 9501  # 专属子树


@pytest.fixture
def chat_client(client, db_session):
    """注入 fake LLM 与固定身份（覆盖 real 模式，保证测试零额度）。

    get_db 无需覆写：conftest 已把 DATABASE_URL 指向测试库。
    """
    app.dependency_overrides[get_llm_client_dep] = lambda: FakeLLMClient()
    app.dependency_overrides[get_current_candidate] = lambda: Candidate(id=CAND)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    """每个用例独立 seed：1 个专属 candidate + 6 道官方池真题（职业理念）。

    库是 session 级（_reset_db 只在整场跑一次），前后必须清理本子树数据，
    否则前一用例的 ChatTurn 残留会让跨场去重把题抽干。
    """

    def _cleanup():
        sess_ids = [
            r[0]
            for r in db_session.query(ChatSession.id).filter(ChatSession.candidate_id == CAND)
        ]
        if sess_ids:
            db_session.query(ChatTurn).filter(ChatTurn.session_id.in_(sess_ids)).delete(
                synchronize_session=False
            )
        db_session.query(ChatSession).filter(ChatSession.candidate_id == CAND).delete(
            synchronize_session=False
        )
        db_session.query(Question).filter(
            Question.knowledge_point.like("教育观·测试%")
        ).delete(synchronize_session=False)
        db_session.commit()

    _cleanup()
    cand = db_session.query(Candidate).filter_by(id=CAND).first()
    if cand is None:
        db_session.add(Candidate(id=CAND, unionid=f"uchat{CAND}"))
        db_session.commit()
    items = []
    for i in range(6):
        items.append(
            {
                "module": Module.PROFESSIONAL_IDEA,
                "knowledge_point": f"教育观·测试{i}",
                "stem": f"（测试题{i}）下列关于教育观的表述正确的是？",
                "options": json.dumps(
                    [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}], ensure_ascii=False
                ),
                "answer": json.dumps(["A"]),
                "explanation": "教育观的核心要点。",
                "type": QuestionType.SINGLE,
                "source": QuestionSource.POOL,
                "proofread_status": ProofreadStatus.PASSED,
                "aigc_flag": True,
                "version": 1,
            }
        )
    import_questions(db_session, items=items)


def _guest(client):
    return client.post("/api/identity/guest").json()["unionid"]


def _start(client):
    r = client.post(
        "/api/chat/start",
        json={"module": "职业理念", "difficulty": "medium", "persona": "coach"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_chat_start_opening_and_first_ask(chat_client, seeded):
    body = _start(chat_client)
    assert body["session_id"] > 0
    assert body["persona"] == "coach"
    types = [m["turn_type"] for m in body["messages"]]
    assert types == ["opening", "ask"]
    assert "面试教练" in body["messages"][0]["content"] or "教练" in body["messages"][0]["content"]
    assert body["messages"][1]["question_id"] is not None


def test_chat_full_flow_probe_then_final(chat_client, seeded):
    """第一问：模糊回答→追问；再答→终评得分。"""
    body = _start(chat_client)
    sid = body["session_id"]

    r1 = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "教育就是培养人"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["type"] == "probe"  # fake 第一轮回追问
    assert b1["messages"][1]["turn_type"] == "probe"

    r2 = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "补充：结合情境举例说明"})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["type"] == "final"
    assert 0 <= b2["score"] <= 100
    assert b2["messages"][1]["turn_type"] == "feedback"
    assert b2["finished"] is False
    assert b2["messages"][2]["turn_type"] == "ask"  # 第二题已出


def test_chat_five_questions_auto_finish_and_stats(chat_client, seeded):
    """答满 5 题自动收官；finish 汇总与 stats 趋势可用。"""
    body = _start(chat_client)
    sid = body["session_id"]
    # fake 评分语义：每题第 1 轮必追问、第 2 轮终评 → 每题两轮，共 5 题
    final = None
    for i in range(5):
        r1 = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": f"第{i + 1}题初答"})
        assert r1.status_code == 200, r1.text
        assert r1.json()["type"] == "probe"
        r2 = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": f"第{i + 1}题补充完整要点"})
        assert r2.status_code == 200, r2.text
        b = r2.json()
        assert b["type"] == "final"
        final = b
    assert final["finished"] is True
    assert final["session_summary"]["question_count"] == 5
    assert len(final["session_summary"]["scores"]) == 5

    # finish 后重复结束应仍可幂等返回汇总
    r = chat_client.post("/api/chat/finish", json={"session_id": sid})
    assert r.status_code == 200
    assert r.json()["question_count"] == 5

    stats = chat_client.get("/api/chat/stats").json()
    assert stats["total_sessions"] == 1
    assert stats["total_questions"] == 5
    assert len(stats["recent"]) == 1

    history = chat_client.get("/api/chat/history").json()
    assert len(history) == 1
    assert history[0]["session_id"] == sid


def test_chat_off_topic_hint(chat_client, seeded):
    body = _start(chat_client)
    sid = body["session_id"]
    r = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "[offtopic]今天天气不错"})
    assert r.status_code == 200
    b = r.json()
    assert b["type"] == "hint"
    # 离题不消耗追问次数：正常回答仍走追问链
    r2 = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "教育是培养人的活动"})
    assert r2.json()["type"] == "probe"


def test_chat_reply_empty_and_overlong(chat_client, seeded):
    body = _start(chat_client)
    sid = body["session_id"]
    assert chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "  "}).status_code == 400
    assert (
        chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "x" * 501}).status_code
        == 400
    )


def test_chat_session_replay(chat_client, seeded):
    """会话恢复：重放整场对话，消息完整。"""
    body = _start(chat_client)
    sid = body["session_id"]
    chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "先答一轮"})

    r = chat_client.get(f"/api/chat/session/{sid}")
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "active"
    types = [m["turn_type"] for m in b["messages"]]
    assert types == ["opening", "ask", "user", "probe"]


def test_chat_llm_failure_503(chat_client, seeded):
    """评分失败显式 503，不静默给分、不落 turn（不 fail-open）。"""

    class BrokenClient(FakeLLMClient):
        def generate(self, req):
            return GenerationResult(text="", payload=None)  # 模拟 LLM 失败

    app.dependency_overrides[get_llm_client_dep] = lambda: BrokenClient()
    body = _start(chat_client)
    sid = body["session_id"]
    r = chat_client.post("/api/chat/reply", json={"session_id": sid, "content": "任何回答"})
    assert r.status_code == 503
    # 状态未变：重放仍只有开场与第一问
    replay = chat_client.get(f"/api/chat/session/{sid}").json()
    assert [m["turn_type"] for m in replay["messages"]] == ["opening", "ask"]


def test_chat_cross_session_dedup(chat_client, seeded):
    """跨场去重：第一场答过的题，第二场优先不重复。"""
    body = _start(chat_client)
    sid = body["session_id"]
    q1 = body["messages"][1]["question_id"]
    for i in range(5):
        chat_client.post("/api/chat/reply", json={"session_id": sid, "content": f"第{i + 1}题初答"})
        chat_client.post("/api/chat/reply", json={"session_id": sid, "content": f"第{i + 1}题补充"})
    body2 = _start(chat_client)
    q2 = body2["messages"][1]["question_id"]
    assert q2 != q1, "第二场第一题不应重复第一场已答的题"


def test_chat_start_rejects_invalid(chat_client, seeded):
    assert (
        chat_client.post(
            "/api/chat/start", json={"module": "不存在模块", "difficulty": "medium", "persona": "coach"}
        ).status_code
        == 400
    )
    assert (
        chat_client.post(
            "/api/chat/start", json={"module": "职业理念", "difficulty": "简单", "persona": "coach"}
        ).status_code
        == 400
    )
