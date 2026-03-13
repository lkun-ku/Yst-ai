"""题目纠错提交（票 13 / Implementation 11 / 用户故事 #21-#22）。

考生报告题目错误（答案/解析/考点归属）→ 进入审校队列。
纠错文本为考生输入侧，接入内容安全检测（Implementation 29），未通过不入库。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, ErrorReport, Question, ReportStatus
from ..services import get_content_safety

router = APIRouter(prefix="/api/reports", tags=["reports"])

VALID_ERROR_TYPES = {"answer", "explanation", "knowledge_point"}


class ReportIn(BaseModel):
    question_id: int
    error_type: str  # answer | explanation | knowledge_point
    detail: str


class ReportOut(BaseModel):
    report_id: int
    status: str


@router.post("", response_model=ReportOut)
def create_report(
    body: ReportIn,
    c: Candidate = Depends(get_current_candidate),
    db: DBSession = Depends(get_db),
) -> ReportOut:
    if body.error_type not in VALID_ERROR_TYPES:
        raise HTTPException(status_code=400, detail="error_type 须为 answer/explanation/knowledge_point")
    if db.get(Question, body.question_id) is None:
        raise HTTPException(status_code=404, detail="question not found")
    if not str(body.detail).strip():
        raise HTTPException(status_code=400, detail="纠错说明不能为空")

    # 输入侧内容安全检测（真实拦截见 WxContentSafetyClient）
    if not get_content_safety().check_input(body.detail):
        raise HTTPException(status_code=400, detail="纠错内容未通过安全检测，未能提交")

    rep = ErrorReport(
        candidate_id=c.id,
        question_id=body.question_id,
        error_type=body.error_type,
        detail=body.detail.strip(),
        status=ReportStatus.PENDING,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return ReportOut(report_id=rep.id, status=str(rep.status.value))
