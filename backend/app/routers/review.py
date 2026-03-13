from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, Mastery, MistakeBook, Module, Session
from ..schemas import ReviewOut
from ..services import get_content_safety

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
    mastery = {str(m.value): mdict.get(str(m.value), 0.0) for m in Module}

    # 薄弱考点：错题本 wrong_count 降序 top3；不足则用最低掌握度模块补齐
    mistakes = (
        db.query(MistakeBook)
        .filter(MistakeBook.candidate_id == c.id)
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
