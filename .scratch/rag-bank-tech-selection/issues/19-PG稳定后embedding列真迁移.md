# 19: PG 稳定后 embedding 列真迁移

- Status: ready-for-agent
- Type: task
- Blocked by: 16（且生产 PG 稳定运行满一个迭代周期）
- 关联：`backend/app/models.py`、`backend/alembic/versions/a1b2c3d4e5f6_pgvector_embedding.py`、`docs/RAG路线三落地复盘.md` §5.2

## What to build

工单 14 原定「`document_chunks.embedding` **改** `VECTOR(1024)`」，实际实现为**新增** `embedding_vec` 列并保留旧 `embedding`（LargeBinary）。该形态是「同时支持 SQLite dev 与 PG 生产」的唯一低风险解，已通过端到端验证，复盘已决定**反向修订措辞而非返工**。

本票为**长期收敛**：当生产确认只跑 PG、不再需要 SQLite 路径且无回滚需求时，做真迁移。

1. 确认全量 `embedding_vec` 已回填（无 NULL 的 ok 切片）。
2. `ALTER TABLE document_chunks DROP COLUMN embedding`。
3. 模型删除 `embedding`，`embedding_vec` 更名为 `embedding`。
4. `documents._embed_document` 去掉双写，单写向量列。
5. `retrieve_by_scope` 去掉方言分发（不再需要内存分支）。
6. 全量测试 + 灰度验证。

## 验收清单

- [ ] 生产 PG 稳定运行满一个迭代，且确认无回滚需求（前置）。
- [ ] 迁移脚本：drop 旧列 + 重命名 + 去掉双写。
- [ ] 去掉方言分发后全量测试通过。
- [ ] 灰度验证检索结果等价。

## 状态

待办；**优先级 P3，暂不执行**——需等生产 PG 稳定运行后再触发。当前双列并存（约一倍向量存储）是可接受的过渡代价。
