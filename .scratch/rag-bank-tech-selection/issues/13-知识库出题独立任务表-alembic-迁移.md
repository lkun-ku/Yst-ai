# 13: 知识库出题独立任务表 + Alembic 迁移

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 08
- 关联：`backend/app/routers/kb.py`、`backend/app/models.py`、`backend/alembic/versions/7a1c0ffee123_kb_task_and_source_chunk.py`、issue `15-知识库出题结果交付接口.md`

## What to build

将知识库出题的进度/任务状态从模块级内存字典（`_tasks`，进程重启即丢、多 worker 不一致）替换为数据库独立任务表，并**顺带持久化生成结果 ID**（供 15 交付接口拉取）。对外异步轮询接口不变。

- 新增 `KbTask` 表：`task_id`(str,PK) / `candidate_id` / `status` / `done` / `total` / `count` / `error` / `generated_question_ids`(Text JSON) / `created_at`。
- Alembic 迁移脚本创建表。
- `kb.py._run` 改为：进度写 `KbTask`；完成时写 `generated_question_ids=json.dumps([q.id for q in created])`（对齐 `DocTask`）。
- `GET /api/kb/task/{id}` 改为查 `KbTask`（并加 `candidate_id` 隔离，防越权读他人任务）。
- dev SQLite 与生产 PG 双路均 pytest 绿。

## 验收清单

- [x] 新增 `KbTask` 表（含 `generated_question_ids` 与 `candidate_id` 隔离）。
- [x] Alembic 迁移创建表，可 upgrade/downgrade（`7a1c0ffee123_kb_task_and_source_chunk.py`）。
- [x] `_run` 进度 + 完成题 ID 落 `KbTask`；移除模块级 `_tasks` 字典。
- [x] `GET /api/kb/task/{id}` 读 `KbTask` 并校验归属；全量 pytest 通过（SQLite/PG 双路）。

## 状态

已落地（历史追溯）。接缝处补 TDD 测试锁定行为（`backend/tests/test_kb_task_persistence.py`，4 例全绿）：

| 测试 | 锁定行为 |
| --- | --- |
| `test_任务进度落库且跨Session可读` | 任务状态落 `KbTask`，关 session 后新 session 仍可读（内存字典实现在此必失败） |
| `test_他人任务返回404` | `candidate_id` 归属校验，防越权读他人任务 |
| `test_任务接口返回进度与题数` | `GET /api/kb/task/{id}` 读 `KbTask` 的 status/done/total/count |
| `test_模块级内存字典已移除` | 回归护栏：`kb` 模块不得再有 `_tasks` 内存字典 |

PG 双路：生产 PG 下 `retrieve_by_scope` 由工单 14 的 `_is_pg` 分发到 `retrieve_by_scope_pg`，任务表本身与方言无关（同 `KbTask` 模型），SQLite/PG 均走同一套 KbTask 读写。
