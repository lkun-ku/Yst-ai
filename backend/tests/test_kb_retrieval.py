"""跨文档知识库检索服务测试（TDD）：混合检索 + RRF 融合 + 三级降级 + 考生隔离。

检索服务在 fake embedding 下完全离线可测（embed_one 确定性伪向量）。
使用唯一 candidate 子树（9002/9003）+ 作用域清理，避免与其它持久化测试数据冲突。
"""
import pytest
from sqlalchemy import text

from app.db import SessionLocal, init_db  # noqa: F401
from app.models import Candidate, Document, DocumentChunk
from app.services.embedding import embed_one, encode_vector
from app.services.kb_retrieval import (
    _is_pg,
    heading_bonus,
    keyword_rank,
    load_chunks,
    rrf,
    retrieve_by_scope,
    vector_rank,
)

CAND_A = 9002
CAND_B = 9003


def _add_candidate(db, cid, unionid=None):
    db.add(Candidate(id=cid, unionid=unionid or f"u{cid}"))
    db.flush()


def _add_doc(db, doc_id, cand_id, title="doc"):
    db.add(
        Document(
            id=doc_id,
            candidate_id=cand_id,
            title=title,
            file_type="txt",
            storage_path=f"./{doc_id}",
        )
    )
    db.flush()


def _add_chunk(db, doc_id, seq, content, heading=None, with_vec=True):
    vec = embed_one(content) if with_vec else None
    db.add(
        DocumentChunk(
            document_id=doc_id,
            seq=seq,
            content=content,
            heading_path=heading,
            char_count=len(content),
            embedding=encode_vector(vec) if vec else None,
            embed_status="ok" if vec else "failed",
        )
    )
    db.flush()


@pytest.fixture
def kb_session(db_session):
    yield db_session
    db_session.rollback()
    db_session.execute(
        text(
            "DELETE FROM document_chunks WHERE document_id IN "
            "(SELECT id FROM documents WHERE candidate_id IN (:a, :b))"
        ),
        {"a": CAND_A, "b": CAND_B},
    )
    db_session.execute(
        text("DELETE FROM documents WHERE candidate_id IN (:a, :b)"),
        {"a": CAND_A, "b": CAND_B},
    )
    db_session.execute(
        text("DELETE FROM candidates WHERE id IN (:a, :b)"),
        {"a": CAND_A, "b": CAND_B},
    )
    db_session.commit()


