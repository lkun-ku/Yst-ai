# 25 - 生题流式展示（WorkBuddy 式过程区）

Status: resolved

## 需求

出题等待 45~90s 期间用户只能看「已完成 X/Y」两个数字，需要 WorkBuddy 式过程展示，并支持中断与续做。

## 关键决策与理由

- 否决 SSE/token 流：微信官方文档明确 enableChunked 暂仅支持 Android，iOS 退化为一次性返回；且出题输出是结构化 JSON，token 碎片无法渲染成题卡。采用增量事件轮询 + 前端打字机（流式感与传输粒度解耦）。
- 事件存储用独立表 kb_task_events 而非 JSON 列：避免 append 的 O(n^2) 写入放大，支持 (task_id, seq) 索引增量拉取。
- 切片两级分离（D1）：事件只存 60 字摘要，全文按需 GET /api/kb/chunk/{id}。
- 协作式取消（D3/D4）：Python 无法安全杀线程、LLM 单次调用不可中断，批次边界检查 cancel_requested，已出题保留为部分卷。
- 缺口重试（D5）：任务保存原始请求参数，重试按已出题数扣减 spec 只补缺口。

## 交付

- 后端：kb_task_events 表 + 迁移；services/kb_events；路线二三节点插桩；接口 events?since / cancel / retry / chunk/{id}。
- 前端：kb 页全屏过程区（时间线打字机/渐现/滚轮/思考句）、answer 页过程时间线（路线一 doc_task_events 同构）。
- 后续增强：时间线改规划式叙述、模型 thinking 构思句 + 真实上下文兜底、动态滚轮。

## 验证

真实 qwen-plus：事件流 retrieve/grade/rewrite/batch/question 齐全；按需取全文 960 字；取消转 cancelled；重试按缺口补齐；pytest 全量通过。

## 遗留

- kb/generate 页时间线 emoji 图标（UI 深度重构二期处理）。
