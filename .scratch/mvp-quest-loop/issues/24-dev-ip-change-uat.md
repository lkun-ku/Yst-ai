Status: open

# 24 真机调试时 dev 机 IP 变更导致上传/请求失败（阶段三候选）

## 概述
走查发现：开发机局域网 IP 变更后（截图中小程序实际请求的 `172.20.10.2:8000` 已不可达），`wx.uploadFile` 与 `wx.request` 报 `ERR_CONNECTION_REFUSED`，「我的资料」页上传资料功能随之失效。`utils/api.js` 已提示“换 WiFi 后需同步改这里”，但当前 BASE 仍是硬编码，换网/换机后需要手动改源码，开发体验差且容易误以为是功能回归。

## 范围
- in：改善真机预览/调试时的 BASE 配置方式（文档化、自动发现、或本地持久化可覆盖）；优化失败提示（提示当前 BASE、期望改哪里）。
- out：生产部署域名切换（生产走域名/CloudBase，不在本单）。

## 优先级
P2（开发体验，非生产阻塞）。

## 验收标准（Acceptance Criteria）
- [ ] 提供一份真机调试配置说明（README/AGENTS），包含如何查看本机局域网 IP 与修改 BASE。
- [ ] 或实现 BASE 可本地覆盖（如从 storage/globalData 读取，避免改源码）。
- [ ] 连接失败提示至少包含“当前 BASE = xxx，请到 utils/api.js 修改”。
- [ ] 上传失败弹窗文案与通用请求文案一致，避免重复提示。

## 关联
- 本次走查（2026-05-27）。
- `miniprogram-native/utils/api.js:18-19` 硬编码 BASE。
- `miniprogram-native/utils/errClassify.js:24` 连接失败兜底提示。
- `miniprogram-native/pages/docs/docs.js:98` 上传使用 BASE。

## 备注
- 根因是开发环境网络拓扑变化，不是代码 bug；但当前硬编码 BASE + 提示不足会反复绊倒本地走查。
- 生产环境通过域名/CloudBase 访问，不受此影响。
