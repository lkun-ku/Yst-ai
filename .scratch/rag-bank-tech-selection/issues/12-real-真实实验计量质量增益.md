# 12: real 真实实验计量质量增益

- Status: ready-for-agent（已落地，历史追溯）
- Type: task
- Blocked by: 06, 08
- 关联：`backend/eval/run_eval.py`、`docs/RAG与题库技术选型方案.md` §4.4

## What to build

在真实 LLM/Embedding 下运行对比 harness，计量路线②③ 相对基线（单文档）的真实质量增益——即质量闭环在真实不确定 LLM 下实际纠正的错误数。这是 fake 离线实验无法回答、却决定闭环价值的关键验证。

**接线（供应商：阿里百炼 DashScope OpenAI 兼容）**：
- `EMBEDDING_MODE=real`；`EMBEDDING_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1`；`EMBEDDING_MODEL=text-embedding-v3`（默认）；`EMBEDDING_API_KEY=<百炼 key>`
- `LLM_MODE=real`；`LLM_API_BASE=同上`；`LLM_MODEL=qwen-plus`；`LLM_API_KEY=<百炼 key>`
- `run_eval.py` 使用专用评测库 `eval_kb.db`（不碰生产 PG），每轮重建保证可重复。

用户已提供百炼 key → 本票可执行。

## 验收清单

- [x] 设上述环境变量后 `python backend/eval/run_eval.py --real` 跑通三路线（2026-05-26，百炼 qwen-plus + text-embedding-v3，exit 0）。
- [x] 产出真实质量对比矩阵（质量五维 / LLM 调用 / 成本），可比 fake 数据。
- [x] 闭环增益结论回填 `docs/RAG与题库技术选型方案.md` §4.4（v2.1）。
- [x] 真实报告（`eval_20260910_001021.json/.md`）入库 `results/`，可复现。

## 状态

已落地（历史追溯）。真实实验已运行，关键发现：

| 路线 | 事实性 | 解析 | 唯一性 | 每题调用 |
| --- | --- | --- | --- | --- |
| baseline（无闭环） | 4.875 | 4.904 | 4.800 | 0.57 |
| kb_handwritten（②） | 5.000 | 4.958 | 4.875 | 3.88 |
| kb_langgraph（③） | 5.000 | 5.000 | 4.958 | 4.21 |

- **闭环真实增益确凿**：②③ 把事实性从 4.875 抬到满分 5.0（baseline 在「教育的本质」scope 跌至 4.25），解析亦升至 5.0。
- **框架中立在真实下仍成立**：②③ 质量高度一致（③ 略有优势）→ 选 ③ 不损失质量，仅取可扩展性，验证用户拍板。
- **成本代价**：②③ 每题 LLM 调用约为 baseline 的 7 倍，已在 §2.5 量化。
- 注：baseline 题数翻倍是结构性（无法跨文档合成），非质量优势。

原始报告：`backend/eval/results/eval_20260910_001021.json` + `.md`。
