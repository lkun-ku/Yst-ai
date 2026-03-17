# 正式规格：RAG 向量检索与教资题库

Status: ready-for-agent
Type: spec
Labels: ready-for-agent

> 上游：本规格综合 `01-feasibility-verification.md`（全量可行性验证）与 `02-selection-questionnaire.md`（人工逐项确认，全部采用默认推荐）。领域术语复用 `CONTEXT.md`（考点 / 题目 / 切片 / 个人题库 / 题库 / 闯关局 / 复盘报告 / 掌握度 / 错题本 / 每日任务 / 连胜 / 模考 / 批改），并遵守 ADR-0007 / 0008 / 0002 / 0006 / 0003。

## Problem Statement

作为教资考生，我希望在上传自己的学习资料后，能用自然语言范围（如"依据我给你的 JavaScript 相关知识出一份系统试题卷"）让系统跨我的个人资料库做向量检索并生成成体系题目；同时在我答错后，系统能基于错题考点推荐针对性复习资料，形成"练—查—学"闭环。现有 AI 出题为单文档内存检索，无法跨个人资料库检索，也未对向量库选型、Embedding、切分、检索优化、部署与验收标准做正式约定。

## Solution

采用 **pgvector（复用现有 Postgres，零新增组件）+ 阿里百炼 text-embedding-v3（OpenAI 兼容，与现有 `embedding.py` 直连）+ 现有 FastAPI/SQLAlchemy 栈** 的推荐组合。单文档出题维持 ADR-0007 的内存混合检索不变；新增"跨文档自然语言范围出题"与"错题推荐"走 pgvector 跨文档 `cosine_distance` 检索。不引入 LangChain（方案 A，维持 ADR-0008），自写轻量检索服务复用现有 `retrieve()` 与 `LLMClient` 接缝。部署为微信云托管容器 + CloudBase PostgreSQL（启用 `vector` 扩展）。

## User Stories

1. 作为考生，我想上传多份资料（PDF/DOCX/TXT/MD），系统解析切分并生成 embedding 存于我的个人资料库，以便后续跨资料检索。
2. 作为考生，我想输入"依据 JavaScript 相关知识出一份系统试题卷"，系统在我的资料库内检索相关切片并生成试卷，以便按需组卷。
3. 作为考生，我想在出题时指定题型配比（单选/多选/判断/填空/简答/材料分析）与难度，系统按范围生成对应题目。
4. 作为考生，我想看到生成的题目带 `source_chunk` 溯源，以便回溯到资料原文片段。
5. 作为考生，我想在闯关答错后，系统按错题考点从我资料库召回相关切片作为复习资料，以便针对性补课。
6. 作为考生，我想错题推荐不跨用户、不泄露他人资料，以便隐私受保护。
7. 作为考生，我想每日任务/每日挑战真的完成题目才标记完成，以便留存有效。
8. 作为考生，我想弱网/中断后可续答，进度不丢，以便体验连贯。
9. 作为系统，我想在 embedding 服务不可用时自动降级（向量→关键词→均匀采样），不得阻塞上传与出题。
10. 作为系统，我想检索带 `candidate_id` 过滤，个人资料严格隔离。
11. 作为运维，我想向量与业务同库（pgvector），不引入独立向量服务，降低运维成本。
12. 作为运维，我想 dev 用 SQLite 内存检索、生产用 PG pgvector，由 `DATABASE_URL` 切换且双路均测试通过。
13. 作为决策者，我想控制 AI 成本（免费无 VIP），单用户单日 embedding+生成 ≤ 1 元，并有日级告警与自动降级。
14. 作为决策者，我想遵守版权纪律：不存原题原文、不留存文档原文（仅存切片）、资料抽样过内容安全检测、个人题不进官方池。
15. 作为评审，我想验收标准可量化：跨文档召回率、检索延迟、出题校验通过率、成本阈值均可测。
16. 作为开发者，我想切换 Embedding 供应商仅改配置（百炼/智谱/千帆均为 OpenAI 兼容），不动检索代码。
17. 作为开发者，我想新增跨文档检索对外接口稳定，便于前端（微信原生）对接。
18. 作为未来需求，若需查询分解/Rewrite-Retrieve-Read/多轮批改，再评估仅检索层引入 LangChain，且生成侧保留 `LLMClient` 接缝。

