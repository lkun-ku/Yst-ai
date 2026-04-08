Status: resolved

# 17 VIP 代码清理（完全免费，阻塞）

## 概述
阶段一 B2 裁决：产品定为**完全免费、不做 VIP**。但代码仍残留 VIP 骨架（权益页、`quota.vip-activate`、ADR-0001 内测占位），与裁决方向冲突，属上线前必须清理的一致性技术债。

## 范围
- in：移除/标注 VIP 相关展示与分支（权益说明页、`quota.vip-activate` 调用、VIP 差异化 UI）；消除与「完全免费」裁决的冲突。
- out：真实支付链路（本就不做，见阶段一 B2）；若 `is_vip` 字段影响既有数据，保留字段仅作向后兼容或评估一并清理（需确认数据影响）。

## 依赖（Blocked by）
- （无；依据阶段一裁决记录 B2）

## 验收标准（Acceptance Criteria）
- 代码内无「VIP / 会员 / 付费差异化」类展示与激活分支（或仅保留无害的向后兼容占位且明确注释已废弃）。
- 产品表达与「完全免费」一致（首页/我的资料页无付费引导）。
- 既有免费用户数据不受影响（额度逻辑保持「全内容可刷、仅限速」）。
- 无因清理引入的回归（pytest + 前端 `node --check` 通过）。

## 关联
- 阶段一裁决记录 B2（完全免费）。
- `docs/需求分析文档.md` S4（原「VIP 差异化」已被 B2 推翻，待重定/删除）。
- `issues/12-quota-vip.md`（原 VIP 工单，状态 resolved 但代码残留需清）。
- ADR-0001（内测占位，需标注废弃）。

## 备注
- 阶段一已登记为「待清理工单」，阶段二必须清零以满足门禁。
- 清理前先 `git stash`/分支，单会话作业，避免误删免费额度逻辑。

## 解决
- 删除 `miniprogram-native/pages/benefits/`（js/json/wxml/wxss 四文件）+ `app.json` 注销该页。
- `utils/errClassify.js`：429 不再带 `action: "benefits"` 导航（完全免费，无 VIP 引导）。
- 后端 `routers/quota.py`：移除 `vip-activate` 端点与 `is_vip` 不限量分支；`schemas.py` 删除 `VipActivateOut`；`is_vip` 字段保留作向后兼容（恒为 False）。
- 同步 `tests/test_quota.py`（删 VIP 测试）、`uat_walk.py`（VIP 步骤改断言 404 + 额度上限 1000）。
- 回归：pytest 全量通过（236 pass / 1 skip）；前端 `node --check` 通过。
