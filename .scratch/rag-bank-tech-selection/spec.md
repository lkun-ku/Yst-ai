# 知识库出题：采纳路线③（LangGraph 编排）作为默认路径

> 来源：三路线代码级对比实验后用户的直接决策。配套 ADR：`docs/adr/0009-知识库出题编排框架引入裁决.md`；技术细节见 `docs/RAG与题库技术选型方案.md`。
> 领域词（CONTEXT.md）：知识库 / 切片 / 个人题库 / 出题任务 / 质量闭环。

## Problem Statement

用户需要「依据自然语言范围（scope）在其**个人知识库**（跨多份上传资料）中做向量检索并生成成体系题目」。现有单文档出题管线（基线）结构上无法满足跨文档合成，必须新增跨文档出题路径。我们在「引入 LangChain/LangGraph」与「自研」之间做了代码级对比，用户最终决策：**走路线③（LangGraph StateGraph 编排）** 作为知识库出题的生产默认路径。

## Solution

将知识库出题 HTTP 入口 `POST /api/kb/generate` 的 `route` 字段默认置为 `"graph"`，经 `_select_generator` 分发到路线③ `kb_graph.generate_by_scope_graph`（LangGraph 编排）；`route="handwritten"` 仍走路线② `kb_generate.generate_by_scope`（手写质量闭环），作为依赖受限时的回退与评测对比基准。

三条路线（基线 / 手写 / LangGraph）共享同一套检索（`kb_retrieval`）、提示词（`prompts_kb`）、LLM·Embedding 接缝（`LLMClient`/`embedding`）、校验落库逻辑（`validate_question_payload`）——唯一差异是编排层（手写控制流 vs LangGraph `StateGraph`），从而保证实验只测量「框架 vs 手写编排」本身。

## User Stories

1. As a 考生（个人知识库持有者），I want 用一段自然语言范围跨我的全部上传资料出一份系统试题卷，so that 不必逐文档分别出题也能得到覆盖整库的成体系题目。
2. As a 考生，I want 出题任务在 LLM 不可用时自动降级为单轮生成，so that 检索/评分异常不会让我完全拿不到题目。
3. As a 考生，I want 我的个人知识库严格隔离（按 candidate_id），so that 我的资料与题目不会越界到他人或官方题库。
4. As a 后端开发者，I want 跨文档出题与既有单文档出题管线隔离，so that 新路径的改动不会破坏已实战检验的 doc_generate 逻辑。
5. As a 后端开发者，I want 切换编排路线只需改一个 route 字段，so that 未来在「手写」与「LangGraph」之间回退/对比零成本。
6. As a 后端开发者，I want 质量闭环（检索相关性评分→改写重检索；生成自检→重生成）带重试上限，so that LLM 成本可控、不因反复重试爆量。
7. As a 评审者，I want 出题质量可被量化评测（LLM-as-judge 五维），so that 任何路线/参数变更都有数据可对照。
8. As a 评审者，I want 选型结论有代码与实验支撑而非空谈，so that 引入 LangGraph 与否是可追溯的工程决策。
9. As a 考生，I want 出题纳入每日次数配额，so that 成本硬约束不被绕过。
10. As a 后端开发者，I want 评测 harness 支持 fake 离线跑通与 real 真实跑通，so that 没有 API key 也能验证管线、有 key 时能量化真实质量增益。

## Implementation Decisions

- **默认路径**：`POST /api/kb/generate` 的 `route` 字段默认 `"graph"`，分发到 `kb_graph.generate_by_scope_graph`（路线③）；`"handwritten"` 走 `kb_generate.generate_by_scope`（路线②）。非法 `route` 返回 400。
- **分发实现**：`backend/app/routers/kb.py` 新增 `_select_generator(route)` 纯函数，按 route 返回对应生成函数；两函数签名对齐（`db, candidate_id, scope, spec, difficulty, focus, enable_loop, on_progress=`），调用点不变。
- **编排层（路线③）**：`kb_graph.py` 用 LangGraph `StateGraph` 表达质量闭环 `retrieve → grade →（不达标则 rewrite→retrieve）→ assemble → generate`；节点直接调用 `LLMClient`（无需包成 `BaseChatModel`），LLM 返回 None 时各子步骤自行降级、图整体不阻塞出题。
- **共享接缝（零改动）**：`kb_retrieval`（跨文档混合检索 + RRF + 相关性评分）、`prompts_kb`（范围改写/评分/出题/自检四块提示词）、`LLMClient`/`embedding` 接缝、`validate_question_payload` 校验落库、quota 日级告警+自动降级——路线②③ 完全共用。
- **依赖**：已钉入 `langgraph==1.2.11` + `langchain-core==1.6.2`，经 Context7 核实与 pydantic 2.9.2 / fastapi 0.115 / Python 3.11 兼容。
- **不改动**：现有单文档出题管线（`doc_generate` / `embedding.retrieve`）、`document_chunks` 模型、`DocTask` 表结构（知识库出题任务用模块级内存进度表，待正式落地再建独立任务表 + Alembic）。
- **实验结论支撑**：受控实验（fake 离线）中路线②③ 五维质量均 4.0、LLM 调用数均 72、提示词字符均 38354（逐项相等，证明框架对效果中立）；基线因批处理摊薄为每题 0.5 调用。真实 LLM 下质量闭环捕捉的错误数即 ②③ 相对基线的质量增益，由 `run_eval.py --real` 计量。

## Testing Decisions

- 仅测外部行为，不测编排内部实现细节：分发单测（`test_kb_dispatch.py`）断言 `_select_generator("graph")` 取 LangGraph 函数、`("handwritten")` 取手写函数、非法 route 经端点返回 400；不依赖真实 embedding/LLM/DB（mock 模块层生成函数）。
- 既有路线单测（`test_kb_generate.py` / `test_kb_graph.py` / `test_kb_retrieval.py`）保留，确保两路线行为不被破坏。
- 评测 harness（`test_run_eval.py` + `backend/eval/run_eval.py`）断言「闭环路线调用数 > 基线」「路线②③ 调用数相等」——持续验证「框架中立」结论不被回归。
- 现有可比先验：单文档出题、embedding 检索均有等价 Fake 离线测试，新测试沿用其「FakeLLMClient 确定性、EMBEDDING_MODE=fake 离线」范式。

## Out of Scope

- 不引入 LangChain 检索层（`PGVector`/`EnsembleRetriever`/`MultiQueryRetriever`）——与现有 `document_chunks` + `candidate_id` 权限模型适配成本高、版本耦合强，且检索增强收益已被自研混合检索 + RRF 覆盖。
- 不重写单文档出题管线；不改动 `DocTask` 表结构（知识库出题任务表留待正式落地）。
- 本 spec 不解决真实 LLM 下的质量增益计量（那是 `run_eval.py --real` 的职责，harness 已就绪，待 API key 跑）。
- 生产向量库（pgvector HNSW）迁移仅文档化，本期不实现。

## Further Notes

- 该决策是对 ADR-0008「不引入 LangChain/LangGraph」的精细化修订：默认仍不引框架检索层，但 LangGraph 编排路径已走通并作为默认。
- 选型全过程、四环节参数、数据流图、效果验证方式见 `docs/RAG与题库技术选型方案.md`；裁决记录见 `docs/adr/0009`。
- 风险提示：路线③ 增加 `langgraph`/`langchain-core` 依赖；质量闭环使 LLM 调用约 1.5~2 倍（fake 实测每题 6 倍，因基线批处理摊薄），须在真实实验中量化每题成本 vs 质量增益。
