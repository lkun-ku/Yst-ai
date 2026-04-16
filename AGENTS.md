# AI 闯关学习小程序

微信小程序项目。以下是工程技能的仓库约定。

## Agent skills

### Issue tracker

Issues 以本地 Markdown 文件存放在 `.scratch/<feature-slug>/issues/NN-<slug>.md`。See `docs/agents/issue-tracker.md`.

### Triage labels

使用默认五个标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

single-context 布局：根 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.

## 构建与配置（阶段二固化，#18/#19）

- `project.config.json`：`setting.es6` / `setting.enhance` 已开启，页面源码使用 ESM `import`，
  转译开关须与写法一致，否则换机重装开发者工具即白屏。改动后跑 `node --check` 校验前端语法。
- 内容安全：默认 `CONTENT_SAFETY_MODE=wx`（真实微信 msgSecCheck，见 `app/services/content_safety.py`）。
  生产环境须配置 `WX_APPID` / `WX_SECRET`，缺凭证时自动降级 `stub` 放行，不阻塞本地开发。
- 产品已裁决「完全免费、不做 VIP」：移除 VIP 权益页与 `vip-activate` 分支（`is_vip` 字段保留作向后兼容，恒为 False）。

## 作业纪律

- 单会话作业、改动前 `git stash` / 建分支；修复后跑 `pytest` 全量 + 前端 `node --check`，确认无回归。
