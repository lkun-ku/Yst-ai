"""daily_task_done_ids

**幂等化（2026-05-26）**：0001_initial 以 `Base.metadata.create_all()` 建立**当前 models 的全量**
schema，已包含 `done_ids` 列。全新库上必须跳过，否则 `alembic upgrade head` 会失败。

SQLite 不允许给已有数据的表加 NOT NULL 且无默认值的列，故显式 server_default。
"""
from alembic import op
import sqlalchemy as sa


revision = '9b438ae98caf'
down_revision = '1b007f4825a3'
branch_labels = None
depends_on = None


def _cols(bind, table):
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if 'done_ids' in _cols(bind, 'daily_tasks'):
        return
    with op.batch_alter_table('daily_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('done_ids', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    bind = op.get_bind()
    if 'done_ids' not in _cols(bind, 'daily_tasks'):
        return
    with op.batch_alter_table('daily_tasks', schema=None) as batch_op:
        batch_op.drop_column('done_ids')
