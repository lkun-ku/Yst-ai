"""doc question models

文档出题模块：questions 表新增 owner_candidate_id / doc_id 列与 answer 扩为 Text；
documents / document_chunks / doc_tasks 三张新表由 0001_initial 的 create_all(metadata) 在建库时一并创建，
本迁移只负责既有 questions 表的增量变更，避免与 0001 重复建表。

**幂等化（2026-03-29）**：0001_initial 以 `Base.metadata.create_all()` 建立**当前 models 的全量**
schema，已包含本迁移要加的列/索引/外键。因此在全新库上本迁移必须跳过已存在的对象，
否则 `alembic upgrade head` 会因 duplicate column 失败（此前测试只走 create_all、从不跑 alembic，
故该问题长期未被发现）。
"""
from alembic import op
import sqlalchemy as sa


revision = '12ead2620c5f'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def _introspect(bind, table):
    """返回 (已有列名集合, 已有索引名集合)。表不存在时返回空集合。"""
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set(), set()
    cols = {c["name"] for c in insp.get_columns(table)}
    idxs = {i["name"] for i in insp.get_indexes(table)}
    return cols, idxs


def upgrade() -> None:
    bind = op.get_bind()
    cols, idxs = _introspect(bind, 'questions')
    added_any = False

    with op.batch_alter_table('questions', schema=None) as batch_op:
        if 'owner_candidate_id' not in cols:
            batch_op.add_column(sa.Column('owner_candidate_id', sa.Integer(), nullable=True))
            added_any = True
        if 'doc_id' not in cols:
            batch_op.add_column(sa.Column('doc_id', sa.Integer(), nullable=True))
            added_any = True
        batch_op.alter_column(
            'answer',
            existing_type=sa.VARCHAR(length=64),
            type_=sa.Text(),
            existing_nullable=False,
        )
        if 'ix_questions_doc_id' not in idxs:
            batch_op.create_index(batch_op.f('ix_questions_doc_id'), ['doc_id'], unique=False)
        if 'ix_questions_owner_candidate_id' not in idxs:
            batch_op.create_index(
                batch_op.f('ix_questions_owner_candidate_id'), ['owner_candidate_id'], unique=False
            )
        # 外键仅在本次确实新增了列时才建（已存在说明 create_all 连外键一并建好了）
        if added_any:
            batch_op.create_foreign_key('fk_questions_doc_id', 'documents', ['doc_id'], ['id'])
            batch_op.create_foreign_key(
                'fk_questions_owner_candidate_id', 'candidates', ['owner_candidate_id'], ['id']
            )


def downgrade() -> None:
    bind = op.get_bind()
    cols, idxs = _introspect(bind, 'questions')
    with op.batch_alter_table('questions', schema=None) as batch_op:
        if 'fk_questions_owner_candidate_id' in idxs or 'owner_candidate_id' in cols:
            try:
                batch_op.drop_constraint('fk_questions_owner_candidate_id', type_='foreignkey')
            except Exception:
                pass
        if 'doc_id' in cols:
            try:
                batch_op.drop_constraint('fk_questions_doc_id', type_='foreignkey')
            except Exception:
                pass
        if 'ix_questions_owner_candidate_id' in idxs:
            batch_op.drop_index(batch_op.f('ix_questions_owner_candidate_id'))
        if 'ix_questions_doc_id' in idxs:
            batch_op.drop_index(batch_op.f('ix_questions_doc_id'))
        batch_op.alter_column(
            'answer',
            existing_type=sa.Text(),
            type_=sa.VARCHAR(length=64),
            existing_nullable=False,
        )
        if 'owner_candidate_id' in cols:
            batch_op.drop_column('owner_candidate_id')
        if 'doc_id' in cols:
            batch_op.drop_column('doc_id')
