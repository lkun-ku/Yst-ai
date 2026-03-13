"""错题本（票 09 / Implementation 8 / 用户故事 #31-#33）。

- 答错的题在提交判定（票 06）时自动写入 MistakeBook，无需手动记录。
- 本路由按考点聚合呈现（同一考点反复错合并，显示错次），按错次降序。
- 一键重练复用票 03 的 POST /api/sessions/start（knowledge_point 发起入口）。
"""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, MistakeBook, Question, QuestionSource, ProofreadStatus
from ..schemas import MistakeGroupOut, QuestionOut
from ..services import get_llm_client
from ..services.llm_client import GenerationRequest
from ..services.proofread import get_valid_knowledge_points
from ..services.realtime import REALTIME_DAILY_LIMIT, get_today_usage, increment_usage
from ..services.validation import validate_question_payload

router = APIRouter(prefix="/api/mistakes", tags=["mistakes"])


class VariantOut(BaseModel):
    question: QuestionOut
    degraded: bool  # True = 实时额度超限/生成失败，降级纯池化供给（考生无感）


@router.get("", response_model=list[MistakeGroupOut])
def list_mistakes(
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> list[MistakeGroupOut]:
    """错题本：按考点聚合（错次累计、按错次降序）。"""
    rows = (
        db.query(MistakeBook, Question.module)
        .join(Question, Question.id == MistakeBook.question_id)
        .filter(MistakeBook.candidate_id == c.id)
        .all()
    )

    groups: dict[str, dict] = {}
    for mb, module in rows:
        g = groups.setdefault(
            mb.knowledge_point,
            {"wrong_count": 0, "question_ids": [], "last_wrong_at": "", "module": module},
        )
        g["wrong_count"] += mb.wrong_count
        g["question_ids"].append(mb.question_id)
        last = mb.last_wrong_at.isoformat() if mb.last_wrong_at else ""
        if last > g["last_wrong_at"]:
            g["last_wrong_at"] = last

    out = [
        MistakeGroupOut(
            knowledge_point=kp,
            module=g["module"],
            wrong_count=g["wrong_count"],
            question_count=len(g["question_ids"]),
            question_ids=sorted(g["question_ids"]),
            last_wrong_at=g["last_wrong_at"],
        )
        for kp, g in groups.items()
    ]
    out.sort(key=lambda x: (-x.wrong_count, x.knowledge_point))
    return out


def _pool_fallback(db: Session, q: Question) -> Question | None:
    """降级：同模块任一已通过审校的池化题（不含本题）。"""
    return (
        db.query(Question)
        .filter(
            Question.module == q.module,
            Question.id != q.id,
            Question.source == QuestionSource.POOL,
            Question.proofread_status == ProofreadStatus.PASSED,
        )
        .first()
    )


@router.post("/{question_id}/variant", response_model=VariantOut)
def make_variant(
    question_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> VariantOut:
    """错题即时变式（票 14 / Implementation 13-14）。

    优先取同考点备用题（不耗实时额度）→ 池空才实时生成（每人每日 3 次上限，
    超限/生成失败自动降级纯池化，考生无感）。
    """
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(status_code=404, detail="question not found")

    # 1. 同考点备用题优先（须为审校通过的池题；实时产物待审，不计入备用，防止绕过限额）
    spare = (
        db.query(Question)
        .filter(
            Question.knowledge_point == q.knowledge_point,
            Question.id != q.id,
            Question.source == QuestionSource.POOL,
            Question.proofread_status == ProofreadStatus.PASSED,
        )
        .first()
    )
    if spare is not None:
        return VariantOut(question=QuestionOut.model_validate(spare), degraded=False)

    # 2. 池空 → 实时生成（每日限额）
    today = date.today()
    if get_today_usage(db, c.id, today) >= REALTIME_DAILY_LIMIT:
        fallback = _pool_fallback(db, q)
        if fallback is None:
            raise HTTPException(status_code=409, detail="无可供给题目")
        return VariantOut(question=QuestionOut.model_validate(fallback), degraded=True)

    res = get_llm_client().generate(
        GenerationRequest(
            kind="variant",
            knowledge_point=q.knowledge_point,
            context={"module": str(q.module.value)},
        )
    )
    payload = res.payload or {}
    # 结构化校验：不通过即弃（Implementation 19-20），转降级
    if payload and not validate_question_payload(payload, get_valid_knowledge_points()):
        new_q = Question(
            module=q.module,
            knowledge_point=q.knowledge_point,
            stem=payload["stem"],
            options=json.dumps(payload["options"], ensure_ascii=False),
            answer=json.dumps(payload["answer"], ensure_ascii=False),
            explanation=payload["explanation"],
            source=QuestionSource.POOL,
            aigc_flag=True,
        )
        db.add(new_q)
        increment_usage(db, c.id, today)
        db.commit()
        db.refresh(new_q)
        return VariantOut(question=QuestionOut.model_validate(new_q), degraded=False)

    # 3. 生成失败/校验不通过 → 降级
    fallback = _pool_fallback(db, q)
    if fallback is None:
        raise HTTPException(status_code=409, detail="无可供给题目")
    return VariantOut(question=QuestionOut.model_validate(fallback), degraded=True)
