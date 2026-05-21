"""kb streaming: add cancel_requested/request_json to kb_tasks and create kb_task_events

Revision ID: abb8b649ff92
Revises: b2c3d4e5f6a7
Create Date: 2026-04-04 12:00:49.609471
"""
from alembic import op
import sqlalchemy as sa


revision = 'abb8b649ff92'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """幂等化（2026-06-12）—— 与同链的 `7a1c0ffee123` 是**同一个原因**。

    `0001_initial` 用 `Base.metadata.create_all()` 建的是**当前 models 的全量** schema，
    所以 `kb_tasks.cancel_requested` / `request_json` 与 `kb_task_events` 表**早已存在**。
    本迁移原先无条件 `add_column` / `create_table`，于是**在全新库上必然失败**——实测：

        ProgrammingError: column "cancel_requested" of relation "kb_tasks" already exists

    也就是说**从头建库走不通**（新部署 / CI 从零起会挂）。一直没暴露是因为日常走的是
    `create_all`（AUTO_MIGRATE），**Alembic 链大概从没在真实库上从头跑过** ——
    这条是本机 PG 复验时才撞出来的。

    补守卫而**不是删掉本迁移**：对"列已存在"的库，本迁移的作用已被那次 `create_all` 覆盖，
    跳过等价；对真正的老库（列还不存在），它照旧生效。
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table('kb_tasks'):
        cols = {c['name'] for c in insp.get_columns('kb_tasks')}
        adds = []
        # server_default 必需：SQLite 的 batch 模式会重建表，已有行需要填充值
        if 'cancel_requested' not in cols:
            adds.append(
                sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false())
            )
        if 'request_json' not in cols:
            adds.append(sa.Column('request_json', sa.Text(), nullable=True))
        if adds:
            with op.batch_alter_table('kb_tasks', schema=None) as batch_op:
                for col in adds:
                    batch_op.add_column(col)

    if not insp.has_table('kb_task_events'):
        op.create_table(
            'kb_task_events',
            sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('task_id', sa.String(length=64), nullable=False),
            sa.Column('type', sa.String(length=16), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('detail', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['task_id'], ['kb_tasks.task_id']),
            sa.PrimaryKeyConstraint('seq'),
        )
        op.create_index(op.f('ix_kb_task_events_task_id'), 'kb_task_events', ['task_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_kb_task_events_task_id'), table_name='kb_task_events')
    op.drop_table('kb_task_events')
    with op.batch_alter_table('kb_tasks', schema=None) as batch_op:
        batch_op.drop_column('request_json')
        batch_op.drop_column('cancel_requested')
