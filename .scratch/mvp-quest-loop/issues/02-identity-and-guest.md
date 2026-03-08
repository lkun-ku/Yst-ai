Status: resolved

# 02 身份与游客

## 概述
实现「进入即用」：允许游客先完成一局再授权微信登录；登录后拿到 unionid 作为身份主键；首次使用展示一次 AIGC 说明；提供「查看/删除学习数据」入口占位。
- 遵守 ADR-0001：用户表以 unionid 为主键，绑定微信开放平台账号。

## D1 决策（2026-05-21 确认）
接受内测期数据重置：按 unionid 设计，但**不**引入手机/邮箱绑定等可移植身份方案。换 AppID 时若 unionid 无法保留，内测数据清空（量级小可接受）。

## 范围
- in：游客态（本地标识）、微信静默登录拿 unionid、首次 AIGC 说明一次、数据查看/删除入口占位、内容安全输入侧占位（接票 13）。
- out：账号合并/迁移的复杂逻辑、真实删除后端实现（先占位，逻辑后续接）。

## 依赖（Blocked by）
- 01

## 验收标准（Acceptance Criteria）
- 未授权可进入小程序并完成一局闯关（游客态，记录保存在本地）。
- 授权后微信登录成功，拿到 unionid；用户表以 unionid 为主键。
- 游客局在授权后身份稳定（D1：内测期不强制合并历史游客数据；过渡策略以本地为准）。
- 首次使用展示一次 AIGC 说明（题目由 AI 生成、仅供参考），之后不再弹出（本地/服务端开关）。
- 提供「查看/删除我的学习数据」入口，点击进入占位页。

## 关联
- 用户故事 #1、#2、#3、#4、#5、#6。
- Implementation 25（unionid）、ADR-0001（主体与合规路径）、Implementation 28（AIGC 说明）。

## 备注
- 「身份在更换 AppID 后仍可识别」（用户故事 #4）依赖微信开放平台账号绑定，D1 已确认接受失败风险。
- 内容安全输入检测的真实拦截在票 13 接入，本票先留调用点。

## Comments

- 2026-05-21：实现完成。后端 `/api/identity`：游客登录（生成 guest_ 身份）、微信登录（按 unionid upsert，is_guest=false）、me（X-Unionid 头）、ack-aigc（首次说明一次性）、delete-data（清该考生全部学习数据，D1 重置可用）。`deps.get_current_candidate` 以 X-Unionid 识别。前端：`utils/api.ts`（含 ensureIdentity 游客取号）、首页 AIGC 确认调用、设置页（查看/删除数据）。pytest 7/7 通过。真实微信 code→unionid 交换占位为直接接收 unionid。
