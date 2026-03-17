"""路线② 手写质量闭环出题测试（TDD）：happy path / 进度回调 / 闭环开关 / 降级 / 路由注册。

全部在 FakeLLMClient 下离线可测（ask 返回确定性伪响应）。
使用唯一 candidate 子树（9001）与作用域清理，避免与其它用例的持久化测试数据冲突。
"""
import pytest
from sqlalchemy import text

from app.db import SessionLocal  # noqa: F401
from app.main import app  # noqa: F401  (导入即触发全部 router 注册，捕捉接线错误)
from app.models import Candidate, Document, DocumentChunk, Module
from app.services.embedding import embed_one, encode_vector
from app.services.kb_generate import generate_by_scope
from app.services.llm_client import FakeLLMClient

CAND = 9001  # 唯一子树，避免与其它测试残留数据冲突


def _add_candidate(db, cid):
    db.add(Candidate(id=cid, unionid=f"u{cid}"))
    db.flush()


def _add_doc(db, did, cand_id):
    db.add(
        Document(
            id=did, candidate_id=cand_id, title=f"doc{did}",
            file_type="txt", storage_path=f"./{did}",
        )
    )
    db.flush()


def _seed(db, cand_id=CAND, n_docs=2, chunks_per=2):
    _add_candidate(db, cand_id)
    cid = 0
    for d in range(n_docs):
        _add_doc(db, cand_id + d, cand_id)
        for s in range(chunks_per):
            if d == 0 and s == 0:
                content = "教育的本质是培养人的社会活动。知识点：个体社会化。"
            else:
                content = f"无关内容{cid}：烹饪与美食的实用技巧。"
            db.add(
                DocumentChunk(
                    document_id=cand_id + d,
                    seq=s,
                    content=content,
                    heading_path="第一章 教育基础" if (d == 0 and s == 0) else "其他",
                    char_count=len(content),
                    embedding=encode_vector(embed_one(content)),
                    embed_status="ok",
                )
            )
            cid += 1
    db.commit()


@pytest.fixture
def kb_gen_session(db_session):
    yield db_session
    db_session.rollback()
    # 仅清理本用例子树，不触碰其他用例数据
    db_session.execute(
        text(
            "DELETE FROM document_chunks WHERE document_id IN "
            "(SELECT id FROM documents WHERE candidate_id = :c)"
        ),
        {"c": CAND},
    )
    db_session.execute(text("DELETE FROM documents WHERE candidate_id = :c"), {"c": CAND})
    db_session.execute(text("DELETE FROM candidates WHERE id = :c"), {"c": CAND})
    db_session.commit()


class TestKbGenerate:
    def test_生成返回个人题(self, kb_gen_session):
        _seed(kb_gen_session, CAND)
        created = generate_by_scope(
            kb_gen_session, CAND, "教育的本质", [{"type": "single", "count": 3}],
            enable_loop=True, client=FakeLLMClient(),
        )
        assert len(created) == 3
        assert all(q.module == Module.PERSONAL for q in created)
        assert all(q.owner_candidate_id == CAND for q in created)
        assert all(q.doc_id is None for q in created)

    def test_进度回调(self, kb_gen_session):
        _seed(kb_gen_session, CAND)
        seen = []
        generate_by_scope(
            kb_gen_session, CAND, "教育的本质", [{"type": "single", "count": 2}],
            client=FakeLLMClient(), on_progress=lambda d, t: seen.append((d, t)),
        )
        assert seen and seen[-1][0] >= 2

    def test_关闭闭环仍可用(self, kb_gen_session):
        _seed(kb_gen_session, CAND)
        created = generate_by_scope(
            kb_gen_session, CAND, "教育的本质", [{"type": "single", "count": 2}],
            enable_loop=False, client=FakeLLMClient(),
        )
        assert len(created) == 2

    def test_LLM全不可用_降级不崩(self, kb_gen_session):
        class NullAsk(FakeLLMClient):
            def ask(self, prompt, timeout=30):
                return None

        _seed(kb_gen_session, CAND)
        created = generate_by_scope(
            kb_gen_session, CAND, "教育的本质", [{"type": "single", "count": 2}],
            client=NullAsk(),
        )
        assert created == []  # 降级：无输出但不抛异常

    def test_评分LLM失败_退化为全量切片(self, kb_gen_session):
        class GradeNull(FakeLLMClient):
            def ask(self, prompt, timeout=30):
                if "【检索相关性评分】" in prompt:
                    return None
                return super().ask(prompt, timeout)

        _seed(kb_gen_session, CAND)
        created = generate_by_scope(
            kb_gen_session, CAND, "教育的本质", [{"type": "single", "count": 2}],
            client=GradeNull(),
        )
        assert len(created) == 2  # 相关性评分 LLM 失败 → 保留全部切片 → 正常生成


def test_app_注册_kb路由():
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/api/kb/generate" in paths
    assert "/api/kb/retrieve" in paths
