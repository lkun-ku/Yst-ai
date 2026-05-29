# `data/answer_bank/` —— 答案库（**独立于知识库**）

用户提供了 2011–2026 年的真题与解析，希望答题/批改时**优先依据真题与满分答案**。
但既定决策 **ADR-0003 是「真题原文不入库」**。这里的做法是两全的 **方案 B**：

| | `data/official/`（知识库） | **`data/answer_bank/`（本目录）** |
| --- | --- | --- |
| 被 `kb_corpus` 自动摄入 | **是**（`rglob("*.md")`） | **否**（在 `official_kb_dir` 之外） |
| 权威级别 | 官方 | **半官方**（教辅整理） |
| 在答题里的位置 | 兜底（答案库命中不足时才用） | **优先** |

实现落在 `app/services/answer_bank.py`：`search_answer_bank()` 先查本库，
`retrieve_for_question()` 命中不足再回落官方语料 —— **"优先"由检索顺序保证，而不是把真题塞进知识库**。

## 产物

`真题答卷库.json`（由 `scripts/build_answer_bank.py` 生成，可重复跑）：

```json
{"note": "...", "authority": "半官方", "counts": {...},
 "items": [{"id","type","stage","subject","stem","options","answer","trap","reference","points","source"}]}
```

- **单选**：`stem + options + answer`（来自 OCR 结构化出的真题集）；
- **主观题**：`stem + reference（标准/示范作答 = 满分卷）+ points（采分点）`。

## 两条硬约束（写在代码里，也写在这里）

1. **命中必须标注 `authority="半官方"`** —— 教辅整理，**不得冒充官方原文**；
2. **命中内容要能支撑引用子串校验** —— 防幻觉的硬机制（`quote in content`）不能绕。

## 为什么是关键词匹配而不是向量

embedding 供应商（百炼）当前**欠费**，`embed_one` 会**静默退回 64 维伪向量**
（`strict_embed` 已如实记 `failed`）。**用伪向量做相似度等于自欺**，所以这里用
确定性关键词匹配（CJK 二元切分 + 词频打分），不依赖外部服务、结果可复现。
等 embedding 恢复后，再叠加向量召回作为补充信号。