## Implementation Decisions

**技术选型（已确认）**
- 向量库：pgvector（复用 Postgres，`vector` 扩展 + HNSW 索引）。Qdrant/Milvus/Chroma 仅作备选，一期不引入。
- 跨文档检索：上 pgvector；单用户个人资料库切片数 ≤ 2000（约 10 份资料）仍可内存检索，超出走 `cosine_distance` SQL；单文档出题维持 ADR-0007 内存检索。
- Embedding：阿里百炼 text-embedding-v3，默认维度 1024（可选 1536/2048）；OpenAI 兼容 `/embeddings`，复用现有 `embedding.py`，切换仅改 `EMBEDDING_MODEL`/`EMBEDDING_API_BASE`/`EMBEDDING_API_KEY`。
- 切分：chunk 1200~1500 字、重叠 150~200 字；结构切分保留 `heading_path` + 语义滑窗；教材/真题解析/考纲条目三类差异化（见现有 `doc_parser.py` 两级切分）。
- 重排：默认规则打分（向量分×权重 + 关键词命中×权重 + 标题命中加成）；Cross-Encoder 精排仅错题推荐等敏感场景按需启用。
- 框架：方案 A 不引 LangChain，自写 `RetrievalService.retrieve_by_scope(db, candidate_id, scope)` 复用现有 `retrieve()` 混合逻辑与 `LLMClient`。

**参数配置（基线）**
| 参数 | 值 | 说明 |
| --- | --- | --- |
| `DOC_CHUNK_SIZE` | 1200~1500 字 | 语义滑窗 |
| `DOC_CHUNK_OVERLAP` | 150~200 字 | 重叠 |
| `EMBEDDING_DIM` | 1024 | 与 pgvector `VECTOR(1024)` 对齐 |
| `DOC_TOP_K` | 8 | 召回切片数 |
| `pgvector` HNSW | m=16, ef_construction=64, `vector_cosine_ops` | 生产 PG 索引 |
| 跨文档内存阈值 | ≤ 2000 chunk/用户 | 超出走 SQL |
| 成本阈值 | ≤ 1 元/用户/日 | embedding+生成，日级告警+自动降级 |

**Schema 变更（概念级，避免过期路径）**
- `document_chunks.embedding`：生产 PG 由 `LargeBinary` 改为 `VECTOR(1024)`；dev SQLite 保留内存检索，不依赖该列。
- 新增 HNSW 索引（`postgresql_using='hnsw'`、`vector_cosine_ops`），经 Alembic 迁移创建。
- 跨文档检索经 `documents.candidate_id` 过滤；可选冗余 `owner_candidate_id` 于 `document_chunks` 加速过滤（保留现有双库分离与权限纪律）。
- 题目结构化字段复用现有 `Question`（type/stem/options/answer/explanation/knowledge_point/module/difficulty/source/source_year/doc_id/owner_candidate_id），材料分析题（material）纳入题型枚举。

**API 契约**
- `POST /api/kb/generate`：请求体 `{ scope: str, spec: [{type,count}], difficulty, focus? }`；服务端在用户个人资料库做跨文档检索 → 分批生成 → 落个人题库；响应 `{ task_id, progress }`（沿用异步轮询）。
- `GET /api/kb/retrieve`：请求体/参数 `{ scope, k? }`；返回召回切片与分数，供调试/评测；所有检索带 `candidate_id` 过滤。
- 权限：跨文档检索强制 `candidate_id` 过滤，个人资料不跨用户；错误码复用现有 4xx 体系；不暴露他人数据。
- 版权：返回切片不留存文档原文、不返回原题原文；解析仅存切片。

