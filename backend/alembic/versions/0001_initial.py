"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21
"""

from alembic import op

from app.db import Base
from app import models  # noqa: F401  确保元数据含全部表

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 以 SQLAlchemy 元数据为单一事实来源创建全部表（dev/test 用 init_db 同构）。
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
