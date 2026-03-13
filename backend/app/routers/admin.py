"""最小审校后台（票 13 / Implementation 19-21）。

- 待审题目队列（按模块抽检比例抽样）+ 通过/驳回（驳回即弃）。
- 错误报告队列（纠错工单）+ 处理（accept=已处理 / reject=驳回）。
- 鉴权：X-Admin-Token 头（内测用简单令牌；完整 Web 后台不在 MVP）。
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from ..config import settings
from ..db import get_db
from ..models import ErrorReport, ProofreadStatus, Question, ReportStatus
from ..services.proofread import sample_pending_questions
from ..services.realtime import realtime_share

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="需要管理员令牌")


class ResolveIn(BaseModel):
    approved: bool


class ReportResolveIn(BaseModel):
    action: str  # accept | reject


@router.get("/proofread/pending", dependencies=[Depends(require_admin)])
def pending_questions(db: DBSession = Depends(get_db)) -> list[dict]:
    """待审题目队列：按抽检比例（文化素养最高）从 PENDING 池抽样。"""
    sampled = sample_pending_questions(db)
    return [
        {
            "id": q.id,
            "module": str(q.module.value),
            "knowledge_point": q.knowledge_point,
            "stem": q.stem,
            "options": q.options,
            "answer": q.answer,
            "explanation": q.explanation,
        }
        for q in sampled
    ]


@router.post("/proofread/{question_id}/resolve", dependencies=[Depends(require_admin)])
def resolve_question(question_id: int, body: ResolveIn, db: DBSession = Depends(get_db)) -> dict:
    """通过（PASSED）/ 驳回即弃（REJECTED，不再进入出题池）。"""
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(status_code=404, detail="question not found")
    q.proofread_status = ProofreadStatus.PASSED if body.approved else ProofreadStatus.REJECTED
    db.commit()
    return {"id": q.id, "proofread_status": str(q.proofread_status.value)}


@router.get("/pool-stats", dependencies=[Depends(require_admin)])
def pool_stats(db: DBSession = Depends(get_db)) -> dict:
    """池化率监控与告警（票 14 / Implementation 17）：实时占比 >5% 告警。"""
    from datetime import date

    share, alarm, realtime_count = realtime_share(db, date.today())
    return {
        "realtime_today": realtime_count,
        "pool_ratio": round(1 - share, 4),
        "alarm": alarm,
        "threshold": 0.95,
    }


@router.get("/reports", dependencies=[Depends(require_admin)])
def list_reports(status: str = "pending", db: DBSession = Depends(get_db)) -> list[dict]:
    """错误报告队列（纠错工单）。"""
    q = db.query(ErrorReport)
    if status:
        q = q.filter(ErrorReport.status == ReportStatus(status))
    rows = q.order_by(ErrorReport.id.desc()).limit(100).all()
    return [
        {
            "id": r.id,
            "question_id": r.question_id,
            "error_type": r.error_type,
            "detail": r.detail,
            "status": str(r.status.value),
        }
        for r in rows
    ]


@router.post("/reports/{report_id}/resolve", dependencies=[Depends(require_admin)])
def resolve_report(report_id: int, body: ReportResolveIn, db: DBSession = Depends(get_db)) -> dict:
    """错误报告处理：accept（已核实处理）/ reject（驳回）。"""
    r = db.get(ErrorReport, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="report not found")
    if body.action not in {"accept", "reject"}:
        raise HTTPException(status_code=400, detail="action 须为 accept/reject")
    r.status = ReportStatus.RESOLVED if body.action == "accept" else ReportStatus.REJECTED
    db.commit()
    return {"id": r.id, "status": str(r.status.value)}
