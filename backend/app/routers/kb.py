"""知识库出题接口（实验性；路线②/③ 的 HTTP 入口）。

- POST /api/kb/generate：依据自然语言 scope 跨文档出题（后台线程，复用独立 SessionLocal）。
- GET  /api/kb/retrieve：调试 / 评测用，返回召回切片与分值。
- GET  /api/kb/task/{task_id}：轮询进度（落 KbTask 表，进程重启不丢）。
- GET  /api/kb/task/{task_id}/questions：交付生成的卷子（按题 ID 回查，含 source_chunk 溯源）。

配额：复用 documents 的每日出题次数上限（doc_daily_gen_limit），超限 429；
质量闭环在 LLM 不可用时自动降级为单轮生成（不阻塞出题）。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import get_current_candidate
from ..models import Candidate, DocTask, KbTask, Question
from ..schemas import KbQuestionOut
from ..services import kb_generate, kb_graph
from ..services.embedding import wait_embed_ready
from ..services.kb_retrieval import retrieve_by_scope

# 出题编排路线：graph=路线③ LangGraph StateGraph；handwritten=路线② 手写质量闭环
VALID_ROUTES = ("graph", "handwritten")

router = APIRouter(prefix="/api/kb", tags=["kb"])


def _select_generator(route: str):
    """按 route 选择出题编排：graph=路线③ LangGraph；其余=路线② 手写。"""
    return (
        kb_graph.generate_by_scope_graph
        if route == "graph"
        else kb_generate.generate_by_scope
    )


class KbGenerateIn(BaseModel):
    scope: str
    spec: list[dict]  # [{"type": "single", "count": 5}]
    difficulty: str = "medium"
    focus: str | None = None
    enable_loop: bool = True
    route: str = "graph"  # "graph"=路线③ LangGraph；"handwritten"=路线② 手写


class KbRetrieveOut(BaseModel):
    id: int
    content: str
    heading_path: str | None = None
    fusion_score: float = 0.0
    vector_score: float = 0.0
    keyword_score: int = 0
    heading_bonus: float = 0.0


def _local_day(dt) -> str:
    if dt is None:
        return ""
    return dt.isoformat()[:10]


def _daily_gen_count(db: Session, candidate_id: int) -> int:
    today = date.today().isoformat()
    return sum(
        1
        for t in db.query(DocTask).filter(DocTask.candidate_id == candidate_id).all()
        if _local_day(t.created_at) == today
    )


def _run(task_id: str, candidate_id: int, payload: KbGenerateIn) -> None:
    db = SessionLocal()  # 独立 DB Session
    rec = db.get(KbTask, task_id)
    if rec is None:
        db.close()
        return
    try:
        rec.status = "running"
        db.commit()

        # 出题前等待该考生切片向量就绪（#23）：超时不阻塞，按三级降级继续
        if not wait_embed_ready(db, candidate_id=candidate_id):
            logger.warning(
                "kb_task=%s candidate=%s 切片向量在超时上限内未就绪，按三级降级继续出题",
                task_id, candidate_id,
            )

        def on_progress(done: int, total: int) -> None:
            rec.done = done
            rec.total = total
            db.commit()

        gen_fn = _select_generator(payload.route)
        created = gen_fn(
            db,
            candidate_id,
            payload.scope,
            payload.spec,
            payload.difficulty,
            payload.focus,
            payload.enable_loop,
            on_progress=on_progress,
        )
        db.flush()
        rec.count = len(created)
        rec.generated_question_ids = json.dumps([q.id for q in created], ensure_ascii=False)
        rec.status = "done"
        db.commit()
    except Exception as e:  # 单批失败不致命
        try:
            # 失败事务必须先回滚：否则失败的 flush 会挂起，后续 commit 静默失败，
            # 任务状态永远停在 running，前端轮询不到终态。
            db.rollback()
            rec.status = "failed"
            rec.error = str(e)[:500]
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/generate")
def generate(
    body: KbGenerateIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    if _daily_gen_count(db, c.id) >= settings.doc_daily_gen_limit:
        raise HTTPException(429, f"今日出题次数已用完（{settings.doc_daily_gen_limit} 次），明天再来")
    if not body.scope or not body.spec:
        raise HTTPException(400, "请填写出题范围 scope 与题型配比 spec")
    if body.route not in VALID_ROUTES:
        raise HTTPException(400, f"route 仅支持 {'/'.join(VALID_ROUTES)}")
    task_id = uuid.uuid4().hex
    db.add(KbTask(task_id=task_id, candidate_id=c.id, status="pending"))
    db.commit()
    threading.Thread(target=_run, args=(task_id, c.id, body), daemon=True).start()
    return {"task_id": task_id, "scope": body.scope}


@router.get("/task/{task_id}")
def task_status(
    task_id: str,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    rec = db.get(KbTask, task_id)
    if not rec or rec.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    return {
        "status": rec.status,
        "done": rec.done,
        "total": rec.total,
        "count": rec.count,
        "error": rec.error,
    }


@router.get("/task/{task_id}/questions", response_model=list[KbQuestionOut])
def task_questions(
    task_id: str,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """交付接口：按 KbTask.generated_question_ids 回查考生本人题目（保生成顺序）。"""
    rec = db.get(KbTask, task_id)
    if not rec or rec.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    ids = json.loads(rec.generated_question_ids or "[]")
    if not ids:
        return []
    qs = (
        db.query(Question)
        .filter(Question.id.in_(ids), Question.owner_candidate_id == c.id)
        .all()
    )
    order = {qid: i for i, qid in enumerate(ids)}
    qs.sort(key=lambda q: order.get(q.id, 0))
    return [KbQuestionOut.model_validate(q) for q in qs]


@router.get("/retrieve", response_model=list[KbRetrieveOut])
def retrieve(
    scope: str,
    k: int = 8,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    chunks = retrieve_by_scope(db, c.id, scope, k=k)
    return [
        KbRetrieveOut(
            **{kk: v for kk, v in ch.items() if kk in KbRetrieveOut.model_fields}
        )
        for ch in chunks
    ]
