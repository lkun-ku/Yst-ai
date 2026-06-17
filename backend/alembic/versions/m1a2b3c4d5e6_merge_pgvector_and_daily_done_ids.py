"""merge_pgvector_and_daily_done_ids

合并两个并行 head，恢复单一 head，使 `alembic upgrade head` 可执行。

分叉来源：两支共同祖先 `6c138b351314`，一支走到 `a1b2c3d4e5f6`（工单 14 pgvector），
另一支走到 `9b438ae98caf`（daily_task_done_ids）。无 merge revision 时存在两个 head，
`alembic upgrade head` 会报 "Multiple head revisions are present"。

Revision ID: m1a2b3c4d5e6
Revises: a1b2c3d4e5f6, 9b438ae98caf
Create Date: 2026-03-04 02:30:00.000000
"""
revision = "m1a2b3c4d5e6"
down_revision = ("a1b2c3d4e5f6", "9b438ae98caf")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 合并点：两分支的 upgrade 已由各自迁移完成，此处无需额外 DDL
    pass


def downgrade() -> None:
    # 合并点：回退由各自迁移的 downgrade 处理
    pass
