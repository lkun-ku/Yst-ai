"""知识库出题路由分发测试：默认路线③(graph) / 显式路线②(handwritten) / 非法 route 400。

分发逻辑抽成 _select_generator 纯函数，单测无需真实 embedding / LLM / DB；
400 用例走真实端点（含依赖覆盖），验证 route 白名单校验。
"""
from unittest.mock import MagicMock

from app.db import SessionLocal, get_db
from app.deps import get_current_candidate
from app.main import app
from app.models import Candidate
from app.routers import kb

CAND = 9101  # 唯一子树，避免与其它测试数据冲突


def test_默认route走路线三():
    g, h = MagicMock(), MagicMock()
    # 直接替换模块层生成函数，验证 _select_generator 的选择结果
    import app.routers.kb as kbmod

    kbmod.kb_graph.generate_by_scope_graph = g
    kbmod.kb_generate.generate_by_scope = h
    assert kb._select_generator("graph") is g
    assert kb._select_generator("handwritten") is h


def test_route_非法值返回400(client):
    app.dependency_overrides[get_current_candidate] = lambda: Candidate(id=CAND)

    def _db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    try:
        r = client.post(
            "/api/kb/generate",
            json={"scope": "x", "spec": [{"type": "single", "count": 1}], "route": "bogus"},
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
