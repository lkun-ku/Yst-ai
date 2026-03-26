"""资料上传与出题（AI 出题模块）。

后台线程必须使用**独立 DB Session**（A6）：请求作用域 Session 在响应结束即关闭，
在线程里继续使用会出错。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import get_current_candidate
from ..models import Candidate, Document, DocumentChunk, DocTask, Question
from ..utils import local_day
from ..schemas import (
    DocTaskOut,
    DocumentDetailOut,
    DocumentOut,
    GenerateIn,
    GenerateOut,
)
from ..services import doc_parser
from ..services.doc_generate import generate_for_document, normalize_spec
from ..services.embedding import embed_one, encode_vector, wait_embed_ready
from ..services.task_pool import EMBED, submit

router = APIRouter(prefix="/api/documents", tags=["documents"])

logger = logging.getLogger(__name__)

ALLOWED_EXT = {"pdf", "docx", "txt", "md"}


def _ext(name: str) -> str:
    return (name.rsplit(".", 1)[-1] if "." in (name or "") else "").lower()


def _local_day(dt):
    """按本地时区取日期。实现收口到 app/utils.local_day，避免与 kb.py 行为分叉（#26）。"""
    return local_day(dt)


def _doc_out(db: Session, doc: Document) -> DocumentOut:
    qcount = db.query(Question).filter(Question.doc_id == doc.id).count()
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        file_type=doc.file_type,
        char_count=doc.char_count,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        status=doc.status,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
        question_count=qcount,
    )


# ---------------- 后台任务（独立 Session） ----------------

def _embed_document(doc_id: int) -> None:
    """异步生成切片向量。失败只标记 embed_status，不影响文档可用性（三级降级）。"""
    db = SessionLocal()
    try:
        for ch in db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).all():
            try:
                vec = embed_one(ch.content)
                # 工单 20/W-5：单一 embedding 列，按方言写入不同形态
                #   PG → vector（供 HNSW 索引与 SQL 余弦检索）
                #   SQLite → float32 字节（供内存 numpy 混合检索）
                if db.get_bind().dialect.name == "postgresql":
                    ch.embedding = vec
                else:
                    ch.embedding = encode_vector(vec)
                ch.embed_status = "ok"
            except Exception:
                ch.embed_status = "failed"
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _run_task(task_id: int) -> None:
    db = SessionLocal()  # A6：独立 DB Session
    try:
        task = db.get(DocTask, task_id)
        if task is None:
            return
        task.status = "running"
        db.commit()

        doc = db.get(Document, task.document_id)
        # 出题前等待切片向量就绪：否则检索会在向量就绪前静默降级为纯关键词
        # （vector_score 全 0，出题质量下降且调用方无感知，#23）；超时不阻塞出题。
        if not wait_embed_ready(db, document_id=task.document_id):
            logger.warning(
                "doc_task=%s document=%s 切片向量在超时上限内未就绪，按三级降级继续出题",
                task_id, task.document_id,
            )
        chunks = [
            {
                "seq": c.seq,
                "content": c.content,
                "heading_path": c.heading_path,
                "char_count": c.char_count,
                "embedding": c.embedding,
                "embed_status": c.embed_status,
            }
            for c in db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.seq)
            .all()
        ]
        scope = json.loads(task.scope) if task.scope else None
        spec = json.loads(task.spec or "[]")

        def on_progress(done: int, total: int) -> None:
            task.done = done
            task.total = total
            db.commit()

        created = generate_for_document(
            db, doc, chunks, spec, task.mode, task.difficulty, task.focus, scope, on_progress
        )
        db.flush()  # 取得题目 id
        task.generated_question_ids = json.dumps([q.id for q in created])
        task.done = task.total
        task.status = "done"
        db.commit()
    except Exception as e:  # 单批失败不致命，整体失败才记 error
        try:
            db.rollback()
            t = db.get(DocTask, task_id)
            if t:
                t.status = "failed"
                t.error = str(e)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------- 接口 ----------------

