"""kb_task_and_source_chunk

知识库出题独立任务表（替代模块级内存字典）+ 题目溯源切片列。

Revision ID: 7a1c0ffee123
Revises: 6c138b351314
Create Date: 2026-05-25 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '7a1c0ffee123'
down_revision = '6c138b351314'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'kb_tasks',
        sa.Column('task_id', sa.String(length=64), primary_key=True),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('done', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('generated_question_ids', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id']),
        sa.Index('ix_kb_tasks_candidate_id', 'candidate_id'),
    )
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_chunk', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_column('source_chunk')
    op.drop_table('kb_tasks')
