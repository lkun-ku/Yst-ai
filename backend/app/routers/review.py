import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import (
    OFFICIAL_MODULES,
    Candidate,
    Mastery,
    MistakeBook,
    Module,
    Question,
    Session,
)
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
    """复盘报告（Implementation 8/15）。

    按**局类型**选择复盘口径（#26）：
    - 官方局（module 为官方五维）→ 五维掌握度 + 官方薄弱考点
      （A1：仅统计 OFFICIAL_MODULES，个人资料维度不串入）
    - 个人题库局（module = 个人资料）→「资料掌握度」：按**本局题目的考点**聚合本局正确率。
      此前个人题库的局也套用教资五维 → 五维全 0% + 教资考点建议，与实际作答的个人题完全错位。
    """
    sess = db.get(Session, session_id)
    if sess is None or sess.candidate_id != c.id:
        raise HTTPException(status_code=404, detail="session not found")

    if sess.module == Module.PERSONAL:
        # ---- 个人题库局：资料掌握度（按本局考点聚合正确率）----
        try:
            results = json.loads(sess.result_json or "[]")
        except Exception:
            results = []

        agg: dict[str, list[int]] = {}
        for r in results or []:
            kp = str(r.get("knowledge_point") or "未分类")
            slot = agg.setdefault(kp, [0, 0])
            slot[0] += 1
            if r.get("is_correct"):
                slot[1] += 1

        # 掌握度 = 该考点本局正确率（key 为考点名，前端沿用同一渲染逻辑）
        mastery = {
            k: (round(slot[1] / slot[0], 2) if slot[0] else 0.0) for k, slot in agg.items()
        }
        # 薄弱考点：正确率升序（错题优先）
        ranked = sorted(
            agg.items(), key=lambda kv: (kv[1][1] / kv[1][0] if kv[1][0] else 1.0)
        )
        weak = [k for k, _ in ranked[:3]]
        unit = "考点"
        scope_text = "资料掌握度"
    else:
        # ---- 官方局：五维掌握度（A1：仅官方五维）----
        mrows = db.query(Mastery).filter(Mastery.candidate_id == c.id).all()
        mdict = {str(m.module.value): m.score for m in mrows}
        mastery = {str(m.value): mdict.get(str(m.value), 0.0) for m in OFFICIAL_MODULES}

        # 薄弱考点：错题本 wrong_count 降序 top3；不足则用最低掌握度模块补齐。
        # A1：inner join Question 并排除 PERSONAL（个人资料不串入官方统计；
        # join 同时天然处理级联删除——个人题被删除后 join 不到，不会残留）。
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
        unit = "模块"
        scope_text = "五维掌握度"

    next_step = (
        f"下一步：针对薄弱考点【{', '.join(weak[:3])}】再练一组题。"
        if weak
        else "保持节奏，明天继续！"
    )
    paragraph = (
        f"本次闯关已生成复盘。你在 {len(mastery)} 个{unit}上的掌握度为 "
        + ", ".join(f"{k} {v:.2f}" for k, v in mastery.items())
        + f"。建议优先巩固：{', '.join(weak[:3])}。（AI 生成，仅供参考）"
    )

    # 票 14：个性化段落由模型写（模板结构兜底，缺失/超限优雅降级）
    today = date.today()
    if get_today_usage(db, c.id, today) < REALTIME_DAILY_LIMIT and weak:
        res = get_llm_client().generate(
            GenerationRequest(
                kind="review_paragraph",
                knowledge_point=weak[0],
                context={"mastery": mastery, "session_id": sess.id, "scope": scope_text},
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
