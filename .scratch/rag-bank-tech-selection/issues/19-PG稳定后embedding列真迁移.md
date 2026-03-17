# 19: embedding 列真迁移（合并双列 + 去双写）

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 16
- 关联：`backend/app/models.py`、`backend/alembic/versions/b2c3d4e5f6a7_embedding_column_consolidation.py`、`backend/app/routers/documents.py`、`docs/RAG路线三落地复盘.md` §5.2

## What to build

工单 14 原定「`document_chunks.embedding` 改 `VECTOR(1024)`」，实际实现为**新增** `embedding_vec`
并保留旧 `embedding`（bytea）。该形态是「同时支持 SQLite dev 与 PG 生产」的低风险解，但代价是
双写、存储翻倍、schema 易漂移。本票收敛为**一列**。

1. `embedding` 改为 `Vector(1024).with_variant(LargeBinary, "sqlite")`，删除 `embedding_vec`。
2. 写入路径去双写：按方言写单列（PG→vector 列表，SQLite→float32 字节）。
3. 迁移：删除遗留 `embedding_vec` 及其索引；HNSW 索引改建于 `embedding` 列。
4. **方言分发保留**（`_is_pg` / `retrieve_by_scope_pg`）。

## 验收清单

- [x] models 合并为单一 `embedding` 列（Vector with_variant LargeBinary）。
- [x] `documents._embed_document` 与 `run_eval._seed_dataset` 去双写，按方言写单列。
- [x] `retrieve_by_scope_pg` 的 SQL 改用 `embedding`（并保留 `CAST(:q AS vector)` 修复）。
- [x] 新增迁移 `b2c3d4e5f6a7` 清理 `embedding_vec` + 建 `document_chunks_embedding_hnsw`。
- [x] 迁移 `a1b2c3d4e5f6` 加保护：embedding 已是 vector 时跳过双列逻辑（否则 `bytes(vector)` 报错）。
- [x] 单一 head、全量 pytest 通过、真实 PG 上迁移与检索均正常。

## 状态

已落地（历史追溯）。**注意：工单原描述中「去掉方言分发」一项技术上不可行，已修正。**

> SQLite 无法执行向量运算，dev/test 全部跑 SQLite（230 个测试）。去掉方言分发会让内存检索
> 路径失效、测试全挂。`with_variant(LargeBinary, "sqlite")` 正是为「同一列按方言取不同形态」
> 设计，合并后仍必须保留分发。

### 真实 PG 验证（TencentDB 18.6 + pgvector 0.8.2）
```
alembic upgrade head → b2c3d4e5f6a7
document_chunks 向量列: [('embedding','USER-DEFINED','vector')]   ← 仅一列
索引: document_chunks_pkey / ix_document_chunks_document_id / document_chunks_embedding_hnsw
retrieve_by_scope（PG 分支）: seq=0 sim=1.0000，其余 0.0000
```
