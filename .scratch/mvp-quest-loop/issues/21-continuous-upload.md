Status: resolved

# 21 连续上传多份资料（体验 P2）

## 概述
用户上传资料后无法继续上传多份。当前 `docs.js` 上传成功仅 `toast` + `load()` 刷新列表（不跳走），但上传入口是否常驻取决于 `docs.wxml`（待确认）。需确保上传控件常驻、支持连续上传，且保留「去出题」入口。

## 范围
- in：上传控件在资料页常驻；上传成功后留在本页并 `toast("已上传，可继续添加")`；支持连续/批量选择（`wx.chooseMessageFile` 当前 `count:1`，可循环或批量）；资料页明确保留「去出题」按钮。
- out：跨端上传（仅微信单端，ADR-0005）。

## 依赖（Blocked by）
- （无；精确根因待读 `docs.wxml` 确认）

## 验收标准（Acceptance Criteria）
- 上传一份资料后，上传入口仍可见，可继续添加第二份/多份。
- 上传成功提示「已上传，可继续添加」，不误跳走。
- 资料页保留「去出题」入口（按已选/全部资料出题）。
- 不破坏现有 `navigateTo` 链路。

## 关联
- 体检报告 §3②、§6 P2。
- `miniprogram-native/pages/docs/docs.js:93-96`（上传成功回调）。
- `miniprogram-native/pages/docs/docs.wxml`（上传入口常驻性待确认）。

## 备注
- 根因存疑：JS 层不跳走，更可能是 wxml 上传入口上传后不可见或 UX 感知问题，需先读 wxml 确认。

## 解决
- `docs.js`：`wx.chooseMessageFile` 由 `count:1` 改为 `count:9` 支持批量选择；新增 `_uploadFiles` 顺序上传多份，全部完成后统一 `load()` 并 toast「已上传，可继续添加」。
- `_upload` 改为返回 Promise，便于顺序 `await`；上传入口在 `docs.wxml` 常驻，保留「去出题」入口。
- 验证：前端 `node --check` 通过。
