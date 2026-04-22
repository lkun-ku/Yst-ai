"""streak_and_mock_exam

**幂等化（2026-03-29）**：0001_initial 以 `Base.metadata.create_all()` 建立**当前 models 的全量**
schema，已包含 streaks / streak_makeups 表与 sessions 的 mode 等列。全新库上必须跳过已存在的
对象，否则 `alembic upgrade head` 会因 duplicate table / duplicate column 失败。
"""
from alembic import op
import sqlalchemy as sa


revision = '1b007f4825a3'
down_revision = '6c138b351314'
branch_labels = None
depends_on = None


def _insp(bind):
    return sa.inspect(bind)


def _cols(insp, table):
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = _insp(bind)

    if not insp.has_table('streak_makeups'):
        op.create_table(
            'streak_makeups',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('candidate_id', sa.Integer(), nullable=False),
            sa.Column('missed_date', sa.String(length=10), nullable=False),
            sa.Column('used_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('candidate_id', 'missed_date', name='uq_streak_makeup'),
        )
        with op.batch_alter_table('streak_makeups', schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f('ix_streak_makeups_candidate_id'), ['candidate_id'], unique=False
            )

    if not insp.has_table('streaks'):
        op.create_table(
            'streaks',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('candidate_id', sa.Integer(), nullable=False),
            sa.Column('current', sa.Integer(), nullable=False),
            sa.Column('max', sa.Integer(), nullable=False),
            sa.Column('last_date', sa.String(length=10), nullable=True),
            sa.Column('cards', sa.Integer(), nullable=False),
            sa.Column('total_days', sa.Integer(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('streaks', schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f('ix_streaks_candidate_id'), ['candidate_id'], unique=True
            )

    # SQLite 不允许给已有数据的表加 NOT NULL 且无默认值的列，故显式 server_default
    sess_cols = _cols(insp, 'sessions')
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        if 'mode' not in sess_cols:
            batch_op.add_column(
                sa.Column('mode', sa.String(length=16), nullable=False, server_default='normal')
            )
        if 'duration_sec' not in sess_cols:
            batch_op.add_column(
                sa.Column('duration_sec', sa.Integer(), nullable=False, server_default='0')
            )
        if 'deadline_at' not in sess_cols:
            batch_op.add_column(sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True))
        if 'timeout' not in sess_cols:
            batch_op.add_column(
                sa.Column('timeout', sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = _insp(bind)
    sess_cols = _cols(insp, 'sessions')

    with op.batch_alter_table('sessions', schema=None) as batch_op:
        if 'timeout' in sess_cols:
            batch_op.drop_column('timeout')
        if 'deadline_at' in sess_cols:
            batch_op.drop_column('deadline_at')
        if 'duration_sec' in sess_cols:
            batch_op.drop_column('duration_sec')
        if 'mode' in sess_cols:
            batch_op.drop_column('mode')

    if insp.has_table('streaks'):
        with op.batch_alter_table('streaks', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_streaks_candidate_id'))
        op.drop_table('streaks')

    if insp.has_table('streak_makeups'):
        with op.batch_alter_table('streak_makeups', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_streak_makeups_candidate_id'))
        op.drop_table('streak_makeups')
