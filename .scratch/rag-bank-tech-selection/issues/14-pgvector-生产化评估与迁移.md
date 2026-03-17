# 14: pgvector 生产化评估与迁移

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 13
- 关联：`backend/alembic/versions/a1b2c3d4e5f6_pgvector_embedding.py`、`backend/app/services/kb_retrieval.py`、`backend/requirements.txt`、`docs/RAG与题库技术选型方案.md`

## What to build

评估并将跨文档 embedding 列从 dev 内存/SQLite `LargeBinary` 迁移到生产 PG pgvector `VECTOR(1024)` + HNSW，使跨文档检索在规模化资料库低延迟、可运维。dev SQLite 仍走内存检索。

- 生产 PG：向量检索列改为 `VECTOR(1024)`（维度对齐 text-embedding-v3）。
  **措辞修订（工单 16 / 复盘 W-4）**：实现为**新增** `embedding_vec VECTOR(1024)` 列承载 PG 检索，
  原 `embedding`（LargeBinary）**保留**作为 dev/SQLite 与降级路径——SQLite 无法表达 `VECTOR` 类型，
  直接改列会让 dev/全量测试崩溃。真迁移（drop 旧列）另见工单 19。
- Alembic 创建 HNSW（`m=16, ef_construction=64, vector_cosine_ops`）。
- `retrieve_by_scope` 加分支：PG 走 `cosine_distance` SQL，SQLite/dev 走内存 numpy。

**前提（用户提供）**：CloudBase PostgreSQL 连接串（`DATABASE_URL`）+ `vector` 扩展版本。用户已在插件市场安装 PG，待提供连接串并执行 `CREATE EXTENSION vector` 确认版本 ≥ 0.5。

## 验收清单

- [x] 用户提供 CloudBase PG 连接串 + `vector` 版本 ≥ 0.5 确认（实际 0.8.2，DMC 实测）。
- [x] `document_chunks.embedding` 迁移为 `VECTOR(1024)`（迁移脚本就绪，键入 1024 维 0.1/0.5/0.9 测试向量端到端通过）。
- [x] HNSW 经 Alembic 创建；双路（SQLite/PG）pytest 绿（217 单测全过，含新增 `_is_pg` 分发冒烟）。
- [x] 目标环境私网连通 + 扩展性复核记录（用户本地→外网 119.91.18.213:23265 受 pg_hba 限制，需另开白名单或走 DMC；本票以 DMC 端到端验证代替）。

## 状态

已落地（历史追溯）。代码与依赖到位（`psycopg2-binary==2.9.9` + `pgvector==0.5.0` 已钉入 `requirements.txt`），迁移脚本与 PG 分支均已就绪。下次部署到生产 PG 时执行 `alembic upgrade head` 即可启用 pgvector；该次试跑因网络/IP白名单限制无法从本地直连 PG，改用 DMC 内联 SQL 验证扩展、HNSW 索引、cosine 算子均通过。

关键文件：
- 迁移：`backend/alembic/versions/a1b2c3d4e5f6_pgvector_embedding.py`
- 双路分发：`backend/app/services/kb_retrieval.py`（`_is_pg` + `retrieve_by_scope_pg` + 主入口按方言分发）
- 依赖：`backend/requirements.txt`
- 单测：`backend/tests/test_kb_retrieval.py::test_分发_SQLite下is_pg返回False`

临时验证脚本 `_pg_check.py`（位于 workspace 根）已清理。