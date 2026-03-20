"""kb streaming: add cancel_requested/request_json to kb_tasks and create kb_task_events

Revision ID: abb8b649ff92
Revises: b2c3d4e5f6a7
Create Date: 2026-05-27 12:00:49.609471
"""
from alembic import op
import sqlalchemy as sa


revision = 'abb8b649ff92'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('kb_tasks', schema=None) as batch_op:
        # server_default 必需：SQLite 的 batch 模式会重建表，已有行需要填充值
        batch_op.add_column(
            sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('request_json', sa.Text(), nullable=True))

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
