"""官方语料灌入的**向量化护栏**：失败了必须看得见，不许伪装成成功。

起因是实测（2026-03-04）：embedding 供应商（百炼）账户欠费返回 400，
而 `embed_one` 用 `except Exception: pass` 把它吞掉、退回 **64 维哈希词袋** ——
于是整批切片被标成 `embed_status='ok'`，检索看着"有向量"实则退化，且**无人报警**。
唯一把它顶出来的是 PG 那句难懂的 `expected 1024 dimensions, not 64`，
而在 **SQLite/dev 上它会静默通过**。这是"假装成功的失败"，比直接报错更危险。

本文件的断言就是那句承诺：**配了 real 却拿不到时 → `failed`，永不 `ok`。**
"""

from __future__ import annotations

import pytest

from app.models import Document, DocumentChunk
from app.services import embedding as emb
from app.services.kb_corpus import ingest_official_corpus

#: 一段法条形态的语料（按「第X条」切）。
_BODY = """---
law: 护栏测试法
short: 护栏测试法
---

第一条 为了开展护栏测试，制定本法。

第二条 任何单位不得把失败伪装成成功。

第三条 违反本法规定的，依法追究责任。
"""

_STORAGE_PATH = "laws/guard.md"


@pytest.fixture
def corpus_root(tmp_path):
    laws = tmp_path / "laws"
    laws.mkdir()
    (laws / "guard.md").write_text(_BODY, encoding="utf-8")
    yield tmp_path
    # 本会话库是共享的，灌进去的行必须带走，免得影响其它按数量断言的用例
    from app.db import SessionLocal

    with SessionLocal() as db:
        doc = (
            db.query(Document)
            .filter(Document.storage_path == _STORAGE_PATH, Document.is_official.is_(True))
            .one_or_none()
        )
        if doc is not None:
            db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(
                synchronize_session=False
            )
            db.delete(doc)
            db.commit()


def _chunks_of(corpus_root, db_session):
    return (
        db_session.query(DocumentChunk)
        .join(Document, Document.id == DocumentChunk.document_id)
        .filter(Document.storage_path == _STORAGE_PATH)
        .all()
    )


def _force_real(monkeypatch):
    """把配置钉成"确实在尝试用真 embedding"。"""
    monkeypatch.setattr(emb.settings, "embedding_mode", "real")
    monkeypatch.setattr(emb.settings, "embedding_api_base", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(emb.settings, "embedding_api_key", "test-key-not-real")


def test_real_失败时_explicit_failed_而不是_ok(corpus_root, db_session, monkeypatch):
    """**这条就是整个护栏的核心**。

    供应商欠费 / 鉴权失败 / 接口抖动，在这里都表现为"真接口抛异常"。
    旧行为：吞掉 → 退 64 维伪向量 → 标 `ok`（于是谁也发现不了）。
    新行为：明确 `failed` + `embedding is None` → 检索按既有三级降级到关键词，且台账上写得清清楚楚。
    """

    def _boom(_text):
        raise RuntimeError("HTTP Error 400: Bad Request（Arrearage）")

    _force_real(monkeypatch)
    monkeypatch.setattr(emb, "_real_embed", _boom)

    stats = ingest_official_corpus(db_session, root=corpus_root, embed=True)

    chunks = _chunks_of(corpus_root, db_session)
    assert chunks, "没灌进切片，本用例无从断言"
    assert stats["embed_failed"] == len(chunks)
    assert stats["embed_ok"] == 0, "接口都失败了，却有片被标成 ok —— 护栏没生效"
    assert {c.embed_status for c in chunks} == {"failed"}
    assert all(c.embedding is None for c in chunks), "失败却仍然写进了向量"


def test_维度与列声明不符时判为失败(corpus_root, db_session, monkeypatch):
    """模型换了维度（如 1024 → 3072）而列没跟上时，错误必须在**灌入阶段**说清楚。

    不拦的话它会一直走到 flush 才炸，而且报的是 PG 那句
    `expected 1024 dimensions, not 64` —— 看的人只会以为是数据库配置错。
    """
    _force_real(monkeypatch)
    monkeypatch.setattr(emb, "_real_embed", lambda _t: [0.1] * 64)
    monkeypatch.setattr(emb, "declared_dim", lambda: 1024)

    stats = ingest_official_corpus(db_session, root=corpus_root, embed=True)

    chunks = _chunks_of(corpus_root, db_session)
    assert stats["embed_failed"] == len(chunks)
    assert all(c.embed_status == "failed" for c in chunks)

    vec, source, reason = emb.strict_embed("随便一句")
    assert source == emb.EMBED_FAILED and vec is None
    assert "维度不符" in reason and "64" in reason and "1024" in reason


def test_刻意用fake模式时标记为fake而非ok(corpus_root, db_session, monkeypatch):
    """离线开发 / 测试用伪向量是**有意为之**，必须允许，但台账里要看得见。

    与"失败"的区别就在于此：一个是故意的、一个不是。混为一谈会让运维无法判断
    「这批检索质量差」是环境使然还是故障使然。
    """
    monkeypatch.setattr(emb.settings, "embedding_mode", "fake")

    stats = ingest_official_corpus(db_session, root=corpus_root, embed=True)

    chunks = _chunks_of(corpus_root, db_session)
    assert stats["embed_fake"] == len(chunks)
    assert stats["embed_ok"] == 0, "伪向量被标成了真向量"
    assert {c.embed_status for c in chunks} == {"fake"}
    # 伪向量本身还要能写进去：否则离线环境根本没法开发检索链路
    assert all(c.embedding is not None for c in chunks)


def test_真embedding成功时才是ok(corpus_root, db_session, monkeypatch):
    """正向用例：真向量拿到且维度相符 → `ok`。防止护栏写成"永远失败"。"""
    _force_real(monkeypatch)
    monkeypatch.setattr(emb, "_real_embed", lambda _t: [0.1] * 8)
    monkeypatch.setattr(emb, "declared_dim", lambda: 8)

    stats = ingest_official_corpus(db_session, root=corpus_root, embed=True)

    chunks = _chunks_of(corpus_root, db_session)
    assert stats["embed_ok"] == len(chunks)
    assert stats["embed_failed"] == 0
    assert {c.embed_status for c in chunks} == {"ok"}


def test_embed_false_仍是pending_且不算向量(corpus_root, db_session):
    """快速验证切分规则的路径不受护栏影响（既有约定，回归保护）。"""
    stats = ingest_official_corpus(db_session, root=corpus_root, embed=False)

    chunks = _chunks_of(corpus_root, db_session)
    assert stats["embed_pending"] == len(chunks)
    assert all(c.embed_status == "pending" and c.embedding is None for c in chunks)
