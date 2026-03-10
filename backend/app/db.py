from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .config import settings
from .models import Base


connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=True, autocommit=False, future=True)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """幂等的 schema 引导（dev/test 用）。

    生产环境使用 Alembic（alembic/ 下已配置，target_metadata=Base.metadata）。
    """
    import app.models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)
