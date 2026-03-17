"""doc_chunk_embedding_and_task_mode

**幂等化（2026-05-26）**：0001_initial 以 `Base.metadata.create_all()` 建立**当前 models 的全量**
schema，已包含本迁移要加的列。全新库上必须跳过已存在的列，否则 `alembic upgrade head`
会因 duplicate column 失败。
"""
from alembic import op
import sqlalchemy as sa


revision = '6c138b351314'
down_revision = '12ead2620c5f'
branch_labels = None
depends_on = None


def _cols(bind, table):
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    doc_task_cols = _cols(bind, 'doc_tasks')
    chunk_cols = _cols(bind, 'document_chunks')

    with op.batch_alter_table('doc_tasks', schema=None) as batch_op:
        if 'mode' not in doc_task_cols:
            batch_op.add_column(sa.Column('mode', sa.String(length=16), nullable=False))
        if 'scope' not in doc_task_cols:
            batch_op.add_column(sa.Column('scope', sa.Text(), nullable=True))

    with op.batch_alter_table('document_chunks', schema=None) as batch_op:
        if 'heading_path' not in chunk_cols:
            batch_op.add_column(sa.Column('heading_path', sa.String(length=512), nullable=True))
        if 'embedding' not in chunk_cols:
            batch_op.add_column(sa.Column('embedding', sa.LargeBinary(), nullable=True))
        if 'embed_status' not in chunk_cols:
            batch_op.add_column(sa.Column('embed_status', sa.String(length=16), nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    doc_task_cols = _cols(bind, 'doc_tasks')
    chunk_cols = _cols(bind, 'document_chunks')

    with op.batch_alter_table('document_chunks', schema=None) as batch_op:
        if 'embed_status' in chunk_cols:
            batch_op.drop_column('embed_status')
        if 'embedding' in chunk_cols:
            batch_op.drop_column('embedding')
        if 'heading_path' in chunk_cols:
            batch_op.drop_column('heading_path')

    with op.batch_alter_table('doc_tasks', schema=None) as batch_op:
        if 'scope' in doc_task_cols:
            batch_op.drop_column('scope')
        if 'mode' in doc_task_cols:
            batch_op.drop_column('mode')