**部署**
- 微信云托管运行 Docker 容器（FastAPI 服务），连同 VPC 内 CloudBase PostgreSQL（启用 `vector` 扩展），向量与业务同库。
- 上线前在目标环境执行 `CREATE EXTENSION IF NOT EXISTS vector;` 验证扩展版本 ≥ 0.5（以用 HNSW）与私网连通（需决，待目标环境信息复核）。

**测试接缝（复用现有，少新增）**
- 复用现有 `embedding.retrieve` 单文档混合检索测试、`doc_generate` 双模式测试、`LLMClient` Fake/Real 接缝。
- 新增最高接缝：`RetrievalService.retrieve_by_scope`（跨文档），在 `EMBEDDING_MODE=fake` 下离线可测；`/api/kb/generate` 端到端用 Fake 链路跑通。

## Testing Decisions

- 只测外部行为，不测实现细节：`retrieve_by_scope` 输入 scope+candidate_id 输出相关切片 Top-K；`/api/kb/generate` 输入 scope+spec 输出落库题目且可溯源。
- 覆盖模块：`embedding`（跨文档检索 + 三级降级）、`RetrievalService`、`doc_generate`（scope 模式）、`/api/kb/*` 路由、Alembic 迁移（LargeBinary→VECTOR + HNSW）。
- 先验参考：现有 `tests/test_embedding.py`、`tests/test_daily.py`、`tests/test_review.py` 的 Fake 链路与断言风格。
- 验收测试（量化，对应 #10）：
  - 跨文档召回：50 条 scope 人工抽检，Top-8 相关切片命中率 ≥ 85%；
  - 检索延迟：单用户 KB ≤ 1 万 chunk，P95 ≤ 300ms（PG `cosine_distance` + HNSW）；
  - 出题结构化校验通过率 ≥ 95%（`validate_question_payload`）；
  - 成本：单用户单日 embedding+生成 ≤ 1 元（日级告警 + 自动降级生效）；
  - 兼容性：dev SQLite 内存检索与 PG pgvector 双路均 pytest 绿。

## Out of Scope

- 不引入 LangChain/LangGraph（维持 ADR-0008；仅触发条件出现时再评估）。
- 不引入 Chroma/Milvus/Qdrant 作为生产主存储（一期）。
- 不做 OCR、不做多文档出卷（ADR 约束）。
- 主观题 AI 批改（材料分析/写作）多轮链路上线后置。
- 原题原文入库、文档原文留存（版权纪律禁止）。
- 官方题库与个人题库合并（双库分离维持）。

## Further Notes

- 本规格与 ADR-0007（单文档内存/跨文档 pgvector 分工）、ADR-0008（默认不引 LangChain）、ADR-0002/0006（双库分离与版权）、ADR-0003（不存原题/文档原文）一致。
- 演进：跨文档检索后端可由内存平滑切 Qdrant/Milvus（仅替换 `retrieve_by_scope` 实现），对外接口不变。
- 架构示意：

```mermaid
flowchart LR
  U[考生: 自然语言范围出题] -->|scope+配比| API[/api/kb/generate]
  API --> RS[RetrievalService.retrieve_by_scope<br/>复用 retrieve 混合检索]
  RS -->|candidate_id 过滤| PG[(document_chunks<br/>pgvector VECTOR1024)]
  RS -->|召回切片 RRF 融合| GEN[doc_generate scope 模式]
  GEN --> LLM[LLMClient Fake/Real]
  GEN --> VAL[validate_question_payload]
  VAL --> QB[(个人题库)]
  QB --> SESS[闯关局/每日任务]
  SESS --> WR[复盘/错题本 按考点聚合]
  WR -->|错题知识点召回| RS
  RS -.->|查-学推荐| U
```

- 需决项：#8 目标环境 pgvector 扩展版本与私网连通、#10 业务侧成本/阈值口径，待上线前在目标环境复核后关闭。
