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


@router.post("/{task_id}/retry")
def retry_task(
    task_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """停止 / 失败后**继续出题**（#26）：按已出题数计算缺口，发起新任务只补剩余部分。

    已出的题不会重复生成（新任务只包含缺口的题量），完成后两卷合起来即用户选择的完整题量。
    """
    t = db.get(DocTask, task_id)
    if t is None or t.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    if t.status not in ("failed", "cancelled"):
        raise HTTPException(400, f"仅 failed / cancelled 任务可续做（当前 {t.status}）")

    total = t.total or 0
    made = min(t.done or 0, total)
    remaining = total - made
    if remaining <= 0:
        raise HTTPException(400, "已无缺口，无需续做")

    try:
        spec = json.loads(t.spec or "[]")
    except Exception:
        spec = []
    old_total = sum(i.get("count", 0) for i in spec) or 1
    # 按原配比缩放剩余题数（取整偏差最多 1 题，由补偿轮兜住）
    new_spec = [
        {"type": i["type"], "count": max(1, round(i["count"] * remaining / old_total))}
        for i in spec
        if i.get("count", 0) > 0
    ]

    new_t = DocTask(
        candidate_id=c.id,
        document_id=t.document_id,
        mode=t.mode,
        spec=json.dumps(new_spec, ensure_ascii=False),
        difficulty=t.difficulty,
        focus=t.focus,
        scope=t.scope,
        status="pending",
    )
    db.add(new_t)
    db.commit()
    return {
        "task_id": new_t.id,
        "from_task_id": task_id,
        "remaining": remaining,
        "spec": new_spec,
    }


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
