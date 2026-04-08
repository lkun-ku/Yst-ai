Status: resolved

# 18 内容安全 wx 模式生产开启（阻塞·合规）

## 概述
内容安全接入点 `WxContentSafetyClient` 已就绪，但真实微信 `msgSecCheck`（`CONTENT_SAFETY_MODE=wx`）需手动开启，默认未启用。生产上线前必须启用真实内容安全校验，否则 UGC（上传资料/题目）存在合规风险。

## 范围
- in：默认 `CONTENT_SAFETY_MODE=wx`（真实微信 msgSecCheck）；补齐生产环境配置与验证；确认上传资料与出题内容经内容安全校验。
- out：自建内容安全模型（用微信官方即可）；非 wx 环境下的 dev/test 仍可用 shadow/strict 占位模式。

## 依赖（Blocked by）
- （无）

## 验收标准（Acceptance Criteria）
- 生产配置下 `CONTENT_SAFETY_MODE=wx` 生效，真实 msgSecCheck 被调用。
- 上传资料 / 出题内容触发内容安全校验，违规内容被拦截并友好提示。
- dev/test 环境仍为可跑的 shadow/占位模式（不影响本地开发）。
- 配置变更有文档说明（CONFIG 说明 + README/AGENTS）。

## 关联
- 体检报告 §2.1（审校后台 UI 与真实 `wx` 模式需手动开启）、§7。
- `backend/app/services/content_safety.py`（`WxContentSafetyClient`）。
- `CONTENT_SAFETY_MODE` 配置项。

## 备注
- 个人主体内测阶段也建议开启 wx 模式以验证真实链路；正式合规以个体户 + 类目备案为准（UAT 清单遗留）。

## 解决
- `backend/app/config.py`：`content_safety_mode` 默认 `"stub"` → `"wx"`（真实 msgSecCheck）。
- `content_safety.py`：`get_content_safety()` 在 `mode==wx` 且配齐 `WX_APPID/WX_SECRET` 才走真实校验，否则自动降级 stub，故默认 wx 不阻塞本地开发。
- `AGENTS.md` 补配置说明（生产需 `WX_APPID`/`WX_SECRET`）。
