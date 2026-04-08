Status: resolved

# 22 自建题库改名 + 子项 + 格式限制（体验 P3）

## 概述
「新建试卷」入口仍是首页顶级入口且未改名，无格式限制提示。需改名为「自建题库」、移入「我的资料」(`docs`) 页作为子项，并在 `kb.wxml` 加出题范围字数限制（≤50 字）与题型数量上限提示。

## 范围
- in：首页 `ENTRIES` 第 6 项改名「自建题库」并移入 `docs` 页子项（或首页保留但标注归属）；`kb.wxml` 增加范围字数限制提示与题型数量上限提示。
- out：新增其他入口结构（保持现有 navigateTo 链路）。

## 依赖（Blocked by）
- （无）

## 验收标准（Acceptance Criteria）
- 入口展示为「自建题库」（非「新建试卷」）。
- 入口归属清晰（子项或标注归属），跳转链路不破坏。
- `kb.wxml` 展示范围字数限制（≤50 字）与题型数量上限提示。

## 关联
- 体检报告 §3①、§6 P3。
- `miniprogram-native/pages/index/index.js:8-16` `ENTRIES`（第 6 项）。
- `miniprogram-native/pages/kb/kb.wxml`（限制提示）。
- `miniprogram-native/pages/docs/docs.js`（跳转关系）。

## 备注
- 改名仅改展示文案与跳转，风险低；注意同步 `docs.js` 跳转关系，不破坏现有 `navigateTo`。

## 解决
- 首页 `index.js` ENTRIES 移除「新建试卷」顶级入口（保留「我的资料」→docs）。
- `docs.wxml` 新增「自建题库 · 组卷出题」入口按钮，`docs.js` 新增 `goKb()` 跳转 `/pages/kb/kb`。
- `kb.wxml` 出题范围 `maxlength` 200→50 并提示「≤50 字」；补充「单类上限 100 题」提示。
- 验证：前端 `node --check` 通过；`navigateTo` 链路不变。
