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

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as ORMSession

from ..db import get_db
from ..deps import get_current_candidate
from ..models import (
    Candidate,
    ChatSession,
    ChatTurn,
    Module,
)
from ..services.llm_client import LLMClient, get_llm_client

router = APIRouter(prefix="/api/chat")

MAX_PROBES = 2  # 用户裁决（2026-04-16）：单题追问固定 2 轮；场次无限出新题
MAX_INPUT = 500
DIFFICULTIES = ("medium", "hard")
PERSONAS = ("coach", "examiner")

_OPENING = {
    "coach": (
        "你好呀，我是你的面试教练。接下来咱们不刷题，就像真实面试那样聊——"
        "我出情境题，你用自己的话答，答得含糊我会追问，咱们把它捋顺。放松，第一题来了——"
    ),
    "examiner": "面试开始。共 5 题，请直接作答，我会视情况追问。第一题——",
}


def get_llm_client_dep() -> LLMClient:
    """可覆写的 LLM 客户端依赖：测试用 app.dependency_overrides 注入 fake。"""
    return get_llm_client()


class ChatStartIn(BaseModel):
    module: str | None = None  # #32 纯 LLM 生成路线：模块选择已删除，仅保留兼容
    difficulty: str = "medium"
    persona: str = "coach"


class ChatReplyIn(BaseModel):
    session_id: int
    content: str
    force_final: bool = False  # 「直接看评分」：跳过剩余追问，强制终评


class ChatFinishIn(BaseModel):
    session_id: int


class ChatTtsIn(BaseModel):
    text: str


@router.post("/voice")
async def chat_voice(
    file: "UploadFile",
    c: Candidate = Depends(get_current_candidate),
):
    """#37 语音输入：录音文件 → ASR 转文字。前端拿到 text 后自行走 reply 发送。"""
    from ..services.voice import transcribe

    audio = await file.read()
    if not audio:
        raise HTTPException(400, "音频内容为空")
    if len(audio) > 5 * 1024 * 1024:
        raise HTTPException(400, "语音过长（上限 5MB）")
    ext = (file.filename or "voice.mp3").rsplit(".", 1)[-1].lower() or "mp3"
    if ext not in ("mp3", "aac", "wav", "m4a"):
        ext = "mp3"
    try:
        text = transcribe(audio, ext)
    except RuntimeError as e:
        print(f"[chat/voice] ASR failed: {e}")
        raise HTTPException(503, "语音识别暂时不可用，请改用文字输入")
    if not text:
        raise HTTPException(400, "没有听清内容，请再试一次")
    return {"text": text}


@router.post("/tts")
def chat_tts(
    body: ChatTtsIn,
    c: Candidate = Depends(get_current_candidate),
):
    """#37 语音输出：文本 → mp3 音频（按需合成 + LRU 缓存）。"""
    from fastapi import Response

    from ..services.voice import synthesize

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "文本为空")
    if len(text) > MAX_INPUT:
        raise HTTPException(400, "文本过长")
    try:
        audio = synthesize(text)
    except RuntimeError as e:
        print(f"[chat/tts] TTS failed: {e}")
        raise HTTPException(503, "语音合成暂时不可用，请稍后重试")
    return Response(content=audio, media_type="audio/mpeg")


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


def _ask_turn(db: ORMSession, s: ChatSession, payload: dict) -> ChatTurn:
    """出题 turn：要点存 points_hit 列（#32 复用语义，见 _current_question）。"""
    return _add_turn(
        db,
        s,
        "ai",
        "ask",
        str(payload["open_question"]),
        points_hit=json.dumps(payload["key_points"], ensure_ascii=False),
    )


def _gen_question(llm: LLMClient, difficulty: str, persona: str, avoid: list[str]) -> dict:
    """#32 纯 LLM 生成：现场出情境化面试题 + 评分要点（不绑官方题库、不走缓存）。"""
    from ..services.llm_client import GenerationRequest

    res = llm.generate(
        GenerationRequest(
            kind="chat_pack",
            knowledge_point="",
            context={"difficulty": difficulty, "persona": persona, "avoid": avoid},
        )
    )
    payload = res.payload if res else None
    if not payload or not payload.get("open_question") or not payload.get("key_points"):
        raise HTTPException(503, "AI 出题服务暂时不可用，请稍后重试")
    return payload