@router.post("", response_model=DocumentOut)
def upload_document(
    file: UploadFile = File(...),
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> DocumentOut:
    ext = _ext(file.filename)
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{ext or '未知'}，支持 {', '.join(sorted(ALLOWED_EXT))}",
        )

    os.makedirs(settings.doc_storage_dir, exist_ok=True)
    tmp = os.path.join(settings.doc_storage_dir, f"tmp_{c.id}_{datetime.now(timezone.utc).timestamp()}.{ext}")
    try:
        with open(tmp, "wb") as f:
            shutil.copyfileobj(file.file, f)

        size_mb = os.path.getsize(tmp) / 1024 / 1024
        if size_mb > settings.doc_max_mb:
            raise HTTPException(400, f"文件过大（{size_mb:.1f}MB），上限 {settings.doc_max_mb}MB")

        parsed = doc_parser.parse_document(tmp, ext)
        if parsed["no_text_layer"]:
            raise HTTPException(400, "该 PDF 没有文字层（可能是扫描件），请上传可复制文本的版本")

        text = parsed["text"]
        if len(text) > settings.doc_max_chars:
            text = text[: settings.doc_max_chars]
        if not text.strip():
            raise HTTPException(400, "未能从文件中提取到文本")

        chunks = doc_parser.split_chunks(text, settings.doc_chunk_size, settings.doc_chunk_overlap)

        # A3：storage_path 留空且解析后删除原文 —— 只保留切片，降低版权风险
        doc = Document(
            candidate_id=c.id,
            title=file.filename or "未命名资料",
            file_type=ext,
            char_count=len(text),
            page_count=parsed["page_count"],
            chunk_count=len(chunks),
            status="parsed",
            storage_path="",
        )
        db.add(doc)
        db.flush()
        for ch in chunks:
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    seq=ch["seq"],
                    content=ch["content"],
                    char_count=ch["char_count"],
                    heading_path=ch["heading_path"],
                )
            )
        db.commit()
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

    # #26 P2：向量化走独立池，避免与出题任务互抢 worker 造成互等
    submit(_embed_document, doc.id, pool=EMBED)
    return _doc_out(db, doc)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    docs = (
        db.query(Document)
        .filter(Document.candidate_id == c.id)
        .order_by(Document.created_at.desc())
        .all()
    )
    return [_doc_out(db, d) for d in docs]


@router.get("/{doc_id}", response_model=DocumentDetailOut)
def get_document(
    doc_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> DocumentDetailOut:
    doc = db.get(Document, doc_id)
    if doc is None or doc.candidate_id != c.id:
        raise HTTPException(404, "资料不存在")
    base = _doc_out(db, doc)
    rows = (
        db.query(DocumentChunk.heading_path)
        .filter(DocumentChunk.document_id == doc.id, DocumentChunk.heading_path.isnot(None))
        .distinct()
        .all()
    )
    headings = [r[0] for r in rows if r[0]]
    return DocumentDetailOut(**base.model_dump(), headings=headings)


@router.delete("/{doc_id}")
def delete_document(
    doc_id: int,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> dict:
    """A5：级联删除该资料生成的个人题（questions.doc_id 有外键）。"""
    doc = db.get(Document, doc_id)
    if doc is None or doc.candidate_id != c.id:
        raise HTTPException(404, "资料不存在")

    removed = db.query(Question).filter(Question.doc_id == doc_id).count()
    db.query(Question).filter(Question.doc_id == doc_id).delete()
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
    db.delete(doc)
    db.commit()
    return {"deleted": True, "removed_questions": removed}


@router.post("/{doc_id}/generate", response_model=GenerateOut)
def generate(
    doc_id: int,
    body: GenerateIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> GenerateOut:
    doc = db.get(Document, doc_id)
    if doc is None or doc.candidate_id != c.id:
        raise HTTPException(404, "资料不存在")
    if doc.status != "parsed":
        raise HTTPException(400, "资料尚未解析完成")

    # 成本硬约束：每日出题次数
    today = date.today().isoformat()
    used = sum(
        1 for t in db.query(DocTask).filter(DocTask.candidate_id == c.id).all() if _local_day(t.created_at) == today
    )
    if used >= settings.doc_daily_gen_limit:
        raise HTTPException(429, f"今日出题次数已用完（{settings.doc_daily_gen_limit} 次），明天再来")

    spec = normalize_spec([s.model_dump() for s in body.spec])
    if not spec:
        raise HTTPException(400, "请至少指定一种题型与题量")

    total = sum(i["count"] for i in spec)
    task = DocTask(
        candidate_id=c.id,
        document_id=doc.id,
        mode=body.mode if body.mode in ("paper", "spot") else "paper",
        spec=json.dumps(spec, ensure_ascii=False),
        difficulty=body.difficulty,
        scope=json.dumps(body.scope, ensure_ascii=False) if body.scope else None,
        focus=body.focus,
        total=total,
    )
    db.add(task)
    db.commit()

    submit(_run_task, task.id)
    return GenerateOut(task_id=task.id, total=total)
