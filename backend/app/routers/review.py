from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import OFFICIAL_MODULES, Candidate, Mastery, MistakeBook, Module, Question, Session
from ..schemas import ReviewOut
from ..services import get_content_safety, get_llm_client
from ..services.llm_client import GenerationRequest
from ..services.realtime import REALTIME_DAILY_LIMIT, get_today_usage, increment_usage

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/{session_id}", response_model=ReviewOut)
def get_review(
    session_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> ReviewOut:
    """复盘报告：五维掌握度、薄弱 3 考点、下一步一件事、AIGC 个性化段落（Implementation 8/15）。

    段落当前为模板占位，真实个性化生成在票 14 接实时管线；缺失时优雅降级（本票已为模板，不会为空）。
    """
    sess = db.get(Session, session_id)
    if sess is None or sess.candidate_id != c.id:
        raise HTTPException(status_code=404, detail="session not found")

    # 五维掌握度（缺省 0）
    mrows = db.query(Mastery).filter(Mastery.candidate_id == c.id).all()
    mdict = {str(m.module.value): m.score for m in mrows}
    # A1：只统计官方五维。PERSONAL 为用户资料独立维度，纳入会让五维雷达变六维。
    mastery = {str(m.value): mdict.get(str(m.value), 0.0) for m in OFFICIAL_MODULES}

    # 薄弱考点：错题本 wrong_count 降序 top3；不足则用最低掌握度模块补齐。
    # A1：与 mastery 保持同一裁决——个人资料（PERSONAL）是用户上传资料生成的独立维度，
    # 不串入官方五维统计（此前未过滤，导致官方复盘里出现「第一章 …」这类个人资料考点）。
    # inner join 同时天然处理级联删除：个人题被删除（A5）后 join 不到，不会残留。
    mistakes = (
        db.query(MistakeBook)
        .join(Question, Question.id == MistakeBook.question_id)
        .filter(MistakeBook.candidate_id == c.id, Question.module != Module.PERSONAL)
        .order_by(MistakeBook.wrong_count.desc())
        .limit(3)
        .all()
    )
    weak = [mb.knowledge_point for mb in mistakes]
    if len(weak) < 3:
        for mod, _ in sorted(mastery.items(), key=lambda x: x[1]):
            if mod not in weak:
                weak.append(mod)
            if len(weak) >= 3:
                break

    next_step = (
        f"下一步：针对薄弱考点【{', '.join(weak[:3])}】再练一组题。"
        if weak
        else "保持节奏，明天继续！"
    )
    paragraph = (
        f"本次闯关已生成复盘。你在 {len(mastery)} 个模块上的掌握度为 "
        + ", ".join(f"{k} {v:.2f}" for k, v in mastery.items())
        + f"。建议优先巩固：{', '.join(weak[:3])}。（AI 生成，仅供参考）"
    )

    # 票 14：个性化段落由模型写（模板结构兜底，缺失/超限优雅降级，Implementation 15/14）
    today = date.today()
    if get_today_usage(db, c.id, today) < REALTIME_DAILY_LIMIT and weak:
        res = get_llm_client().generate(
            GenerationRequest(
                kind="review_paragraph",
                knowledge_point=weak[0],
                context={"mastery": mastery, "session_id": sess.id},
            )
        )
        if res.text.strip():
            paragraph = res.text.strip()
            increment_usage(db, c.id, today)

    # 输出侧内容安全检测（Implementation 29）：未通过不得展示，降级占位文案
    if not get_content_safety().check_output(paragraph):
        paragraph = "该报告内容未通过安全检测，暂时无法展示。"

    return ReviewOut(
        session_id=sess.id,
        mastery=mastery,
        weak_points=weak[:3],
        next_step=next_step,
        paragraph=paragraph,
        aigc_flag=True,
    )