def _module_of(payload: dict) -> Module:
    """LLM 判定题目所属模块；无法判定时默认职业理念（供统计口径）。"""
    raw = str(payload.get("module", "") or "")
    for m in Module:
        if m.value == raw:
            return m
    return Module.PROFESSIONAL_IDEA


def _current_question(db: ORMSession, s: ChatSession) -> tuple[ChatTurn, list, int]:
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
    # #33 补充轮次 = 该 ask 之后的 probe + hint 轮数（提示也占用上限，
    # 否则用户反复离题会永远收提示、拿不到分、没有出口）
    idx = turns.index(last_ask)
    probes = sum(1 for t in turns[idx + 1 :] if t.turn_type in ("probe", "hint"))
    # 终评已出且未被新一轮 ask 覆盖 → 场次已收尾
    if any(t.turn_type == "feedback" for t in turns[idx + 1 :]):
        raise HTTPException(409, "本题已出分，请继续下一题或结束训练")
    # #32：要点复用 ask turn 的 points_hit 列存储（纯 LLM 生成路线不走 ChatPack 缓存，零迁移）
    key_points = json.loads(last_ask.points_hit) if last_ask.points_hit else []
    if not key_points:
        # 纯生成改造前的旧会话无要点：明确 409 让前端清掉本场重开，不要 500
        raise HTTPException(409, "本场为旧版对话，请重新开始训练")
    return last_ask, key_points, probes


