# 27 · 四项需求 + 全站 UI 改版「纸卷 v1」

Status: resolved

## 四项需求（用户确认方案后实施）

1. 资料页：PATCH /api/documents/{id} 重命名 + doc-detail 查看页（章节树 + 切片按需全文，复用 /api/kb/chunk/{id}）。
2. 历史闯关：重练 409 根因修复（sessions.py 去掉 source==POOL 过滤，个人题可练；owner_candidate_id 防越权；不足降级开局不 409）；每局显示重练入口；答题中/复盘页加重练入口（A+B）。
3. 错题本：个人题重练随 2 修复；答对即移出错题本（交卷时删除记录）。
4. 每日任务页删除：考期 picker 直改上首页 + 连胜徽章；今日任务并入错题本（开始直达重练）；后端 /api/daily /api/streak mark_task_progress 全保留。

## 主流程对齐（路线一）

用户主流程「我的资料-出题」走路线一 doc_generate，此前质量闭环只覆盖路线二三。修复：判重阈值路线一 0.85（章节配额题干天然相似，0.6 成批误杀）；接入 _generate_batch_with_fallback（gen_fn 注入 + selfcheck=False）；取整损耗校正（计划数=选择数）；补偿 3 到 6 轮 + 多候选 limit 截断 + 0.95 兜底。30 题卷从只出几道到 25~28（5 切片小文档受内容容量限制）。

## UI 大改版「纸卷 v1」

概念图 3 方向 x 3 屏（imagegen-frontend-mobile），用户选方向 A。规范固化 docs/design-spec.md（design-taste-frontend + high-end-visual-design + gpt-taste 按小程序载体裁剪）。
- 三核心屏深度重构：answer（平铺+发丝线选项五态+CSS线性对错标记+答题卡弹层+底部固定操作区）/ index（考期140rpx大数字居中+入口发丝线列表+Reicon单色图标+模块卡边框阴影）/ review（战绩hero+五维细条+骨架屏）。
- 检查点1三屏：quest/history/mistakes（模块行列表+战绩文字色+分组行列表，feedback 去 emoji）。
- 5 屏令牌重映射过渡层：docs/settings/generate/kb/doc-detail 旧变量指向规范色（布局深度重构留二期）。
- 公共类提升 app.wxss：card-mod/btn-main/btn-sub/zone-label/empty-note；令牌 --paper/--ink-*/--primary/--danger-ink/--ease-spec。
- 用户实测迭代：考期居中+数字140rpx+无框立于桌面；卡片边框明显化+极轻阴影（修订规范零阴影条款为一种极轻阴影）；修复 stat-zone/inline-row padding 覆盖致文字贴边的 CSS 层级疏失。

## 提交

21a752e（三核心屏）/ 2a0ec9e（检查点1）/ 9b91dbb（5屏映射+固化）/ caa7765（规范+概念图存档）。

## 遗留

- kb/generate 深度重构（时间线 emoji 图标、表单行距）。
- 防批间同质化（跨批 bloom 轮转 + 已覆盖考点摘要）。
