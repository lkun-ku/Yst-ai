"""official_corpus_namespace

为官方语料（考纲 / 法条 / rubric）建立命名空间：

1. `documents` 新增 `is_official`（`NOT NULL DEFAULT false` + 索引）
2. `documents.candidate_id` 改为**可空**

**为什么 `candidate_id` 用 NULL 而不是哨兵 id**：官方语料**不属于任何考生**。
若给它塞一个哨兵 `candidate_id`，那么任何「按 candidate_id 查资料」的既有查询
都会把官方语料一并捞出来 —— 那正是本项目最该避免的泄漏路径。用 NULL 则
天然不匹配任何用户（`candidate_id == x` 对 NULL 行为假）。

**权限过滤必须在检索阶段完成**，不是先检索后过滤：后者会让无权文档先进入候选集，
挤占 top-k 并污染 RRF 排名，而且内容**已经进了上下文**。改动落在
`kb_retrieval.load_chunks_for_scope` 与 `retrieve_by_scope_pg`。

**只建 schema，不灌数据**：语料是参考数据，由 `kb_corpus.ingest_official_corpus()`
幂等写入 —— 把上百条法条冻死在迁移文件里会让考纲修订变成写迁移。
升级后须显式执行：

    python -m app.services.kb_corpus     # 或 scripts 入口

**幂等化**：与 6c138b351314 / b7f1c2d3e4a5 同理 —— `0001_initial` 以 `create_all`
建立**当前 models 的全量** schema，全新库上这些对象已存在，每一步都要先探测再执行。

**方言差异**：
- Postgres：`ADD COLUMN ... NOT NULL DEFAULT false` 与 `ALTER COLUMN DROP NOT NULL` 都是简单 ALTER；
- SQLite：`alter_column(nullable=True)` 需 batch 重建表，由 `render_as_batch=True` 处理。
"""
from alembic import op
import sqlalchemy as sa


revision = 'd2f4a6b8c0e1'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def _cols(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    doc_cols = _cols(bind, 'documents')

    with op.batch_alter_table('documents', schema=None) as batch_op:
        if 'is_official' not in doc_cols:
            # server_default 保证存量行填 false，同时让「新建库」与「老库迁移」两条路径一致。
            batch_op.add_column(
                sa.Column(
                    'is_official',
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if 'candidate_id' in doc_cols:
            batch_op.alter_column(
                'candidate_id', existing_type=sa.INTEGER(), nullable=True
            )

    if 'ix_documents_is_official' not in _indexes(bind, 'documents'):
        op.create_index('ix_documents_is_official', 'documents', ['is_official'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    doc_cols = _cols(bind, 'documents')

    # 官方语料的 candidate_id 为 NULL，若直接把列改回 NOT NULL 会失败 —— 先清掉它们。
    # 这是下迁的**数据损失**，故在 docstring 与返回值中显式说明，不静默处理。
    if 'is_official' in doc_cols:
        op.execute('DELETE FROM documents WHERE is_official = 1')

    with op.batch_alter_table('documents', schema=None) as batch_op:
        if 'candidate_id' in doc_cols:
            batch_op.alter_column(
                'candidate_id', existing_type=sa.INTEGER(), nullable=False
            )
        if 'is_official' in doc_cols:
            batch_op.drop_column('is_official')

    if 'ix_documents_is_official' in _indexes(bind, 'documents'):
        op.drop_index('ix_documents_is_official', table_name='documents')
