# 可行性验证：RAG 向量检索与教资题库选型

Status: resolved
Type: research

> 验证目标：核实《docs/RAG与题库技术选型方案.md》技术路线是否可落地。覆盖 4 向量库 + 3 Embedding 模型 + pgvector×SQLAlchemy 兼容 + Docker/微信云托管部署。来源含 Context7 官方文档（2026-09）、腾讯云/CloudBase 官方文档、各模型厂商文档。

## 摘要

推荐组合 **pgvector + 阿里百炼 text-embedding-v3 + 现有 FastAPI/SQLAlchemy 栈** 全链路具备官方支持、版本兼容与明确部署路径，**判定可行**。原担忧的"微信云托管能否获取带 pgvector 的 Postgres"已被证实消解：CloudBase PostgreSQL 与腾讯云数据库 PostgreSQL 均提供官方 pgvector 支持。Qdrant/Milvus/Chroma 作为备选，一期不推荐（运维成本高于 pgvector 的零新增组件）。

## 1. 向量数据库

| 库 | 最新稳定/版本 | Python 3.11 / SQLAlchemy 2.0 | 维护状态 | 索引与部署形态 | 兼容性结论 |
| --- | --- | --- | --- | --- | --- |
| **pgvector** | PG 扩展 0.8.x（`vector`）；Python 包 `pgvector` 0.3.x | ✅ SQLAlchemy 2.0 经 `pgvector.sqlalchemy`（`VECTOR`、`cosine_distance`、HNSW） | ✅ 活跃（Andrew Kane） | PG 扩展；HNSW/IVFFlat；复用现有 PG | **推荐，零新增组件** |
| **Qdrant** | `qdrant-client` 近期稳定；服务端独立 | ✅ Python 3.11；独立服务，不直接依赖 SQLAlchemy | ✅ 活跃（Rust） | 独立服务/集群/Cloud；强 payload 过滤；原生 hybrid（prefetch + `FusionQuery(RRF)`） | 备选（需独立组件） |
| **Milvus** | PyMilvus 映射服务端 1.x–3.x（参考 v2.5.21） | ✅ Python ≥3.9；SDK 与 server 版本需对齐 | ✅ 活跃（CNCF） | 重：standalone（Lite/compose）或分布式（etcd+MinIO+消息队列） | 海量场景备选，一期过重 |
| **Chroma** | `chromadb` 近期稳定 | ✅ Python 3.11；内嵌/单机 | ✅ 活跃（有融资） | `PersistentClient(path)` 本地持久；生产走 Chroma Cloud（serverless）或分布式 | 原型备选，生产 HA 弱 |

官方写法核实（Context7）：
- pgvector：`from pgvector.sqlalchemy import VECTOR`；`VECTOR(1024)`；`order_by(Document.embedding.cosine_distance(vec)).limit(k)`；HNSW 索引 `Index('embedding_hnsw', Document.embedding, postgresql_using='hnsw', postgresql_with={'m':16,'ef_construction':64}, postgresql_ops={'embedding':'vector_cosine_ops'})`。
- Qdrant：`QdrantClient(":memory:"/path)`；`query_points(query=vec, query_filter=Filter(...), limit=...)`；hybrid 用 `prefetch=[Prefetch(...),...]` + `FusionQuery(fusion=Fusion.RRF)`。
- Milvus：`MilvusClient(uri="http://localhost:19530")`；`pymilvus` 需与 server 版本对齐；分布式需 etcd/MinIO。
- Chroma：`chromadb.PersistentClient(path=...)`；生产建议 Chroma Cloud 或分布式架构。

## 2. Embedding 模型

| 模型 | 维度 | 中文效果 | 定价口径（以官网实时为准） | API | 结论 |
| --- | --- | --- | --- | --- | --- |
| **阿里百炼 text-embedding-v3** | 可变 1024/1536/2048 | ⭐⭐⭐⭐⭐ | 按 token；官方原价见 help.aliyun.com/zh/model-studio/text-embedding-v3（2026-05-05 更新）；社区参考约 0.0007 元/千 tokens 量级 | OpenAI 兼容 `/embeddings` | **推荐**（与现有 `embedding.py` 直连） |
| 智谱 embedding-3 | 可调 256/512/1024/2048（8K 窗口） | ⭐⭐⭐⭐⭐ | 按 token；见 docs.bigmodel.cn/cn/guide/start/pricing | OpenAI 兼容 | 备选 |
| 百度千帆 bge-large-zh | 1024 | ⭐⭐⭐⭐ | 每日免费 100 次、按量后付费（cloud.baidu.com/doc/qianfan-docs，2026-03-05） | OpenAI 兼容 | 备选 |

