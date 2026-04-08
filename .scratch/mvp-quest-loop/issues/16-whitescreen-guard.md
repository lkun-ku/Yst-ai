Status: resolved

# 16 白屏兜底与根因定位（阻塞）

## 概述
小程序页面在运行期（`onLoad/onShow/onReady` 或渲染中）抛出未捕获异常时整页变白，且无任何提示，用户只见白屏、控制台红字易被忽略。需加全局错误兜底把错误显形，并对关键异步页加错误态 UI（重试而非空白），同时集中防御 `JSON.parse`/字段访问失败分支。

## 范围
- in：`app.js` 增加 `onError` 显形（弹窗/错误页）；`answer`/`kb` 等关键页加错误态 UI（加载失败展示「重试」而非空白）；统一 `JSON.parse` 带默认值的防御封装；字段访问用 `?.` 与默认值；失败分支 `setData` 合法可渲染结构（空态/错误态）。
- out：具体白屏触发页的根因逐页定位（需 `onError` 显形后据真实报错栈确认，属本单验收的一部分，但修复以兜底为主，逐页根因可作后续）。

## 依赖（Blocked by）
- （无）

## 验收标准（Acceptance Criteria）
- `app.js` 有 `onError` 处理，运行期异常可被捕获并显形（不再「白屏无声」）。
- `answer`/`kb` 页在异步加载失败时展示错误态 UI（含重试），不整页空白。
- 所有页面 `JSON.parse` 失败回退到合法默认值（`[]`/对象），不对 `undefined` 调方法。
- 失败响应（5xx/422、字段缺失）下前端失败分支 `setData` 的为可渲染结构，不白屏。
- 新增/补充前端语法校验（`node --check`）纳入提交校验。

## 关联
- 体检报告 §5（白屏专项）、§6 P0。
- `miniprogram-native/app.js`（缺 `onError`）。
- `miniprogram-native/pages/answer/answer.js:150-156` `_stateText`（解析链风险点）。
- `miniprogram-native/pages/docs/docs.js` / `kb/kb.js`（异步失败分支）。

## 备注
- 用户已确认：本次白屏为运行期异常，非构建转译问题（构建风险见 #19，独立）。
- 诊断用 `diagnosing-bugs` 六步循环；先加 `onError` 显形定位真实触发页。

## 解决
- `app.js` 增加 `onError` 全局兜底：运行期未捕获异常弹窗显形 + `console.error`，消除「白屏无声」。
- 关键异步页（answer/kb）失败分支已多为 try/catch + 错误态（`loadError`/`onRetry`），失败分支 `setData` 合法可渲染结构；逐页真实根因需据此显形后据报错栈确认（运行期），本单以兜底为主。
- 回归：前端 `node --check` 通过；pytest 全量通过。
