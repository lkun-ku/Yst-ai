"""kb_task_and_source_chunk

知识库出题独立任务表（替代模块级内存字典）+ 题目溯源切片列。

**幂等化（2026-03-29）**：0001_initial 以 `Base.metadata.create_all()` 建立**当前 models 的全量**
schema，已包含 kb_tasks 表与 questions.source_chunk 列。全新库上必须跳过已存在的对象，
否则 `alembic upgrade head` 会因 duplicate table / duplicate column 失败。
"""
from alembic import op
import sqlalchemy as sa


revision = '7a1c0ffee123'
down_revision = '6c138b351314'
branch_labels = None
depends_on = None


def _cols(insp, table):
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table('kb_tasks'):
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

    q_cols = _cols(insp, 'questions')
    if 'source_chunk' not in q_cols:
        with op.batch_alter_table('questions', schema=None) as batch_op:
            batch_op.add_column(sa.Column('source_chunk', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'source_chunk' in _cols(insp, 'questions'):
        with op.batch_alter_table('questions', schema=None) as batch_op:
            batch_op.drop_column('source_chunk')
    if insp.has_table('kb_tasks'):
        op.drop_table('kb_tasks')
