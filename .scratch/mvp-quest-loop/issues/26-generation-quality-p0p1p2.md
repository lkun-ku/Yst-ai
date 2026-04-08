# 26 · 出题质量攻坚（P0 0产出 / P1 耗时 / P2 并发 / A 认知层级 / 遗留四项）

Status: resolved（遗留 3 项见文末）

## P0 大卷 0 产出（根因 A-1 自检截断）

自检把 chunks[:6] 拼接后截 800 字，而生成侧可看 30000 字——依据后段切片出的题在自检时看不到原文，必判无依据而误杀，8 题卷 0 产出。
修复：自检依据按题溯源（_ctx_for_question 优先取该题 source_id 切片，上限 doc_selfcheck_chars=8000）；_generate_batch_with_fallback 规则校验前置 + 降粒度重试（3-2-1）；自检全否时仍保留规则通过项（不欠产核心语义）。

## P1 耗时（45~90s 到 20s）

自检改抽检（doc_selfcheck_sample=2，均匀取样，通过即整批放行，不通过升级全批）；单趟遍历避免重复检测。

## P2 并发治理

裸线程收敛为有界线程池 task_pool（ThreadPoolExecutor）；出题 gen 与向量化 embed 分池——出题内 wait_embed_ready 等向量化，共用池会互抢 worker 死锁。DOC_GEN_WORKERS=4 / DOC_EMBED_WORKERS=2。

## A 认知层级

BLOOM_DESC + bloom_distribution；提示词显式注入层级分布；默认 understand,apply,analyze（刻意不含 remember，研究显示 AI 默认 62% 出记忆题）；接口可选 bloom 字段。批次 6 改 3（实测 6 题批模型常只出 3 题需多轮补偿 133.5s，小批量 58.8s）。

## 遗留四项（均已处理）

1. 时区一致性：local_day 收口 app/utils（kb 用 UTC 日与本地日比较致配额失效）。
2. 回归测试：tests/test_kb_regression_26.py 17 用例锁结构语义。
3. 题干近似判重：near_duplicate 3-gram Jaccard，路线①阈值 0.85 / 路线②③ 0.6，fake 跳过（同模板伪题 0.8 会误杀）。
4. bloom 解析收敛 utils.parse_bloom_levels。

## 真实环境验证

8 题卷 0 到 8；3 题 45~90s 到 20.2s；并发 3 任务 20.5s 无死锁；6 题 58.8s 含应用/分析题；全部 pytest 通过。

## 未做（后续票）

- 防批间同质化（跨批 bloom 轮转 + 已覆盖考点摘要）——转入 #27 尾巴。
- 难度 IRT 校准 / 考点树覆盖控制（长期）。
