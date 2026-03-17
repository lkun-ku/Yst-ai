# 技术选型确认问卷：RAG 向量检索与教资题库

**Purpose:** 《docs/RAG与题库技术选型方案.md》已完成全量可行性验证（见 `01-feasibility-verification.md`）。本问卷把你需拍板的全部可选项列出，逐项确认后用于产出正式规格文档（to-spec）。每项给出默认推荐、备选与影响，便于一次过确认。

**From:** 方案撰写方 ｜ **To:** 技术决策者（产品/技术负责人） ｜ **How your answers will be used:** 直接转化为 `.scratch/rag-bank-tech-selection/issues/03-formal-spec.md` 的正式选型/参数/接口/验收标准。

## Context

项目是教资 AI 闯关刷题小程序（FastAPI + SQLAlchemy 2.0 + 微信原生）。本次为两条线做选型：① RAG 向量检索（支撑"依据我给你的 JavaScript 相关知识出一份系统试题卷"这类自然语言范围出题 + 错题推荐）；② 教资闯关题库结构化与"练—查—学"闭环。可行性已验证：推荐组合 pgvector + 百炼 text-embedding-v3 + 现有栈全链路官方支持，微信云托管 + CloudBase PostgreSQL(pgvector) 部署路径明确。下列选项即待你确认的决策点。

## How to answer

请在每项答案留白处填写「确认默认 / 选备选 / 自定义」。可部分回答、可标「待定」。建议 1 轮内给回，最迟随正式规格前确认。标"待决"的项需你提供环境/预算信息才能关闭。

## 一、向量检索与存储

### 1. 向量库选型

_Why this matters: 决定是否需要新增独立组件与运维成本，影响后续全部检索代码形态。_

默认推荐：**pgvector**（复用现有 Postgres，零新增组件，与 SQLAlchemy/Alembic 契合度最高；CloudBase/TencentDB 均官方支持）。
备选：Qdrant（独立服务、强过滤/原生 hybrid）、Milvus（海量分布式）、Chroma（原型）。
>

### 2. 跨文档检索是否上向量库及触发阈值

_Why this matters: 厘清与 ADR-0007（单文档内存检索不引向量库）的边界，避免过早或遗漏引入。_

默认推荐：**上 pgvector 做跨文档检索**；单用户个人资料库切片数 ≤ 2000（约 10 份资料）时仍可内存检索，超过即走 `cosine_distance` SQL；单文档出题维持 ADR-0007 内存检索不变。
备选：全量内存检索（仅适合极小规模）、一开始就全量 pgvector。
>

### 3. Embedding 模型

_Why this matters: 直接决定中文召回质量与单次 embedding 成本，且现有 `embedding.py` 为 OpenAI 兼容接口，切换仅改配置。_

默认推荐：**阿里百炼 text-embedding-v3**（维度可选 1024/1536/2048，中文强，OpenAI 兼容，与现有 `embedding.py` 直连）。
备选：智谱 embedding-3（256~2048 可调、8K 窗口）、百度千帆 bge-large-zh（1024）。
>

### 4. 切片大小与重叠

_Why this matters: 影响召回粒度与上下文预算；过小噪声多、过大割裂语义。_

默认推荐：**chunk 1200~1500 字、重叠 150~200 字**（结构切分保留 `heading_path` + 语义滑窗）。
备选：800~1000/100、2000/300。
>

### 5. 重排序策略

_Why this matters: 影响"练—查—学"中错题推荐的命中精度与额外算力成本。_

默认推荐：**规则打分**（向量分×权重 + 关键词命中×权重 + 标题命中加成），零额外模型。
备选：接入 Cross-Encoder 精排（bge-reranker，仅错题推荐等敏感场景启用）。
>

## 二、生成链路与框架

### 6. 是否引入 LangChain

_Why this matters: 触及 ADR-0008（不引 LangChain）。方案 A 零新依赖复用现有 `retrieve`/`LLMClient`；方案 B 仅检索层引入。_

默认推荐：**方案 A — 不引 LangChain**，自写轻量 `RetrievalService.retrieve_by_scope(db, candidate_id, scope)`，复用现有 `retrieve()` 混合逻辑与 `LLMClient` 接缝（约 80~150 行）。
备选（仅当触发条件出现再评估）：方案 B — 仅检索层引入 LangGraph/LangChain（`PGVector`+`EnsembleRetriever`+`MultiQueryRetriever`），生成侧保留 `LLMClient`。
触发条件：查询分解/Rewrite-Retrieve-Read 多轮、频繁切换多向量后端、主观题多轮批改链。
>

## 三、部署形态

### 7. 部署与 Postgres/pgvector 来源

_Why this matters: 决定向量数据落点与微信云托管私网连通方式；可行性已证 CloudBase/TencentDB 均支持 pgvector。_

默认推荐：**微信云托管容器（FastAPI）+ CloudBase PostgreSQL（启用 `vector` 扩展）**，向量与业务同库。
备选：微信云托管 + 腾讯云数据库 PostgreSQL（独立）、微信云托管 + 自管 PostgreSQL 容器。
>

### 8. 上线前 pgvector 扩展验证（需决）

_Why this matters: 可行性 R1——需确认目标环境扩展版本 ≥0.5（以用 HNSW）与私网连通。_

默认推荐：上线前在目标 CloudBase/TencentDB 执行 `CREATE EXTENSION IF NOT EXISTS vector;` 并验证版本与只读副本向量索引支持。
待你提供：目标环境实例类型/版本、是否用只读副本。
>

## 四、接口与验收

### 9. 接口约定

_Why this matters: 决定前端（微信原生）对接契约与权限过滤点。_

默认推荐：新增 `POST /api/kb/generate`（scope + spec + difficulty → 跨文档检索后生成）、`GET /api/kb/retrieve`（scope → 召回切片与分数，调试/评测用）；所有检索带 `candidate_id` 权限过滤，个人资料不跨用户；错误码复用现有 4xx 体系。
备选：合并进现有 `/api/docs/*`、不暴露 retrieve 调试接口。
>

### 10. 验收标准（需决量化值）

_Why this matters: 正式规格须可量化验收；下列为推荐基线，需你确认或给业务口径。_

默认推荐（基线）：
- 跨文档检索召回：Top-8 相关切片命中率 ≥ 85%（人工抽检 50 条 scope）；
- 检索延迟：P95 ≤ 300ms（PG `cosine_distance` + HNSW，单用户 KB ≤ 1 万 chunk）；
- 出题结构化校验通过率 ≥ 95%（`validate_question_payload`）；
- 成本：单用户单日 embedding+生成 ≤ 1 元（日级告警 + 自动降级）；
- 兼容性：dev SQLite 内存检索与 PG pgvector 双路均 pytest 绿。
备选：你提供更高/更低阈值。
>

## Anything else?

是否还有方案未覆盖的约束（合规、预算上限、上线时间窗、特定云厂商绑定）需要补充进正式规格？
>
