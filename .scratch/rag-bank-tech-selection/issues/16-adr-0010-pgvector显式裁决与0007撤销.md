# 16: ADR-0010 pgvector 显式裁决与 ADR-0007 撤销

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 14
- 关联：`docs/adr/0010-跨文档检索改用pgvector扩展.md`、`docs/adr/0007-保留RAG但不引向量数据库.md`、`docs/agents/domain.md`、`docs/RAG路线三落地复盘.md`

## What to build

pgvector 落地与 ADR-0007「不引向量数据库」冲突，但仅在选型文档单方面写「互补」，ADR-0007 仍为 `accepted`，违反 `domain.md` §「Flag ADR conflicts」（冲突须显式声明而非静默覆盖）。

- 新建 **ADR-0010**，首段按 `domain.md` 要求写 `Contradicts ADR-0007 … but worth reopening because …`。
- **ADR-0007** Status 改为 `superseded by ADR-0010`，保留原文存档。
- 澄清禁止对象：ADR-0007 禁止的是 **Chroma 类独立部署的向量数据库服务**（判断仍成立）；pgvector 是 **PostgreSQL 扩展**，非独立服务。
- 澄清与 ADR-0009 不冲突：ADR-0009 禁止的是 LangChain 的 `PGVector` 封装，本实现用裸 SQL。

## 验收清单

- [x] 新建 `docs/adr/0010-跨文档检索改用pgvector扩展.md`，含 Contradicts 声明、Chroma vs pgvector 对比表、四种备选方案取舍。
- [x] ADR-0007 标 `superseded by ADR-0010`，补精确口径。
- [x] 选型文档 §0.3 ADR 关系表同步为「已撤销 superseded by」。
- [x] 项目计划与技术方案 v2 的 ADR 清单补 0007/0009/0010。

## 状态

已落地（历史追溯）。该票同时闭合复盘 W-1 与 W-4（工单 14 措辞反向修订：「改 VECTOR(1024)」→「新增 `embedding_vec` 承载 PG 检索，旧列保留为 dev/降级路径」）。

> 过程中发现 ADR-0007 曾被误删（git `AD` 状态），已从索引恢复后再标记 superseded。
