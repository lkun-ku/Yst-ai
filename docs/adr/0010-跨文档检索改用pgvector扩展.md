# 跨文档检索：改用 pgvector 扩展（撤销 ADR-0007）

## Status

accepted（2026-05-26）｜ 撤销 ADR-0007（保留 RAG 但不引向量数据库）

> _Contradicts ADR-0007 (保留 RAG 但不引向量数据库)，but worth reopening because 其反转条件「需要跨资料联合检索」已由 `kb_retrieval.retrieve_by_scope` 跨文档检索实现并落地；且 pgvector 为 PostgreSQL 扩展而非独立向量数据库服务，不引入额外部署与一致性成本。_
>
> （以上声明格式遵循 `docs/agents/domain.md` §「Flag ADR conflicts」：与既有 ADR 冲突时显式声明，而非静默覆盖。）

## Context

ADR-0007 在「检索范围是**单个文档**」的前提下裁决「不引向量数据库」：embedding 存 `document_chunks.embedding`（BLOB），检索时进程内做余弦。它同时预留了反转条件：

> 当单用户资料总量达到数千切片、或需要跨资料联合检索时，重新评估引入向量库。

此后需求升级为「自然语言 scope → 跨个人知识库（多文档）出题」，`kb_retrieval.retrieve_by_scope` 落地了跨文档混合检索（向量 + 关键词二元组 + RRF 融合 + 标题路径加成）。**ADR-0007 的反转条件已被触发**：检索范围从「单文档约 200 chunk」变为「该考生全部资料切片，跨文档累计可达数千～数万」。

### 关键辨析：ADR-0007 禁止的到底是什么

| 维度 | Chroma（ADR-0007 禁止） | pgvector（本 ADR 采用） |
| --- | --- | --- |
| 部署形态 | **独立服务**，额外持久化目录与运维 | PostgreSQL **扩展**，随库启停，无独立部署 |
| 数据一致性 | 与业务库双写，需自行保证同步 | 与 `document_chunks` **同表同事务** |
| 权限模型 | 需重建 `candidate_id` 隔离 | 沿用现有 `candidate_id` 过滤 |
| 适用规模 | 百万切片 | 单用户数千～数十万切片 |

→ ADR-0007 所禁止的是**独立部署的向量数据库服务**，该判断**依然成立且未被推翻**。pgvector 不是这一类东西，但它确实改变了存储与检索路径，**属于必须显式声明的架构变更**。

### 与 ADR-0009 的关系（不冲突）

ADR-0009 Decision 3 明确「需要检索层能力时复用已落地的 `kb_retrieval` 混合检索 + RRF，**而非引入 LangChain 的 `PGVector`/`EnsembleRetriever`**」。本 ADR：

- **未引入** LangChain 的 `PGVector` 封装（检索逻辑仍写在自研 `kb_retrieval` 内，按方言分发）
- **未引入** Chroma 等独立向量数据库服务

→ **与 ADR-0009 不冲突**，反而是其「复用 kb_retrieval」要求的落实。

## Decision

**在 PostgreSQL 生产库上启用 pgvector 扩展承载跨文档向量检索；dev/test 的 SQLite 仍走内存 numpy 余弦路径。**

1. 新增列 `document_chunks.embedding_vec VECTOR(1024)`（**不替换**原 `embedding` BLOB 列），并建 HNSW 索引（`m=16, ef_construction=64, vector_cosine_ops`）。
2. `retrieve_by_scope` 按方言自动分发：PostgreSQL → `retrieve_by_scope_pg`（SQL 余弦 + HNSW）；SQLite/dev → 原内存混合检索。两条路径返回结构一致，上层无感。
3. 写入侧同步：PG 上写切片向量时同时写 `embedding` 与 `embedding_vec`（见 `documents.py._embed_document`），避免 `embedding_vec` 恒 NULL 导致检索静默返空。
4. **不引入** LangChain 的 `PGVector` / `EnsembleRetriever` 检索封装（沿用 ADR-0009 Decision 3）。
5. **不引入** Chroma 等独立向量数据库服务——ADR-0007 对它们的判断依然成立。

## Considered Options

- **维持 ADR-0007，全量内存余弦**（放弃）：跨文档后切片量随资料数线性增长；单次数千～数万向量的暴力余弦虽仍可用，但每次出题都要**全量加载并计算**，延迟与内存随规模恶化，且无法利用索引剪枝。
- **引入 Chroma**（放弃）：即 ADR-0007 已否决项，独立部署 + 双写同步 + 权限重建的成本高于收益。
- **pgvector 扩展 + HNSW**（**采用**）：零额外部署，复用现有 PG 与 `candidate_id` 权限模型，索引在库内，dev/SQLite 路径完全不受影响。
- **替换 `embedding` 列类型为 `VECTOR`**（放弃）：SQLite 无法表达 `VECTOR` 类型，会让 dev/测试全量崩溃；且大表改类型回滚代价高。

## Consequences

- 迁移脚本 `a1b2c3d4e5f6_pgvector_embedding.py`；因历史分叉另加合并 revision `m1a2b3c4d5e6`，保证单一 head（`alembic upgrade head` 可执行）。
- 依赖新增 `psycopg2-binary==2.9.9` + `pgvector==0.5.0`；**仅 PG 生产路径需要**，SQLite dev 不加载。
- 双列并存带来约一倍的向量存储（1024 维 ≈ 4KB/切片），以空间换取 dev/PG 双路兼容与可回滚。待生产 PG 稳定一迭代后可做真迁移（drop 原 `embedding`、`embedding_vec` 更名），见 `docs/RAG路线三落地复盘.md` W-5。
- PG 分支只走向量通道（不参与关键词 RRF 与标题加成），与内存路径召回存在细微差异；生产切片均带 embedding，可接受，若需完全对齐应另开工单。
- 已验证：腾讯云 TencentDB PostgreSQL 18.x + pgvector **0.8.2** 端到端通过（建列 / HNSW 索引 / `<=>` cosine 算子）。

## 关联

- 撤销：[ADR-0007](./0007-保留RAG但不引向量数据库.md)（已标 superseded）
- 相关但不冲突：[ADR-0009](./0009-知识库出题编排框架引入裁决.md)
- 方案与复盘：`docs/RAG与题库技术选型方案.md`（§2.3、§0.3）、`docs/RAG路线三落地复盘.md`