- 三模型均为 OpenAI 兼容 `/embeddings` 接口，现有 `embedding.py._real_embed`（urllib 调 `/embeddings`）**无需改造即可切换供应商**，仅改 `EMBEDDING_MODEL`/`EMBEDDING_API_BASE` 配置。
- 项目既有成本估算（已评审）：一份 200 页资料 embedding ≈ 0.1~0.4 元/份（一次性、可反复复用），相对生成成本可忽略。
- 精确定价需上线前在官网复核并设日级告警（成本硬约束）。

## 3. pgvector × SQLAlchemy 兼容性

- **PG 扩展版本**：HNSW 需 pgvector 扩展 ≥ 0.5.0（2023 起），当前云上 0.8.x 满足；IVFFlat 更早即支持。
- **Python 包**：`pgvector`（PyPI）含 `pgvector.sqlalchemy` 子模块，全面支持 SQLAlchemy 2.0 的 `VECTOR` 类型、`l2_distance`/`cosine_distance`/`ip_distance` 及 HNSW/IVFFlat 索引定义（Context7 /pgvector/pgvector-python，benchmark 89）。
- **Alembic 迁移**：可将 `document_chunks.embedding` 从 `LargeBinary` 改为 `VECTOR(1024)`；HNSW 索引用 `op.create_index(..., postgresql_using='hnsw', postgresql_ops={'embedding':'vector_cosine_ops'})` 创建；新增切片经后台 embedding 任务自动纳入，无需全量重建。
- **双路分支**：dev SQLite 走内存 `retrieve()`（现有）；生产 PG 走 `cosine_distance` SQL，由 `DATABASE_URL` 切换，对外 `retrieve_by_scope` 接口不变。

## 4. 部署环境（Docker / 微信云托管）

- **微信云托管**：运行 Docker 容器（FastAPI 服务），自动建 VPC 并绑定当前云开发环境，可连同 VPC 内的云数据库。
- **Postgres + pgvector 获取（关键风险点已消解）**：
  - CloudBase PostgreSQL **官方支持 pgvector**（docs.cloudbase.net/database/postgresql/pgvector，2026-05-04）——"适合在数据库中保存 embedding，并按相似度查询"。
  - 腾讯云数据库 PostgreSQL 提供官方 pgvector 使用指南（cloud.tencent.com/document/product/409/132364，2026-06-01）。
  - 推荐部署形态：**微信云托管容器（FastAPI）+ CloudBase/TencentDB PostgreSQL（启用 `vector` 扩展）**，向量与业务同库，零新增向量组件。
- **SQLite 与 PG 双路影响**：检索实现按 `DATABASE_URL` 分支；单机 dev 仍内存检索，不依赖 PG/pgvector。

## 5. 可行性结论（可行 / 风险 / 需决）

**可行（已证实）**
- 推荐组合全链路官方支持：pgvector（CloudBase/TencentDB 均提供）、百炼 text-embedding-v3（OpenAI 兼容，与 `embedding.py` 直连）、SQLAlchemy 2.0 集成写法明确。
- 部署路径明确：微信云托管 + CloudBase PostgreSQL(pgvector)。

**风险 / 需决**
- **R1（需决）**：上线前在目标 CloudBase/TencentDB 环境执行 `CREATE EXTENSION IF NOT EXISTS vector;` 验证扩展版本（≥0.5 以用 HNSW），并确认私网连通与只读副本是否支持向量索引。
- **R2（阈值）**：跨文档检索上向量库的触发规模——单用户个人资料库较小时可暂缓 `VECTOR` 迁移，但建议在首次跨文档需求前完成 `LargeBinary→VECTOR(1024)` 迁移与 HNSW 索引。
- **R3（成本）**：Embedding 精确定价以官网实时为准，上线前复核并设日级告警/自动降级（免费无 VIP 硬约束）。
- **R4（不推荐）**：Chroma/Milvus 仅作备选；其生产运维成本高于 pgvector 零新增组件，一期不引入。

## Comments
