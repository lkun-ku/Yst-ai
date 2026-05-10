# 前端测试从 vitest 改用 Node 内置 test runner

## Status

accepted（2026-06-11）

> 上游：[ADR-0005](./0005-弃用Taro改用微信原生.md)（前端改原生后只剩 `utils/` 有可单测的纯逻辑）。
> 相关：`docs/改造计划.md` §5 未决。

## Context

前端 `utils/` 下 6 个 `*.test.js`（共 189 行）用 vitest 写。本会话核对时发现
**它们一条都跑不起来**：

```
npx vitest run
  → node_modules/vitest/dist/cli.js:1
    SyntaxError: Unexpected identifier 'as'
    Node.js v20.20.2
```

崩在**任何测试文件被加载之前** —— 是 vitest 自身的入口文件以 ESM 被加载、里面却写着
`require`。而 `miniprogram-native/package.json` 当时**没有 `type` 字段**（即默认 CJS），
所以这是已装 vitest 版本与 Node v20.20.2 的兼容问题，**与业务代码无关**。

**后果不是"少跑几个测试"，而是"前端没有回归网"**：`node --check` 只查语法，
查不出 `stableStringify` 哪天变得键序敏感、`optionState` 的四态哪天合并成三态。
而这两个函数各自都对应着一条已记录的历史缺陷（D8 去重锁漏判、E-02 漏选态）。

修它需要换 vitest 版本 → 需要 npm registry → **本环境 DNS 按域名限制**（`pypi.org`、
`registry.npmjs.org` 均不可解析，`ntce.neea.edu.cn` 可以）。所以"升级/降级依赖"这条常规路线
在本环境**走不通**。

## Decision

**改用 Node 内置 test runner**（`node --test`），并把 `package.json` 的 `scripts.test`
改为 `node --test utils/`。

具体两步：

1. **`package.json` 加 `"type": "module"`** —— 这是能否跑起来的前提：`utils/*.js` 用 ESM 写法，
   而 Node 在无 `type` 字段时把 `.js` 当 CJS，`import` 直接语法错误。
   （副带收益：`node --check pages/*.js` 现在按 ESM 检查，与源码写法一致。）
2. **6 个测试文件从 vitest API 迁到 `node:test` + `node:assert/strict`**：
   `expect(x).toBe(y)` → `assert.equal(x, y)`；`.not.toBe` → `assert.notEqual`；
   `.toContain` → `assert.ok(s.includes(t))`；`.toEqual` → `assert.deepEqual`。

**`vitest` 保留在 `devDependencies` 里，不删。** 理由很具体：删了会让 `package.json`
与 `package-lock.json` 不同步，`npm ci` 会直接报错（"not in sync"）—— 而重生成 lock
同样需要 npm registry。所以这不是"忘了删"，是**在依赖不可达的环境里保持 lock 一致的唯一解**；
`package.json` 里用 `"//"` 字段写明这一点，免得后来者当成漏删而"顺手清理"。

## Considered Options

- **升级/降级 vitest**（否决）：需要 npm registry，本环境 DNS 不通。
- **手改 `node_modules` 里 vitest 的入口文件**（否决）：改的是不入库的目录，
  换机/重装即失效，且下次有人跑 `npm ci` 就回来了。
- **继续只用 `node --check`**（否决）：它查不出**行为**回归，而这里两个函数恰恰都曾有行为缺陷。
- **给测试文件改 `.mjs` 后缀**（否决）：测试能跑，但它 import 的 `utils/*.js` 仍会被当 CJS → 照样失败；
  要成必须把 `utils/` 也改后缀，而小程序运行时按 `.js` 找文件。
- **删掉 vitest 依赖**（否决）：见 Decision 最后一段 —— 会让 `npm ci` 失败。

## Consequences

- **前端回归网恢复**：`npm test` → **28 项通过 / 0 失败，约 0.28s**（6 个套件）。
  用例数与迁移前**逐套件一致**（5+6+7+2+5+3），没有在迁移中丢掉断言。
- **前端测试不再依赖任何第三方包**，与 ADR-0005 以来"零 npm 运行时依赖"的取向一致。
- 代价：**`node:test` 的断言消息不如 vitest 丰富**（`assert.equal` 的 diff 可读性弱一些）；
  且这份测试**只覆盖 `utils/` 的纯逻辑** —— `pages/` 仍无自动化测试
  （那需要小程序运行时，只能靠开发者工具，本环境跑不了）。
- 偏离了计划书技术栈里写的"pytest + vitest"。这是**环境约束下的被迫偏离**，
  已在 `docs/改造计划.md` §5 登记。

### 已知边界

1. **`vitest` 仍是 `devDependencies` 的一项**（为 lock 一致），但它**永远跑不起来** ——
   文档与 `package.json` 的 `"//"` 字段都写明了，避免有人被它误导。
2. **`pages/` 无自动化测试**：两个新页面（teacher / subjective）的验证手段只有
   `node --check` 与一次性 WXML 自检（标签配对 / 插值配对 / 绑定的处理函数是否存在）。
   **渲染正确性必须由人在微信开发者工具里确认** —— 这一点写进了页面的交付说明。
3. `node --test` 需要 Node ≥ 18（本机 v20.20.2 ✅）。

## 反转条件

1. **若 npm registry 恢复可达**，应评估是否换回 vitest（它的断言可读性更好、
   生态更熟）。**但换回的前置条件是先跑通一次**，而不是"改回去试试" ——
   本轮就是因为"装了却从没跑过"才让缺陷潜伏了这么久。
2. **若前端接入构建期校验**（如小程序 CI 或 miniprogram-simulate 之类的组件测试），
   应把 `pages/` 纳入自动化 —— 那才是前端测试的下一层，本轮只恢复了 `utils/` 这一层。
