"""异步任务进度轮询（AI 出题）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, DocTask
from ..schemas import DocTaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=DocTaskOut)
def get_task(
    task_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> DocTaskOut:
    """前端按 1.5s 轮询本接口更新进度（页面 onUnload 需清除定时器）。"""
    t = db.get(DocTask, task_id)
    if t is None or t.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    try:
        ids = json.loads(t.generated_question_ids or "[]")
    except Exception:
        ids = []
    return DocTaskOut(
        id=t.id,
        document_id=t.document_id,
        status=t.status,
        mode=t.mode,
        done=t.done,
        total=t.total,
        error=t.error,
        question_count=len(ids),
    )
