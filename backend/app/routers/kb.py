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
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import get_current_candidate
from ..models import (
    Candidate,
    DocTask,
    Document,
    DocumentChunk,
    KbTask,
    KbTaskEvent,
    Question,
)
from ..schemas import KbQuestionOut
from ..services import kb_events, kb_generate, kb_graph
from ..services.task_pool import submit
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
    # #26 A：布鲁姆认知层级池，逗号分隔（如 "understand,apply"）；None = 用配置默认
    bloom: str | None = None


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


class _TaskCancelled(Exception):
    """协作式取消信号：由 on_progress / on_event 回调在批次边界抛出。

    Python 无法安全强杀线程，且单次 LLM 调用（10~20s）内部不可中断，
    因此取消最迟在一个批次结束后生效（D4）。
    """


def _question_ids_of(db, task_id: str) -> list[int]:
    """从已记录的 question 事件里回收本次任务生成的题目 id（取消 / 重试时用）。"""
    ids: list[int] = []
    for e in (
        db.query(KbTaskEvent)
        .filter(KbTaskEvent.task_id == task_id, KbTaskEvent.type == "question")
        .order_by(KbTaskEvent.seq)
        .all()
    ):
        if not e.detail:
            continue
        try:
            qid = json.loads(e.detail).get("id")
        except Exception:
            qid = None
        if qid and qid not in ids:
            ids.append(qid)
    return ids


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

        def on_event(type_: str, text: str, detail=None) -> None:
            """#25：记录过程事件，并在事件点检查取消信号（协作式，批次边界生效）。"""
            kb_events.emit(db, task_id, type_, text, detail)
            db.refresh(rec)
            if rec.cancel_requested:
                raise _TaskCancelled()

        def on_progress(done: int, total: int) -> None:
            rec.done = done
            rec.total = total
            db.commit()
            db.refresh(rec)
            if rec.cancel_requested:
                raise _TaskCancelled()

        # #26 A：认知层级池（逗号分隔字符串 → 列表；空则用配置默认）
        bloom = [s.strip() for s in (payload.bloom or "").split(",") if s.strip()] or None
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
            on_event=on_event,
            bloom=bloom,
        )
        db.flush()
        # 合并已有 id：重试任务会带上原任务已完成的题目，避免被本次结果覆盖
        existing = json.loads(rec.generated_question_ids or "[]")
        ids = existing + [q.id for q in created if q.id not in existing]
        rec.count = len(ids)
        rec.generated_question_ids = json.dumps(ids, ensure_ascii=False)
        rec.status = "done"
        db.commit()
    except _TaskCancelled:
        # D3：保留已生成题目并交付部分卷。题目写入已随 on_progress 的 commit 落库，
        # 但 created 局部变量随异常丢失，故从 question 事件回收 id。
        try:
            db.rollback()
            r = db.get(KbTask, task_id)
            if r is not None:
                done_ids = _question_ids_of(db, task_id)
                r.status = "cancelled"
                if done_ids:
                    r.count = len(done_ids)
                    r.generated_question_ids = json.dumps(done_ids, ensure_ascii=False)
                db.commit()
        except Exception:
            pass
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
    # D5：保存原始请求参数，供失败 / 取消后「仅重试缺口」使用
    request_json = json.dumps(
        {
            "scope": body.scope,
            "spec": body.spec,
            "difficulty": body.difficulty,
            "focus": body.focus,
            "enable_loop": body.enable_loop,
            "route": body.route,
            "bloom": body.bloom,
        },
        ensure_ascii=False,
    )
    db.add(KbTask(task_id=task_id, candidate_id=c.id, status="pending", request_json=request_json))
    db.commit()
    # #26 P2：走有界线程池而非裸起线程，避免并发打爆 LLM 供应商限流
    submit(_run, task_id, c.id, body)
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


# ---------------- #25 生题流式展示：过程事件 / 取消 / 重试 ----------------

