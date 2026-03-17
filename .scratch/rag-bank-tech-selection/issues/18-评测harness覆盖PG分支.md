# 18: 评测 harness 覆盖 PG 分支

- Status: ready-for-agent
- Type: task
- Blocked by: 16
- 关联：`backend/eval/run_eval.py`、`backend/app/services/kb_retrieval.py`（`_is_pg` / `retrieve_by_scope_pg`）

## What to build

现状：`run_eval.py` 强制 `DATABASE_URL` 为 SQLite（`eval_kb.db`），因此 `retrieve_by_scope_pg` 的 **PG 分支永远不会被评测 harness 执行**。工单 13 验收写「SQLite/PG 双路 pytest 绿」，其中 PG 一路实际未验证。

- 让评测 harness 支持 PG：新增开关（如 `--pg <url>` 或环境变量 `EVAL_DATABASE_URL`），指向 PG 时全程走 PG 分支。
- 或在无 PG 环境时**明确注明不覆盖**，避免验收清单误导。
- 对比 PG 分支与内存分支的召回结果（题数、顺序、分值），记录差异。

## 验收清单

- [ ] harness 支持 PG 数据源，可跑通三路线。
- [ ] 与 SQLite 结果对比，记录召回差异（预期：PG 分支无关键词 RRF 与标题加成，存在细微差异）。
- [ ] 文档注明 PG 分支的覆盖范围。
- [ ] 无 PG 环境时，验收清单明确标注「未覆盖」而非「通过」。

## 状态

待办；**当前受阻塞**——本地到腾讯云 PG 的外网连接受 `pg_hba` 限制（IP 白名单未放行），无法从本地直连执行。需先解决白名单，或改用可直连的 PG 实例。

复盘优先级 P1（依赖可用的 PG 实例）。
