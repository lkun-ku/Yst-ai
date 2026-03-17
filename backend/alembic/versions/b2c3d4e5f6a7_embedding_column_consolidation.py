"""embedding_column_consolidation

工单 20 / W-5：合并双列，收敛为单一向量字段。

背景：工单 14 为了实现「SQLite dev 与 PG 生产并行」，拆成了两列——
`embedding`（bytea，内存检索用）+ `embedding_vec`（vector，PG 检索用）。
代价是双写、存储翻倍、schema 易漂移。

W-5 收敛为**一列** `embedding`，用 `Vector(1024).with_variant(LargeBinary, "sqlite")`
按方言选择存储形态：PG 上是 `VECTOR(1024)`，SQLite 上是 blob。方言分发仍保留
（SQLite 无法做向量运算），但只有一份数据与一条写入路径。

本迁移负责：
1. 删除遗留的 `embedding_vec` 列及其索引（幂等；全新库上 a1b2c3d4e5f6 已跳过，
   此处仅作兜底）
2. HNSW 索引改建于合并后的 `embedding` 列

Revision ID: b2c3d4e5f6a7
Revises: m1a2b3c4d5e6
Create Date: 2026-05-26 07:00:00.000000
"""
from alembic import op


revision = "b2c3d4e5f6a7"
down_revision = "m1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite/dev：embedding 为 blob，无需 pgvector 索引

    # 1) 清理早期双列形态的遗留（幂等）
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_vec_hnsw;")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_vec;")

    # 2) HNSW 索引建于合并后的 embedding 列（幂等）
    op.execute(
        "CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_hnsw;")
    # 不恢复 embedding_vec 列：回退到双列形态无实质收益，
    # 若确需回退请走 a1b2c3d4e5f6 之前的历史版本。