@router.get("/task/{task_id}/events")
def task_events(
    task_id: str,
    since: int = 0,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """增量拉取出题过程事件（#25）。

    前端按 `since`（上次拿到的最大 seq）续拉，断线 / 切后台后不丢事件。
    事件落库而非存内存，故跨进程与进程重启均可回放。
    """
    rec = db.get(KbTask, task_id)
    if not rec or rec.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    rows = (
        db.query(KbTaskEvent)
        .filter(KbTaskEvent.task_id == task_id, KbTaskEvent.seq > since)
        .order_by(KbTaskEvent.seq)
        .limit(kb_events.MAX_EVENTS_PER_TASK)
        .all()
    )
    return {
        "status": rec.status,
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


@router.post("/task/{task_id}/cancel")
def cancel_task(
    task_id: str,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """请求取消出题（协作式，下一个批次边界生效）。

    D3：已生成的题目**保留**并作为部分卷交付；D4：最迟一个批次（约 20s）后停止。
    """
    rec = db.get(KbTask, task_id)
    if not rec or rec.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    if rec.status in ("done", "failed", "cancelled"):
        return {"task_id": task_id, "status": rec.status, "cancelled": False}
    rec.cancel_requested = True
    db.commit()
    return {"task_id": task_id, "status": rec.status, "cancelled": True}


@router.post("/task/{task_id}/retry")
def retry_task(
    task_id: str,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """失败 / 取消后**仅重试缺口**（#25 D5）。

    用任务保存的原始请求参数，按已出题数扣减 spec 后发起新任务；
    新任务继承原任务已完成的题目 id，完成后即为完整卷子（不重复生成）。
    """
    rec = db.get(KbTask, task_id)
    if not rec or rec.candidate_id != c.id:
        raise HTTPException(404, "任务不存在")
    if rec.status not in ("failed", "cancelled"):
        raise HTTPException(400, f"仅 failed / cancelled 任务可重试（当前 {rec.status}）")
    if not rec.request_json:
        raise HTTPException(400, "缺少原始请求参数，无法重试（请重新提交出题）")

    req = json.loads(rec.request_json)
    made: dict[str, int] = {}
    for e in (
        db.query(KbTaskEvent)
        .filter(KbTaskEvent.task_id == task_id, KbTaskEvent.type == "question")
        .all()
    ):
        if not e.detail:
            continue
        try:
            d = json.loads(e.detail)
        except Exception:
            continue
        t = d.get("type") or "single"
        made[t] = made.get(t, 0) + 1

    spec = [
        {"type": s.get("type"), "count": max(0, int(s.get("count", 0)) - made.get(s.get("type"), 0))}
        for s in (req.get("spec") or [])
    ]
    spec = [s for s in spec if s["count"] > 0]
    if not spec:
        raise HTTPException(400, "缺口为 0，无需重试")

    req["spec"] = spec
    done_ids = _question_ids_of(db, task_id)
    new_id = uuid.uuid4().hex
    db.add(
        KbTask(
            task_id=new_id,
            candidate_id=c.id,
            status="pending",
            request_json=json.dumps(req, ensure_ascii=False),
            generated_question_ids=json.dumps(done_ids, ensure_ascii=False),
        )
    )
    db.commit()
    submit(_run, new_id, c.id, KbGenerateIn(**req))
    return {"task_id": new_id, "from_task_id": task_id, "spec": spec}


@router.get("/chunk/{chunk_id}")
def get_chunk(
    chunk_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
):
    """按需取切片全文（#25 D1：时间线点开时用）。

    事件里只存 id + 标题 + 前 60 字摘要，点开才拉全文——避免事件表与轮询流量膨胀。
    仅本人资料可见。
    """
    ch = db.get(DocumentChunk, chunk_id)
    if ch is None:
        raise HTTPException(404, "切片不存在")
    doc = db.get(Document, ch.document_id)
    if doc is None or doc.candidate_id != c.id:
        raise HTTPException(404, "切片不存在")
    return {
        "id": ch.id,
        "document_id": ch.document_id,
        "seq": ch.seq,
        "heading_path": ch.heading_path,
        "content": ch.content,
    }
