import json
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, Question, QuestionSource, Session
from ..schemas import QuestionOut, SessionStartIn, SessionStartOut

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _arrange(rows: list[Question], n: int) -> list[Question]:
    """随机取样并尝试避免相邻同题型（Implementation 4：不连续两个相同题型）。"""
    rows = list(rows)
    random.shuffle(rows)
    selected = rows[:n]
    for i in range(len(selected) - 1):
        if selected[i].type == selected[i + 1].type:
            for j in range(i + 2, len(selected)):
                if selected[j].type != selected[i].type:
                    selected[i + 1], selected[j] = selected[j], selected[i + 1]
                    break
    return selected


@router.post("/start", response_model=SessionStartOut)
def start_session(
    body: SessionStartIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> SessionStartOut:
    """开始闯关局：按模块或考点发起，一局固定题量一次下发（Implementation 4）。"""
    if not body.module and not body.knowledge_point:
        raise HTTPException(status_code=400, detail="module 或 knowledge_point 至少提供一个")
    if body.question_count <= 0:
        raise HTTPException(status_code=400, detail="question_count 必须为正整数")

    q = db.query(Question).filter(Question.source == QuestionSource.POOL)
    if body.module is not None:
        q = q.filter(Question.module == body.module)
    if body.knowledge_point is not None:
        q = q.filter(Question.knowledge_point == body.knowledge_point)

    pool = q.all()
    if len(pool) < body.question_count:
        raise HTTPException(
            status_code=409,
            detail=f"可选题目不足：需 {body.question_count}，仅 {len(pool)}",
        )

    selected = _arrange(pool, body.question_count)
    sess = Session(
        candidate_id=c.id,
        module=body.module,
        knowledge_point=body.knowledge_point,
        question_count=len(selected),
        question_ids=json.dumps([x.id for x in selected], ensure_ascii=False),
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    return SessionStartOut(
        session_id=sess.id,
        candidate_id=c.id,
        module=sess.module,
        knowledge_point=sess.knowledge_point,
        question_count=sess.question_count,
        questions=[QuestionOut.model_validate(x) for x in selected],
    )
