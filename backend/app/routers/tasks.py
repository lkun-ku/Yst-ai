"""异步任务进度轮询（AI 出题）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, DocTask, DocTaskEvent
from ..schemas import DocTaskOut
from ..services import kb_events

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


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """请求取消文档出题（#26）。

    协作式取消：下一个**批次边界**生效——LLM 单次调用无法中断，Python 也不能安全
    强杀线程；强行中断会丢掉整批已生成的题目。已出的题**保留**为部分卷。
    """
    t = db.get(DocTask, task_id)
    if t is None or t.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    if t.status in ("done", "failed", "cancelled"):
        return {"task_id": task_id, "status": t.status, "cancelled": False}
    t.cancel_requested = True
    db.commit()
    return {"task_id": task_id, "status": t.status, "cancelled": True}


@router.get("/{task_id}/events")
def task_events(
    task_id: int,
    since: int = 0,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """增量拉取文档出题过程事件（#26 第二批）。

    前端按 `since`（上次拿到的最大 seq）续拉，断线 / 切后台后不丢事件。
    事件落库（doc_task_events）而非存内存，故跨进程与重启均可回放。
    """
    t = db.get(DocTask, task_id)
    if t is None or t.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    rows = (
        db.query(DocTaskEvent)
        .filter(DocTaskEvent.task_id == task_id, DocTaskEvent.seq > since)
        .order_by(DocTaskEvent.seq)
        .limit(kb_events.MAX_EVENTS_PER_TASK)
        .all()
    )
    return {
        "status": t.status,
        "latest_seq": rows[-1].seq if rows else since,
        "events": [
            {
                "seq": r.seq,
                "type": r.type,
                "text": r.text,
                "detail": r.detail,
                "ts": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ],
    }
