"""AI 模拟答路由（#31）：聊天式问答训练，与选择题闯关体系完全独立。

接口：
- POST /api/chat/start         配置（模块/难度/人设）→ 开场白 + 第一问
- POST /api/chat/reply         用户自然语言回答 → 追问 / 终评（严格追问式评分链）
- POST /api/chat/finish        结束训练，返回本局汇总
- GET  /api/chat/session/{id}  重放对话（小程序切后台回收后恢复现场）
- GET  /api/chat/history       训练场历史列表
- GET  /api/chat/stats         趋势统计（近 10 场均分序列 + 累计）

约束（用户决策）：每场 5 题、每题追问 ≤2；评分失败显式 503 绝不 fail-open；
不与连胜/今日任务联动；内测期不限每日场次数。
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as ORMSession

from ..db import get_db
from ..deps import get_current_candidate
from ..models import (
    Candidate,
    ChatPack,
    ChatSession,
    ChatTurn,
    Module,
    ProofreadStatus,
    Question,
    QuestionSource,
)
from ..services.llm_client import LLMClient, get_llm_client

router = APIRouter(prefix="/api/chat")

MAX_QUESTIONS = 5
MAX_PROBES = 2
MAX_INPUT = 500
DIFFICULTIES = ("medium", "hard")
PERSONAS = ("coach", "examiner")

_OPENING = {
    "coach": (
        "你好呀，我是你的面试教练。接下来我们用聊天的方式过一遍高频考点，"
        "用自己的话说就行，说得不完整我会追问，咱们一起把它捋顺。准备好了，第一题来了——"
    ),
    "examiner": "面试开始。共 5 题，请直接作答，我会视情况追问。第一题——",
}


def get_llm_client_dep() -> LLMClient:
    """可覆写的 LLM 客户端依赖：测试用 app.dependency_overrides 注入 fake。"""
    return get_llm_client()


class ChatStartIn(BaseModel):
    module: str
    difficulty: str = "medium"
    persona: str = "coach"


class ChatReplyIn(BaseModel):
    session_id: int
    content: str


class ChatFinishIn(BaseModel):
    session_id: int


def _turn_out(t: ChatTurn) -> dict:
    return {
        "seq": t.seq,
        "role": t.role,
        "turn_type": t.turn_type,
        "content": t.content,
        "question_id": t.question_id,
        "score": t.score,
        "points_hit": json.loads(t.points_hit) if t.points_hit else [],
        "points_missed": json.loads(t.points_missed) if t.points_missed else [],
        "points_wrong": json.loads(t.points_wrong) if t.points_wrong else [],
        "suggestion": t.suggestion,
    }


def _own_session(db: ORMSession, c: Candidate, session_id: int) -> ChatSession:
    s = db.get(ChatSession, session_id)
    if s is None or s.candidate_id != c.id:
        raise HTTPException(404, "训练场不存在")
    return s


def _add_turn(db: ORMSession, s: ChatSession, role: str, turn_type: str, content: str, **kw) -> ChatTurn:
    seq = (db.query(ChatTurn).filter(ChatTurn.session_id == s.id).count() or 0) + 1
    t = ChatTurn(session_id=s.id, seq=seq, role=role, turn_type=turn_type, content=content, **kw)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _pick_question(db: ORMSession, c: Candidate, module: str) -> Question:
    """抽官方真题：优先排除该用户近期已练过的（跨场去重），池不足时放宽。"""
    base = db.query(Question).filter(
        Question.source == QuestionSource.POOL,
        Question.proofread_status == ProofreadStatus.PASSED,
        Question.module == module,
    )
    seen_ids = [
        r[0]
        for r in db.query(ChatTurn.question_id)
        .join(ChatSession, ChatSession.id == ChatTurn.session_id)
        .filter(
            ChatSession.candidate_id == c.id,
            ChatSession.module == module,
            ChatTurn.question_id.isnot(None),
        )
        .all()
    ]
    q = base.filter(~Question.id.in_(seen_ids)).order_by(Question.id) if seen_ids else base
    first = q.order_by(Question.id).first()
    if first is not None:
        return first
    first = base.order_by(Question.id).first()
    if first is None:
        raise HTTPException(409, "该模块暂无可练习的题目")
    return first


def _get_or_create_pack(db: ORMSession, llm: LLMClient, q: Question, difficulty: str) -> ChatPack:
    pack = db.query(ChatPack).filter(ChatPack.question_id == q.id).first()
    if pack is not None:
        return pack
    correct = ""
    try:
        opts = json.loads(q.options or "[]")
        ans = json.loads(q.answer or "[]")
        correct = "、".join(o.get("text", "") for o in opts if o.get("key") in ans)
    except Exception:
        pass
    from ..services.llm_client import GenerationRequest

    res = llm.generate(
        GenerationRequest(
            kind="chat_pack",
            knowledge_point=q.knowledge_point,
            context={
                "module": q.module if isinstance(q.module, str) else q.module.value,
                "stem": q.stem,
                "answer_text": correct,
                "explanation": q.explanation,
                "difficulty": difficulty,
            },
        )
    )
    payload = res.payload if res else None
    if not payload or not payload.get("open_question") or not payload.get("key_points"):
        raise HTTPException(503, "AI 包装服务暂时不可用，请稍后重试")
    pack = ChatPack(
        question_id=q.id,
        open_question=payload["open_question"],
        key_points=json.dumps(payload["key_points"], ensure_ascii=False),
        difficulty=payload.get("difficulty", difficulty),
    )
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


def _current_question(db: ORMSession, s: ChatSession) -> tuple[ChatTurn, ChatPack, int]:
    """定位当前未终评的题：最后一轮 ask 与已追问次数。"""
    turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.session_id == s.id)
        .order_by(ChatTurn.seq)
        .all()
    )
    last_ask = next((t for t in reversed(turns) if t.turn_type == "ask"), None)
    if last_ask is None:
        raise HTTPException(409, "当前没有待回答的问题")
    # 追问次数 = 该 ask 之后（不含）到末尾的 probe 轮数
    idx = turns.index(last_ask)
    probes = sum(1 for t in turns[idx + 1 :] if t.turn_type == "probe")
    # 终评已出且未被新一轮 ask 覆盖 → 场次已收尾
    if any(t.turn_type == "feedback" for t in turns[idx + 1 :]):
        raise HTTPException(409, "本题已出分，请继续下一题或结束训练")
    qid = last_ask.question_id
    pack = db.query(ChatPack).filter(ChatPack.question_id == qid).first() if qid else None
    if pack is None:
        raise HTTPException(500, "题目包装缺失")
    return last_ask, pack, probes


@router.post("/start")
def chat_start(
    body: ChatStartIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
):
    if body.module not in [m.value for m in Module]:
        raise HTTPException(400, "无效模块")
    if body.difficulty not in DIFFICULTIES:
        raise HTTPException(400, "无效难度")
    if body.persona not in PERSONAS:
        raise HTTPException(400, "无效人设")
    # 收尾该用户残留的 active 场（同一时间只允许一场对话）
    for stale in db.query(ChatSession).filter(
        ChatSession.candidate_id == c.id, ChatSession.status == "active"
    ):
        stale.status = "finished"
    db.commit()

    q = _pick_question(db, c, body.module)
    pack = _get_or_create_pack(db, llm, q, body.difficulty)
    s = ChatSession(
        candidate_id=c.id,
        module=Module(body.module),
        difficulty=body.difficulty,
        persona=body.persona,
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    opening = _add_turn(db, s, "ai", "opening", _OPENING[body.persona])
    ask = _add_turn(db, s, "ai", "ask", pack.open_question, question_id=q.id)
    return {
        "session_id": s.id,
        "persona": s.persona,
        "difficulty": s.difficulty,
        "module": s.module.value if hasattr(s.module, "value") else str(s.module),
        "messages": [_turn_out(opening), _turn_out(ask)],
        "progress": {"cur": 1, "total": MAX_QUESTIONS},
    }


@router.post("/reply")
def chat_reply(
    body: ChatReplyIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
):
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "回答不能为空")
    if len(content) > MAX_INPUT:
        raise HTTPException(400, f"回答请控制在 {MAX_INPUT} 字以内")

    s = _own_session(db, c, body.session_id)
    if s.status != "active":
        raise HTTPException(409, "本场训练已结束")
    ask, pack, probes = _current_question(db, s)
    key_points = json.loads(pack.key_points)

    # 本题的对话历史（开场白除外，供评分链参考上下文）
    turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.session_id == s.id)
        .order_by(ChatTurn.seq)
        .all()
    )
    history = [
        {"role": "user" if t.role == "user" else "ai", "content": t.content}
        for t in turns
        if t.turn_type in ("probe", "user") and t.seq > ask.seq
    ]

    res = llm.generate(
        _grade_request(
            open_question=pack.open_question,
            key_points=key_points,
            history=history,
            content=content,
            probes_used=probes,
            persona=s.persona,
        )
    )
    payload = res.payload if res else None
    if not payload:
        # 显式失败：不落任何 turn（本题状态不变，可重试），绝不静默给分
        raise HTTPException(503, "AI 评分暂时不可用，请稍后重试")

    user_turn = _add_turn(db, s, "user", "user", content)
    verdict = payload.get("verdict")

    if verdict == "off_topic":
        # hint 与 probe 区分类型：离题提示不消耗追问次数（#31 语义）
        hint = _add_turn(db, s, "ai", "hint", str(payload.get("hint", "请围绕刚才的问题作答哦")))
        return {
            "type": "hint",
            "messages": [_turn_out(user_turn), _turn_out(hint)],
            "progress": {"cur": s.question_count + 1, "total": MAX_QUESTIONS},
        }

    if verdict == "need_probe" and probes < MAX_PROBES:
        probe = _add_turn(db, s, "ai", "probe", str(payload.get("probe_question", "能展开说说吗？")))
        return {
            "type": "probe",
            "messages": [_turn_out(user_turn), _turn_out(probe)],
            "progress": {"cur": s.question_count + 1, "total": MAX_QUESTIONS},
        }

    # 终评（need_probe 但已达追问上限也强制终评）
    hit = payload.get("hit") or []
    missed = payload.get("missed") or []
    wrong = payload.get("wrong") or []
    score = max(0, min(100, int(payload.get("score") or 0)))
    n = s.question_count + 1
    s.question_count = n
    s.score_avg = round(
        ((s.score_avg or 0) * (n - 1) + score) / n, 1
    )
    fb = _add_turn(
        db,
        s,
        "ai",
        "feedback",
        str(payload.get("feedback", "")),
        score=score,
        points_hit=json.dumps(hit, ensure_ascii=False),
        points_missed=json.dumps(missed, ensure_ascii=False),
        points_wrong=json.dumps(wrong, ensure_ascii=False),
        suggestion=str(payload.get("suggestion", "")),
    )
    finished = n >= MAX_QUESTIONS
    next_ask = None
    if not finished:
        q = _pick_question(db, c, s.module.value if hasattr(s.module, "value") else str(s.module))
        pack2 = _get_or_create_pack(db, llm, q, s.difficulty)
        next_ask = _add_turn(db, s, "ai", "ask", pack2.open_question, question_id=q.id)
    else:
        s.status = "finished"
    db.commit()
    messages = [_turn_out(user_turn), _turn_out(fb)]
    if next_ask is not None:
        messages.append(_turn_out(next_ask))
    return {
        "type": "final",
        "messages": messages,
        "score": score,
        "finished": finished,
        "session_summary": _summary(db, s) if finished else None,
        "progress": {"cur": n, "total": MAX_QUESTIONS},
    }


def _grade_request(**ctx):
    from ..services.llm_client import GenerationRequest

    return GenerationRequest(
        kind="chat_grade",
        knowledge_point="",
        context=ctx,
    )


def _summary(db: ORMSession, s: ChatSession) -> dict:
    scores = [
        t.score
        for t in db.query(ChatTurn)
        .filter(ChatTurn.session_id == s.id, ChatTurn.turn_type == "feedback")
        .all()
        if t.score is not None
    ]
    return {
        "session_id": s.id,
        "module": s.module.value if hasattr(s.module, "value") else str(s.module),
        "persona": s.persona,
        "question_count": s.question_count,
        "score_avg": s.score_avg,
        "scores": scores,
    }


@router.post("/finish")
def chat_finish(
    body: ChatFinishIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
):
    s = _own_session(db, c, body.session_id)
    s.status = "finished"
    db.commit()
    return _summary(db, s)


@router.get("/session/{session_id}")
def chat_replay(
    session_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
):
    """会话恢复：小程序切后台回收后，回前台重放整场对话。"""
    s = _own_session(db, c, session_id)
    turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.session_id == s.id)
        .order_by(ChatTurn.seq)
        .all()
    )
    return {
        "session_id": s.id,
        "persona": s.persona,
        "difficulty": s.difficulty,
        "module": s.module.value if hasattr(s.module, "value") else str(s.module),
        "status": s.status,
        "messages": [_turn_out(t) for t in turns],
        "progress": {"cur": s.question_count + (1 if s.status == "active" else 0), "total": MAX_QUESTIONS},
    }


@router.get("/history")
def chat_history(
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
):
    rows = (
        db.query(ChatSession)
        .filter(ChatSession.candidate_id == c.id, ChatSession.status == "finished")
        .order_by(ChatSession.id.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "session_id": s.id,
            "module": s.module.value if hasattr(s.module, "value") else str(s.module),
            "persona": s.persona,
            "question_count": s.question_count,
            "score_avg": s.score_avg,
            "started_at": s.started_at.isoformat() if s.started_at else None,
        }
        for s in rows
    ]


@router.get("/stats")
def chat_stats(
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
):
    rows = (
        db.query(ChatSession)
        .filter(ChatSession.candidate_id == c.id, ChatSession.status == "finished")
        .order_by(ChatSession.id.desc())
        .limit(10)
        .all()
    )
    recent = [
        {"session_id": s.id, "score_avg": s.score_avg, "module": s.module.value if hasattr(s.module, "value") else str(s.module)}
        for s in reversed(rows)
    ]
    total_q = (
        db.query(ChatSession)
        .filter(ChatSession.candidate_id == c.id)
        .with_entities(ChatSession.question_count)
        .all()
    )
    best = max((s.score_avg for s in rows), default=0)
    return {
        "total_sessions": len(total_q),
        "total_questions": sum(x[0] or 0 for x in total_q),
        "best_score": best,
        "recent": recent,
    }