class TestLoadChunks:
    def test_跨文档加载全部切片(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_doc(kb_session, 90020, CAND_A)
        _add_doc(kb_session, 90030, CAND_A)
        _add_chunk(kb_session, 90020, 0, "教育的本质", heading="第一章")
        _add_chunk(kb_session, 90030, 0, "汽车发动机原理", heading="第二章")
        kb_session.commit()
        chunks = load_chunks(kb_session, CAND_A)
        assert len(chunks) == 2
        assert all(c["document_id"] in (90020, 90030) for c in chunks)

    def test_按考生隔离(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_candidate(kb_session, CAND_B)
        _add_doc(kb_session, 90020, CAND_A)
        _add_doc(kb_session, 90030, CAND_B)
        _add_chunk(kb_session, 90020, 0, "A 的私有内容")
        _add_chunk(kb_session, 90030, 0, "B 的私有内容")
        kb_session.commit()
        assert len(load_chunks(kb_session, CAND_A)) == 1
        assert len(load_chunks(kb_session, CAND_B)) == 1
        assert load_chunks(kb_session, CAND_A)[0]["content"] == "A 的私有内容"


class TestChannels:
    def test_vector_rank_相关在前(self):
        chunks = [
            {"has_vec": True, "embedding": embed_one("教育的本质")},
            {"has_vec": True, "embedding": embed_one("汽车发动机")},
        ]
        ranked = vector_rank(embed_one("教育的本质"), chunks)
        assert ranked[0][1] == 0

    def test_keyword_rank_命中计分(self):
        chunks = [{"content": "教育的本质是培养人"}, {"content": "发动机原理"}]
        ranked = keyword_rank("教育的本质", chunks)
        assert ranked and ranked[0][1] == 0

    def test_rrf_融合多路(self):
        # 两通道都排 0 在前、1 在后 → chunk0 融合分高于 chunk1
        fused = rrf([[(1.0, 0), (0.5, 1)], [(0.8, 0), (0.3, 1)]])
        assert fused[0] > fused[1]

    def test_heading_bonus_标题命中(self):
        chunks = [{"heading_path": "第一章 教育基础"}, {"heading_path": None}]
        bonus = heading_bonus("教育基础", chunks)
        assert bonus.get(0, 0) > 0
        assert 1 not in bonus


class TestRetrieveByScope:
    def test_跨文档召回相关切片(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_doc(kb_session, 90020, CAND_A)
        _add_doc(kb_session, 90030, CAND_A)  # 第二份文档（跨文档场景）
        _add_chunk(kb_session, 90020, 0, "无关内容：美食烹饪技巧", heading="第三章")
        _add_chunk(kb_session, 90030, 0, "教育的本质是培养人的社会活动", heading="第一章")
        kb_session.commit()
        got = retrieve_by_scope(kb_session, CAND_A, "教育的本质", k=1)
        assert got and "教育" in got[0]["content"]

    def test_返回分值字段完整(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_doc(kb_session, 90020, CAND_A)
        _add_chunk(kb_session, 90020, 0, "教育的本质是培养人", heading="第一章 教育")
        kb_session.commit()
        got = retrieve_by_scope(kb_session, CAND_A, "教育的本质", k=1)
        assert "fusion_score" in got[0]
        assert "vector_score" in got[0]
        assert "keyword_score" in got[0]
        assert "heading_bonus" in got[0]
        assert "embedding" not in got[0]  # 内部键已剔除

    def test_无向量时降级关键词(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_doc(kb_session, 90020, CAND_A)
        # 全部无 embedding → 走关键词通道
        _add_chunk(kb_session, 90020, 0, "教育的本质是培养人的社会活动", with_vec=False)
        _add_chunk(kb_session, 90020, 1, "汽车发动机原理", with_vec=False)
        kb_session.commit()
        got = retrieve_by_scope(kb_session, CAND_A, "教育的本质", k=1)
        assert got and "教育" in got[0]["content"]

    def test_两级皆空时均匀采样兜底(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_doc(kb_session, 90020, CAND_A)
        for i in range(10):
            _add_chunk(kb_session, 90020, i, f"完全无关的内容{i}", with_vec=False)
        kb_session.commit()
        got = retrieve_by_scope(kb_session, CAND_A, "量子物理前沿", k=3)
        assert len(got) == 3
        # 均匀采样覆盖首尾，而非只取前三
        assert got[0]["seq"] == 0
        assert got[-1]["seq"] > 3

    def test_考生隔离不串档(self, kb_session):
        _add_candidate(kb_session, CAND_A)
        _add_candidate(kb_session, CAND_B)
        _add_doc(kb_session, 90020, CAND_A)
        _add_doc(kb_session, 90030, CAND_B)
        _add_chunk(kb_session, 90020, 0, "教育的本质", heading="第一章")
        _add_chunk(kb_session, 90030, 0, "教育的本质被另一考生占用", heading="第一章")
        kb_session.commit()
        got = retrieve_by_scope(kb_session, CAND_A, "教育的本质", k=5)
        assert len(got) == 1
        assert got[0]["content"] == "教育的本质"


# ---------------- 工单 14：分发冒烟（SQLite 下应走内存 numpy 路径，不触发 pgvector） ----------------


def test_分发_SQLite下is_pg返回False(db_session):
    """SQLite 引擎上 _is_pg 必须 False；retrieve_by_scope 走原内存路径。"""
    assert _is_pg(db_session) is False
    # 没有数据时直接返回空，不应触发 PG 分支或 pgvector SQL
    out = retrieve_by_scope(db_session, 1_000_001, "anything", k=3)
    assert out == []