@router.post("/start")
def chat_start(
    body: ChatStartIn,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client_dep),
):
    if body.module is not None and body.module not in [m.value for m in Module]:
        raise HTTPException(400, "无效模块")
    if body.difficulty not in DIFFICULTIES:
        raise HTTPException(400, "无效难度")
    if body.persona not in PERSONAS:
        raise HTTPException(400, "无效人设")
    # #32 多会话：不再自动收尾历史 active 场——允许多场并存，随时回历史继续
    # （stats 只统计 finished 场，不受影响）

    # #32 纯 LLM 生成：现场出第一题（不再抽官方题库包装）
    payload = _gen_question(llm, body.difficulty, body.persona, avoid=[])
    s = ChatSession(
        candidate_id=c.id,
        module=_module_of(payload),
        difficulty=body.difficulty,
        persona=body.persona,
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    opening = _add_turn(db, s, "ai", "opening", _OPENING[body.persona])
    ask = _ask_turn(db, s, payload)
    return {
        "session_id": s.id,
        "persona": s.persona,
        "difficulty": s.difficulty,
        "module": s.module.value if hasattr(s.module, "value") else str(s.module),
        "messages": [_turn_out(opening), _turn_out(ask)],
        "progress": {"cur": 1, "total": None},  # #33 无限问答
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

    # #36 自愈：上一题已终评但下一题出题失败（历史 503 遗留的卡死态）→ 先补出题。
    # 用户这条消息将作为新题的回答，重发即恢复，无需任何特殊操作。
    turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.session_id == s.id)
        .order_by(ChatTurn.seq)
        .all()
    )
    last_ask = next((t for t in reversed(turns) if t.turn_type == "ask"), None)
    if last_ask is not None:
        idx = turns.index(last_ask)
        if any(t.turn_type == "feedback" for t in turns[idx + 1 :]):
            asked = [t.content for t in turns if t.turn_type == "ask"]
            payload_heal = _gen_question(llm, s.difficulty, s.persona, avoid=asked)
            _ask_turn(db, s, payload_heal)
            turns = (
                db.query(ChatTurn)
                .filter(ChatTurn.session_id == s.id)
                .order_by(ChatTurn.seq)
                .all()
            )
    elif not any(t.turn_type == "ask" for t in turns):
        # active 场却没有任何 ask（异常残留）→ 同样补题
        payload_heal = _gen_question(llm, s.difficulty, s.persona, avoid=[])
        _ask_turn(db, s, payload_heal)
        turns = (
            db.query(ChatTurn)
            .filter(ChatTurn.session_id == s.id)
            .order_by(ChatTurn.seq)
            .all()
        )

    ask, key_points, probes = _current_question(db, s)
    if body.force_final:
        probes = MAX_PROBES  # 用户跳过追问：评分链按已到上限处理，直接终评

    history = [
        {"role": "user" if t.role == "user" else "ai", "content": t.content}
        for t in turns
        if t.turn_type in ("probe", "user") and t.seq > ask.seq
    ]

    res = llm.generate(
        _grade_request(
            open_question=ask.content,
            key_points=key_points,
            history=history,
            content=content,
            probes_used=probes,
            probe_limit=MAX_PROBES,
            persona=s.persona,
        )
    )
    payload = res.payload if res else None
    if not payload:
        # 显式失败：不落任何 turn（本题状态不变，可重试），绝不静默给分
        raise HTTPException(503, "AI 评分暂时不可用，请稍后重试")

    user_turn = _add_turn(db, s, "user", "user", content)
    verdict = payload.get("verdict")

    if verdict == "off_topic" and probes < MAX_PROBES:
        # hint 与 probe 区分类型，但共享补充轮次上限（#33）
        hint = _add_turn(db, s, "ai", "hint", str(payload.get("hint", "请围绕刚才的问题作答哦")))
        return {
            "type": "hint",
            "messages": [_turn_out(user_turn), _turn_out(hint)],
            "progress": {"cur": s.question_count + 1, "total": None},  # #33 无限问答
        }

    if verdict == "need_probe" and probes < MAX_PROBES:
        probe = _add_turn(db, s, "ai", "probe", str(payload.get("probe_question", "能展开说说吗？")))
        return {
            "type": "probe",
            "messages": [_turn_out(user_turn), _turn_out(probe)],
            "progress": {"cur": s.question_count + 1, "total": None},  # #33 无限问答
        }

    # 终评（need_probe / off_topic 但已达补充轮次上限也强制终评——用户永远有出口）
    hit = payload.get("hit") or []
    missed = payload.get("missed") or []
    wrong = payload.get("wrong") or []
    if verdict == "off_topic":
        # LLM 仍判离题但已达上限：按零命中终评，不给用户无限循环的空洞
        hit, missed = [], list(key_points)
        payload["feedback"] = payload.get("feedback") or "几轮下来没有围绕这道题作答，这题先记个低分，我们换下一题。"
        payload["suggestion"] = payload.get("suggestion") or "先听清题目问的具体情境，再针对性作答"
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
    # #33 持续问答：终评后出下一题。
    # #36 解耦：出题失败不能让整个请求失败（否则终评已落库、下一题没出成，
    # 会话卡进「已出分无新题」死锁）——返回 next_failed，用户下一条消息触发自愈补题。
    asked = [t.content for t in turns if t.turn_type == "ask"]
    next_ask = None
    next_failed = False
    try:
        payload2 = _gen_question(llm, s.difficulty, s.persona, avoid=asked)
        next_ask = _ask_turn(db, s, payload2)
    except HTTPException:
        next_failed = True
        db.rollback()
    messages = [_turn_out(user_turn), _turn_out(fb)]
    if next_ask is not None:
        messages.append(_turn_out(next_ask))
    return {
        "type": "final",
        "messages": messages,
        "score": score,
        "finished": False,
        "next_failed": next_failed,
        "session_summary": None,
        "progress": {"cur": n, "total": None},  # total=None：无限问答，不再设上限
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
        "progress": {"cur": s.question_count + (1 if s.status == "active" else 0), "total": None},
    }


@router.get("/history")
def chat_history(
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
):
    """#32 多会话：返回全部对话（含未完成的 active 场，供继续作答）。"""
    rows = (
        db.query(ChatSession)
        .filter(ChatSession.candidate_id == c.id)
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
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
        }
        for s in rows
    ]


@router.delete("/session/{session_id}")
def chat_delete(
    session_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: ORMSession = Depends(get_db),
):
    """#35 删除一场对话（仅本人）：级联删除其全部回合，避免孤儿数据。"""
    s = _own_session(db, c, session_id)
    db.query(ChatTurn).filter(ChatTurn.session_id == s.id).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"ok": True, "session_id": session_id}


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
