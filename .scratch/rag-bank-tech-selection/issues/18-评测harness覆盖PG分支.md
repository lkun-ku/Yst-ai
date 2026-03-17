# 18: 评测 harness 覆盖 PG 分支 + 真实 PG 端到端验证

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 16
- 关联：`backend/eval/run_eval.py`、`backend/app/services/kb_retrieval.py`、`backend/alembic/versions/`

## What to build

`run_eval.py` 强制 `DATABASE_URL` 为 SQLite（`eval_kb.db`），因此 `retrieve_by_scope_pg` 的 **PG 分支永远不会被评测 harness 执行**。工单 13 验收写「SQLite/PG 双路 pytest 绿」，其中 PG 一路实际未验证。

- 让评测 harness 支持 PG：新增 `EVAL_DATABASE_URL` 开关。
- 在真实 PG 上跑通迁移与 PG 分支检索。
- 记录 PG 分支与内存分支的召回差异。

## 验收清单

- [x] harness 支持 PG 数据源（`EVAL_DATABASE_URL`），未设置时仍用独立 `eval_kb.db`。
- [x] harness 在 PG 上同步写 `embedding_vec`（否则 `embedding_vec IS NOT NULL` 过滤掉全部切片）。
- [x] 真实 PG 上 `alembic upgrade head` 跑通（15 张表 + VECTOR(1024) 列 + HNSW 索引）。
- [x] PG 分支检索端到端验证：`_is_pg` 分发 → SQL 余弦 → 正确排序。
- [x] 全量 pytest 通过（230）。

## 状态

已落地（历史追溯）。**关键突破：本地直连成功**——根因是用户名，TencentDB 的管理员账号是 **`lkun`** 而非 `postgres`（`postgres` 被 pg_hba 拒）。此前一直试 `postgres` 导致误判为「白名单问题」。

### 直连前提（三道门，缺一不可）
1. 安全组放行客户端 IP 到 `TCP:5432`
2. SSL 开启（pg_hba 要求加密）
3. **用户名用 `lkun`**（不是 postgres）

### 真实 PG 验证结果（腾讯云 TencentDB PostgreSQL 18.6 + pgvector 0.8.2）
```
alembic upgrade head → 15 张表，version = m1a2b3c4d5e6
embedding      : bytea            （LargeBinary，dev 路径保留）
embedding_vec  : vector           ← VECTOR(1024) 已建
索引           : document_chunks_embedding_vec_hnsw USING hnsw (embedding_vec vector_cosine_ops)
retrieve_by_scope: dialect=postgresql, _is_pg=True
  seq=0 sim=1.0000  ← 查询命中
  seq=1 sim=0.0000
  seq=2 sim=0.0000
```

### 本票发现并修复的两个真实 bug（此前测试无法暴露，因测试从不跑 alembic/PG）
1. **迁移链非幂等（阻断生产部署）**：`0001_initial` 用 `Base.metadata.create_all()` 建立
   **当前 models 的全量** schema，后续 5 个迁移又逐条 `ADD COLUMN` / `CREATE TABLE`，
   在全新库上 `alembic upgrade head` 必然 duplicate 失败。
   → 已把 `12ead2620c5f` / `6c138b351314` / `1b007f4825a3` / `9b438ae98caf` / `7a1c0ffee123`
   全部改为幂等（对象已存在则跳过），迁移保持自包含。
2. **PG 分支绑定参数丢失（阻断全部 PG 检索）**：SQL 里写 `:q::vector` 时，SQLAlchemy 的
   `text()` 把 `::` 当转换符处理，`:q` **不被识别为绑定参数而静默丢失**（运行时参数只剩 cid/k）。
   → 改用 `CAST(:q AS vector)`。

### 已知限制
- PG 上跑 harness 需 `EMBEDDING_MODE=real`（1024 维）。fake 模式是 64 维，与
  `VECTOR(1024)` 列维度不符，无法插入。
- PG 分支只走向量通道，**不参与关键词 RRF 与标题加成**，与内存路径召回存在细微差异
  （生产切片均带 embedding，可接受）；如需完全对齐应另开工单。
