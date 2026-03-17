# 17: source_chunk 批级溯源（消除 top-1 误导）

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 15
- 关联：`backend/app/services/kb_generate.py`、`backend/app/services/kb_graph.py`、`backend/app/services/doc_generate.py`、`docs/RAG路线三落地复盘.md` §5.3

## What to build

现状：5 处写入点均为 `source_chunk=picked[0]["content"] if picked else None`，即**一个批次召回 top-k（k=8）切片，但该批次生成的全部题目共享首个切片内容**。

- `kb_generate.py:178`、`:197`
- `kb_graph.py:123`、`:143`、`:189`

问题：题目可能源自 `picked[3]` 或 `picked[7]`，溯源却一律指向 `picked[0]` —— **溯源可用但不精确，会误导**。

方案（复盘 §5.3 选定 **B**，此处改用 chunk `id` 而非 `seq`，因其全局唯一、可直接回查 `document_chunks`）：

- `source_chunk` 改为存**批级切片 id 清单 JSON**，如 `"[12,35,88,201]"`。
- 保留 `doc_generate._persist_questions` 的透传签名不变，仅改写入端构造值。
- 同步注释与 schema 说明（字段语义由「切片文本」变为「切片 id 清单」）。
- 前端/消费方按 id 回查切片内容（不在本票范围内）。

## 验收清单

- [x] 5 处写入点改为写入批级 id 清单 JSON（不再只取 `picked[0]`）。
- [x] `source_chunk` 为空（无召回）时仍为 `None`，行为不变。
- [x] 补单测 `tests/test_source_chunk.py`（4 例）：全部 id 清单 / 空值 None / 缺 id 跳过 / 回归护栏（不再是首片文本）。
- [x] 全量 pytest 通过（230）。

## 状态

已落地（历史追溯）。实现要点：

- `doc_generate.build_source_chunk(picked)` 新增 helper（单一实现，消除 5 处重复）。
- `_chunk_payloads`（`doc_generate` 与 `kb_generate`）携带 `id`；已确认 `kb_question_prompt`
  只读 `seq/heading_path/content`，新增键**不影响提示词**。
- 采用 **chunk `id`** 而非复盘原建议的 `seq`：`id` 全局唯一可直接回查 `document_chunks`，
  而 `seq` 是文档内序号，跨文档场景下有歧义。

TDD 过程：先写 4 例测试（RED：ImportError），再实现 helper 与 5 处替换（GREEN），全量回归无副作用。
