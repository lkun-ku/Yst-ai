import os

# 必须在导入 app 之前设定，确保测试使用独立 SQLite 文件库。
os.environ["DATABASE_URL"] = "sqlite:///./.test_tmp.db"
os.environ["AUTO_MIGRATE"] = "true"
# 精排默认关闭：真实模型是 266MB 的本地下载，让单测依赖它有两重代价 ——
# 慢，且在一台没下过权重的机器上行为不同。需要验证重排链路的用例显式设
# RERANK_IMPL=fake 并用 FakeReranker 替身；**真实模型的收益由
# eval/retrieval_eval.py --rerank 测量**，不在单测里。
os.environ.setdefault("RERANK_IMPL", "off")
# 用户决策（2026-04-09）：测试使用**真实环境**（backend/.env 的 real 模式），
# 不强制 fake——真实模型下的出题/判分/复盘才可信。代价：测试变慢（doc 生成 30s+/次）。

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine, init_db


@pytest.fixture(scope="session", autouse=True)
def _reset_db():
    """会话级重置：每次测试运行使用干净库，避免跨运行数据累积导致唯一约束冲突。"""
    import app.models  # noqa: F401  确保元数据含全部表

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    if os.path.exists(".test_tmp.db"):
        os.remove(".test_tmp.db")


@pytest.fixture
def db_session():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
