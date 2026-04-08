Status: resolved

# 20 主观题误显「部分对」（体验 P1）

## 概述
前端 `answer.js:_stateText` 未区分题型，填空/简答（可接受答案为多个）即使用户乱写，也显示「部分对（漏选/错选）」。后端 `sessions.py:_judge` 实际正确（blank 归一化匹配、short 标 `self_review` 不计入正确率）。属展示误判，纯前端文案修复。

## 范围
- in：`_stateText` 对 `blank`/`short` 题型不套用「部分对」文案，改为「已作答（参考答案见解析）」；最终判分以提交后后端 `_judge` 结果（review 页）为准。
- out：改变后端判分逻辑（后端正确，不动）；review 页 `self_review` 提示（需确认已到位）。

## 依赖（Blocked by）
- （无）

## 验收标准（Acceptance Criteria）
- `blank`/`short` 题型不显示「部分对（漏选/错选）」误文案。
- 主观题展示「已作答（参考答案见解析）」，提交后 review 页以 `_judge` 为准。
- 补充 `_stateText` 题型分支单测或后端 `_judge` 用例守护。
- 前端 `node --check` 通过。

## 关联
- 体检报告 §3④、§6 P1。
- `miniprogram-native/pages/answer/answer.js:150-156` `_stateText`。
- `backend/app/sessions.py:221-250` `_judge` / `_judge_blank` / `_judge_short`。

## 备注
- 根因已定位（前端误判），改动小、风险低。

## 解决
- `answer.js` `_stateText` 增加题型分支：`blank`/`short` 不再套用「部分对（漏选/错选）」，改为「已作答（参考答案见解析）」；其余题型逻辑不变。
- 后端 `_judge` 本就正确（blank 归一化匹配、short 标 `self_review` 不计入正确率），前端展示与后端判分一致。
- 验证：前端 `node --check` 通过；复用既有 `_qtype`/`_isCorrect`，无新增回归。
