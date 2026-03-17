# AI 编排：不引入 LangChain / LangGraph

## Status

superseded by ADR-0009（2026-05-26）｜ 反转条件已满足：知识库出题的**质量闭环**（检索相关性评分 → 改写重检索、生成自检 → 重生成）本身即含条件分支与多轮编排，LangGraph 编排路线已代码级走通并成为生产默认

> **本 ADR 原文保留以存档决策理由，当前结论以 [ADR-0009](./0009-知识库出题编排框架引入裁决.md) 为准。**
>
> 精确口径（避免误读）：
> - **仍不引入**：LangChain 全套依赖，及其检索层封装 `PGVector` / `EnsembleRetriever`（ADR-0009 Decision 3）。
> - **已引入**：`langgraph==1.2.11` + `langchain-core==1.6.2`；`POST /api/kb/generate` 的 `route` 默认 `"graph"`，经 `_select_generator` 分发到 `kb_graph.generate_by_scope_graph`。
> - **仍保留**：手写路线② `kb_generate.generate_by_scope` 作为 `route="handwritten"` 的可选回退与评测对照。
> - **不受影响**：单文档出题管线（`doc_generate` / `embedding.retrieve`）依旧不依赖任何编排框架，本 ADR 对其结论仍然有效。

## Context

需求文档 8.1 原定后端「FastAPI + LangChain / LangGraph」，理由是 AI 编排生态成熟。但本项目的出题管线是**线性的**：

```
解析 → 清洗 → 两级切分 → embedding → 检索 → 分批生成 → 题型校验 → 去重 → 落库
```

没有多步 agent、条件分支或工具调用。我们已有干净的 AI 接缝 `LLMClient`（Fake / Real 双实现，测试不耗额度）；核心能力（解析、切分、JSON 修复）自写均不到 100 行。

## Decision

**暂不引入 LangChain / LangGraph**，保留在 `LLMClient` 接缝后，按需再引。

## Considered Options

- **全套引入**（放弃）：大依赖树、版本 churn、抽象层开销；单人项目维护成本高于收益，且当前管线用不上其核心价值（编排 / agent）。
- **只装 langchain-text-splitters**（未采纳）：切分需求我们自己已实现且更可控（标题结构切分 + 1500/200 滑窗，并针对中文断行做了清洗）。
- **不引入**（采用）：保留 `LLMClient` 抽象作为模型调用的唯一切换点。

## Consequences

- 依赖保持精简：AI 相关第三方仅 `pypdf` / `python-docx` / `numpy` 三个。
- 所有模型调用收敛在 `LLMClient.generate()` 一个入口，换供应商只需改配置。
- 提示词工程、JSON 三层保障（json_object → 本地修复 → 结构化校验）由我们自持，出问题可直接调试，不必穿透框架。

## 反转条件

出现需要条件分支 / 多轮工具调用的 AI 流程时（例如主观题批改的多轮追问、模考后的分支建议）重新评估——届时引入属自然演进而非返工。
