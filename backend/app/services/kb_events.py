"""出题过程事件记录（#25 生题流式展示）。

事件落库到 `kb_task_events`，前端按 `since` 增量拉取渲染时间线。

设计要点：
- **观测性不得反过来拖垮业务**：写事件失败（DB 异常等）一律吞掉并回滚，出题主流程不受影响；
- **但取消信号必须传播**：取消由 `routers/kb.py` 的回调抛出，故 `emit` 本身不捕获
  回调上层逻辑的异常（只捕获本模块的写库异常）。
"""

from __future__ import annotations

import json

from ..models import KbTaskEvent

#: 单任务事件上限：长卷（100 题）场景下防止事件表被撑大
MAX_EVENTS_PER_TASK = 200


def emit(
    db,
    task_id: str,
    type_: str,
    text: str,
    detail: dict | list | str | None = None,
) -> None:
    """写入一条过程事件。失败静默（仅回滚），绝不向上传播。"""
    try:
        payload: str | None = None
        if detail is not None:
            payload = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        db.add(
            KbTaskEvent(
                task_id=task_id,
                type=str(type_)[:16],
                text=text,
                detail=payload,
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def slice_previews(chunks: list[dict], limit: int = 8, preview_len: int = 60) -> list[dict]:
    """把召回切片压成时间线可展示的摘要（D1：只存 id/标题/前 60 字，全文按需另取）。"""
    out: list[dict] = []
    for c in (chunks or [])[:limit]:
        out.append(
            {
                "id": c.get("id"),
                "heading": c.get("heading_path"),
                "preview": (c.get("content") or "")[:preview_len],
            }
        )
    return out
