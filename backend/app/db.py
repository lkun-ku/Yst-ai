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

    - **建表**：生产环境使用 Alembic（alembic/ 下已配置，target_metadata=Base.metadata）。
    - **参考数据**：知识点树（知识考纲骨架）随建表一并幂等补齐。它不是业务数据而是
      **参考数据**——`questions.kp_id` 的解析、覆盖度统计的聚合都挂在它上面，
      缺了它新库上一切维度查询都会静默返回空。

      `ensure_knowledge_tree` 在已齐备时只做一次 SELECT、不写库，故可安全地在此重复调用。
      生产若以 `AUTO_MIGRATE=false` 启动（不经过本函数），则用
      `python scripts/seed_knowledge_tree.py` 显式写入（在 backend/ 下运行）。
    """
    import app.models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)

    from .seed.knowledge_tree import ensure_knowledge_tree

    db = SessionLocal()
    try:
        ensure_knowledge_tree(db)
    finally:
        db.close()
