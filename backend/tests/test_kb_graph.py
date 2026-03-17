"""路线③ LangGraph StateGraph 编排出题测试（TDD）：happy path / 闭环开关 / 降级 / 改写分支收敛。

与 test_kb_generate 共享同一组断言，验证「框架 vs 手写编排」输出一致（控制变量）。
"""
import pytest
from sqlalchemy import text

from app.models import Candidate, Document, DocumentChunk, Module
from app.services.embedding import embed_one, encode_vector
from app.services.kb_graph import generate_by_scope_graph
from app.services.llm_client import FakeLLMClient

CAND = 9004  # 唯一子树


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
def kb_graph_session(db_session):
    yield db_session
    db_session.rollback()
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


class TestKbGraph:
    def test_生成返回个人题(self, kb_graph_session):
        _seed(kb_graph_session, CAND)
        created = generate_by_scope_graph(
            kb_graph_session, CAND, "教育的本质", [{"type": "single", "count": 3}],
            enable_loop=True, client=FakeLLMClient(),
        )
        assert len(created) == 3
        assert all(q.module == Module.PERSONAL for q in created)
        assert all(q.owner_candidate_id == CAND for q in created)
        assert all(q.doc_id is None for q in created)

    def test_关闭闭环仍可用(self, kb_graph_session):
        _seed(kb_graph_session, CAND)
        created = generate_by_scope_graph(
            kb_graph_session, CAND, "教育的本质", [{"type": "single", "count": 2}],
            enable_loop=False, client=FakeLLMClient(),
        )
        assert len(created) == 2

    def test_LLM全不可用_降级不崩(self, kb_graph_session):
        class NullAsk(FakeLLMClient):
            def ask(self, prompt, timeout=30):
                return None

        _seed(kb_graph_session, CAND)
        created = generate_by_scope_graph(
            kb_graph_session, CAND, "教育的本质", [{"type": "single", "count": 2}],
            client=NullAsk(),
        )
        assert created == []

    def test_图改写分支执行且收敛(self, kb_graph_session):
        # 评分始终判不相关 → 触发 rewrite 边；Fake 改写返回原文 → 立即终止循环，退化为全量切片
        class GradeFail(FakeLLMClient):
            def ask(self, prompt, timeout=30):
                if "【检索相关性评分】" in prompt:
                    return '{"relevant": false, "score": 0.1}'
                return super().ask(prompt, timeout)

        _seed(kb_graph_session, CAND)
        created = generate_by_scope_graph(
            kb_graph_session, CAND, "教育的本质", [{"type": "single", "count": 3}],
            client=GradeFail(),
        )
        # 收敛且不抛异常；退化为全量切片后正常生成
        assert len(created) == 3
