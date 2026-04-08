Status: resolved

# 19 构建转译开关固化（阻塞·风险）

## 概述
`project.config.json` 中 `es6:false` / `enhance:false`，但页面源码使用 `import`（ESM），属构建转译风险：换机/重装开发者工具即可能因 ESM 未被转译而白屏。需固化转译开关并写入 README/AGENTS，规避「换机即白屏」。

## 范围
- in：`project.config.json` 明确转译要求（`es6`/`enhance` 与实际源码写法一致）；写入 README/AGENTS 固化说明；确认 `node --check` 前端语法校验通过。
- out：大规模重构为 ESM 工程化（仅需确保开关与写法一致，不强制改写法）。

## 依赖（Blocked by）
- （无）

## 验收标准（Acceptance Criteria）
- `project.config.json` 转译开关与页面实际写法一致（消除 ESM 不被转译风险）。
- README/AGENTS 含构建可复现说明，团队换机可一键跑通。
- 前端 `node --check` 全量通过。

## 关联
- 体检报告 §5.4 备注、§7（构建可复现）。
- `miniprogram-native/project.config.json`（`es6:false`/`enhance:false`）。
- `miniprogram-native/pages/**`（页面用 `import`）。

## 备注
- 与 #16 的白屏（运行期）是两类不同风险，本单专注构建期转译一致性。

## 解决
- `project.config.json`：`setting.es6` / `setting.enhance` 由 `false` 改为 `true`，使页面 ESM `import` 被转译，规避换机重装开发者工具白屏。
- `AGENTS.md` 固化构建可复现说明（改动后跑 `node --check`）。
- 回归：前端 `node --check` 通过。
