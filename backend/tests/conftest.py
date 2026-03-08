import os

# 必须在导入 app 之前设定，确保测试使用独立 SQLite 文件库。
os.environ["DATABASE_URL"] = "sqlite:///./.test_tmp.db"
os.environ["AUTO_MIGRATE"] = "true"
os.environ["APP_ENV"] = "test"

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db


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
