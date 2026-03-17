# 采纳路线③（LangGraph）作为知识库出题默认路径

- Status: ready-for-agent
- Type: task
- 关联：`.scratch/rag-bank-tech-selection/spec.md`、ADR-0009、`docs/RAG与题库技术选型方案.md`

## 背景

三路线代码级对比实验（基线单文档 / 手写质量闭环 / LangGraph 编排）后，用户直接决策：**走路线③（LangGraph StateGraph 编排）** 作为知识库出题的生产默认路径，路线②保留为 `route="handwritten"` 可选项。

## 决策要点

- `POST /api/kb/generate` 的 `route` 字段默认 `"graph"` → 分发到 `kb_graph.generate_by_scope_graph`；`"handwritten"` 走 `kb_generate.generate_by_scope`；非法值返回 400。
- 分发由 `backend/app/routers/kb.py` 的 `_select_generator(route)` 纯函数实现，两生成函数签名已对齐。
- 路线②③ 共享检索/提示词/LLM·Embedding 接缝/校验落库，唯一差异是编排层；框架对出题效果中立（受控实验验证）。

## 已完成实现（本工单对应的落地）

- [x] `kb.py`：`route` 字段 + `_select_generator` 分发 + 非法值 400。
- [x] `test_kb_dispatch.py`：分发单测（默认③ / ②可选 / 非法400），全量 pytest 通过。
- [x] ADR-0009 决策修订为「默认路线③、路线②保留可选」。
- [x] `docs/RAG与题库技术选型方案.md` §1.4/§4.4/§5 结论同步。
- [x] 本 spec（`spec.md`）与工单发布。

## 后续（agent 可承接，非本决策阻塞项）

- 真实 LLM 下跑 `backend/eval/run_eval.py --real` 计量质量增益（需 `EMBEDDING_MODE/LLM_MODE=real` + API key）。
- 正式落地时为知识库出题新增独立任务表 + Alembic 迁移（当前用模块级内存进度表）。
- 生产向量库（pgvector HNSW）迁移评估。
