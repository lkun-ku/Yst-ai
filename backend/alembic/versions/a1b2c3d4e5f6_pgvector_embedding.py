"""pgvector_embedding

工单 14：生产 PG 启用 pgvector。给 document_chunks 加 VECTOR(1024) 列 + HNSW 索引；
回填数据从原 LargeBinary（float32 字节）转换为 vector。SQLite/dev 路径下 no-op，
原 LargeBinary 列继续作为内存检索输入。

设计要点：
- 仅在 dialect == postgresql 时执行，避免污染 dev.db（SQLite 不支持 vector 类型）
- CREATE EXTENSION IF NOT EXISTS vector —— 幂等，依赖腾讯云 PG 镜像预装 pgvector 0.8.x
- 只回填 1024 维（生产百炼 text-embedding-v3 默认维）；dev/fake 64 维行保持 NULL
- HNSW(m=16, ef_construction=64) —— 100k chunk 内召回 P95 < 10ms，符合 SPEC
- 旧 embedding 列保留（不删），便于回滚与跨版本兼容

Revision ID: a1b2c3d4e5f6
Revises: 7a1c0ffee123
Create Date: 2026-03-04 02:00:00.000000
"""
from __future__ import annotations

import struct

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "7a1c0ffee123"
branch_labels = None
depends_on = None


_EMBED_DIM = 1024  # 百炼 text-embedding-v3 默认维；其它维数据不迁移


def _is_pg(bind) -> bool:
    return bind.dialect.name == "postgresql"


def _embedding_is_vector(bind) -> bool:
    """判断 document_chunks.embedding 是否已是 pgvector 的 vector 类型。

    工单 20/W-5 合并双列后，models 里唯一的 embedding 列在 PG 上直接是
    `VECTOR(1024)`（由 0001_initial 的 create_all 建立）。此时本迁移当初
    「bytea embedding + 新增 embedding_vec」的双列设计不再需要，必须整体跳过：
    否则后面的 backfill 会对 vector 值执行 bytes() 而报错。
    """
    row = bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='document_chunks' AND column_name='embedding'"
        )
    ).fetchone()
    return bool(row and row[0] == "USER-DEFINED")


def upgrade() -> None:
    bind = op.get_bind()
    if not _is_pg(bind):
        return  # SQLite/dev: 维持 LargeBinary 列，路径不变

    # W-5：embedding 已是 vector（单列形态）→ 无需再建 embedding_vec，直接跳过
    if _embedding_is_vector(bind):
        return

    # 1) 启用 pgvector（幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 2) 新增 VECTOR(1024) 列（IF NOT EXISTS 允许重跑）
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN IF NOT EXISTS embedding_vec VECTOR(1024);"
    )

    # 3) 回填：LargeBinary(float32 little-endian, 1024 维) → vector
    rows = bind.execute(
        sa.text(
            "SELECT id, embedding FROM document_chunks "
            "WHERE embedding IS NOT NULL AND embedding_vec IS NULL"
        )
    ).fetchall()

    backfilled = 0
    for row in rows:
        rid = row[0]
        blob = bytes(row[1]) if row[1] else b""
        if len(blob) != _EMBED_DIM * 4:
            # 非 1024 维（dev/fake 64 维或历史其他维），跳过不迁移
            continue
        floats = struct.unpack(f"<{_EMBED_DIM}f", blob)
        vec_literal = "[" + ",".join(f"{x:.7f}" for x in floats) + "]"
        bind.execute(
            sa.text(
                "UPDATE document_chunks SET embedding_vec = (:v)::vector "
                "WHERE id = :id"
            ),
            {"v": vec_literal, "id": rid},
        )
        backfilled += 1

    # 4) HNSW 索引（cosine ops 对应余弦距离）
    op.execute(
        "CREATE INDEX IF NOT EXISTS document_chunks_embedding_vec_hnsw "
        "ON document_chunks USING hnsw (embedding_vec vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )

    # 迁移结果以 NOTICE 输出（alembic logger）
    op.execute(
        f"DO $$ BEGIN RAISE NOTICE 'pgvector backfill: % rows migrated', {backfilled}; "
        "END $$;"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _is_pg(bind):
        return
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_vec_hnsw;")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_vec;")
    # 不 DROP EXTENSION —— 同一实例上可能还有别的库在用 vector