Status: resolved

# 23 出题上限过低 + 生成不足（体验 P4）

## 概述
出题上限过低（`MAX_PER_TYPE=30`）且用户设 N 题实际只出 9 道左右。前端默认 `SPEC_DEF` 单选=3、其余=0；后端 `kb_generate.py`/`kb_graph.py` 单批 LLM 产出不足、去重丢弃重复题、按考点/切片限流未补齐，导致产出短搠。

## 范围
- in：前端 `MAX_PER_TYPE` 30→100（与后端 `doc_max_q_per_task` 对齐）；后端生成器增加「补齐轮次 + 每批多生成余量」逻辑，确保 `count == sum(spec.count)`（或明确告知缺口）。
- out：降低生成质量（仅补量，保持校验）；额度上限放开到无上限（保持限速，见 #17 完全免费）。

## 依赖（Blocked by）
- （无；确切根因待读 `kb_generate.py`/`kb_graph.py` 确认）

## 验收标准（Acceptance Criteria）
- 前端 `MAX_PER_TYPE=100`，默认 `SPEC_DEF` 合理（用户可设到更大题量）。
- 后端按 `spec.count` 精确产出（含补齐轮次），`count == sum(spec.count)` 或显式告知缺口。
- 提高上限不引发超时/额度失控（评估 `doc_daily_gen_limit` 与轮询超时）。
- pytest 含题数精确匹配回归用例。

## 关联
- 体检报告 §3③、§6 P4。
- `miniprogram-native/pages/kb/kb.js:19` `MAX_PER_TYPE=30`；`kb.js:4-10` `SPEC_DEF`。
- `backend/app/services/kb_generate.py` / `kb_graph.py` `generate_by_scope*`。
- `backend/app/routers/kb.py:95-105`。

## 备注
- 提高上限会增加 LLM 额度消耗与生成耗时，需同步评估超时与限流。

## 解决
- 前端 `kb.js` `MAX_PER_TYPE` 30→100（与后端 `doc_max_q_per_task` 对齐）。
- 后端 `config.doc_max_q_per_task` 默认 30→100（`normalize_spec` 截断上限）。
- **修复补偿循环根因**：`kb_generate.py`/`kb_graph.py` 原补偿轮 `for qtype, count in spec` 误把 **dict 列表**解包成 dict 键（qtype="type"），请求非法题型致产出失败；改为遍历 `batches`（(qtype,count) 元组），并增强为最多 5 轮、每轮 `min(doc_batch_size*3, need)` 余量，确保 `count≈sum(spec.count)`。
- 回归：新增「欠产补偿填满到目标数」用例（路线②/③ 各一），pytest 全量通过。
