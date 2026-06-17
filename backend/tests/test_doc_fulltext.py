"""资料"查看原文"：**优先给原文**，没有原文才回退重建，并且必须**如实标注**。

## 为什么钉这条

2026-03-04 实测反馈："查看资料看到的是切分后的，没法看原文"。根因有两条：
① 上传时原文被丢弃（A3 只删了**原始文件**，但连**解析后的文本**也没留）；
② 存量资料只能靠"按 seq 拼接切片"重建，而重建是**有损**的
   （实测 7980 → 7935 字，少掉的是被提取成 `heading_path` 的章节标题行）。

两条修完之后，最大的风险是**回退路径被当成原文展示** —— 用户拿它逐字核对，
发现少了标题行，会以为产品把资料弄坏了。所以这里同时钉"优先原文"与"回退要标注"。
"""

from __future__ import annotations

from app.models import Candidate, Document, DocumentChunk
from app.routers.documents import get_document_full

_ORIGINAL = "## 教育观\n素质教育是面向全体学生的教育。\n"


def _mk_doc(db, cid: int, *, content: str | None, chunks: list[str]) -> Document:
    doc = Document(
        candidate_id=cid, title="资料.md", file_type="md",
        char_count=len(content or ""), page_count=0, chunk_count=len(chunks),
        status="parsed", storage_path="", content=content,
    )
    db.add(doc)
    db.flush()
    for i, ch in enumerate(chunks):
        db.add(DocumentChunk(document_id=doc.id, seq=i, content=ch, char_count=len(ch)))
    db.commit()
    return doc


def _mk_cand(db, cid: int) -> Candidate:
    cand = Candidate(id=cid, unionid=f"full{cid}")
    db.add(cand)
    db.commit()
    return cand


def test_有原文时给原文并标注来源(db_session):
    cand = _mk_cand(db_session, 777001)
    doc = _mk_doc(db_session, cand.id, content=_ORIGINAL, chunks=["（切片内容与原文不同）"])

    out = get_document_full(doc.id, c=cand, db=db_session)

    assert out.source == "original", "上传时留存的原文优先 —— 这才是'看原文'"
    assert out.text == _ORIGINAL, "必须是原文本身，不能是重建"


def test_没有原文时回退重建并如实标注(db_session):
    """存量资料没有原文。回退没错，但**必须让前端知道这是重建**。"""
    cand = _mk_cand(db_session, 777002)
    doc = _mk_doc(db_session, cand.id, content=None, chunks=["甲乙丙丁戊己"])

    out = get_document_full(doc.id, c=cand, db=db_session)

    assert out.source == "restitched"
    assert out.text == "甲乙丙丁戊己"


def test_别人的资料看不到(db_session):
    """原文是用户上传的内容，越权读取是**隐私事故**，不只是功能问题。"""
    import pytest
    from fastapi import HTTPException

    owner = _mk_cand(db_session, 777003)
    other = _mk_cand(db_session, 777004)
    doc = _mk_doc(db_session, owner.id, content=_ORIGINAL, chunks=["x"])

    with pytest.raises(HTTPException) as ei:
        get_document_full(doc.id, c=other, db=db_session)
    assert ei.value.status_code == 404
