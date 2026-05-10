/** D8 修复的回归测试：stableStringify 必须键序无关，否则去重锁会漏判。
 *
 * 用 Node 内置 test runner（`node --test`）而不是 vitest —— 原因见 `docs/adr/0022`：
 * 本机 Node v20.20.2 下已装的 vitest 在**入口文件**就崩（它自己的 `dist/cli.js`
 * 以 ESM 被加载却写着 `require`），而修它要换版本、换版本要 npm registry（本环境 DNS 不通）。
 * 内置 runner 零依赖、`node --test` 直接可用。
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { stableStringify } from "./api.js";

describe("stableStringify（去重锁 key 的稳定性）", () => {
  it("对象键顺序不同，序列化结果必须一致", () => {
    const a = stableStringify({ a: 1, b: 2 });
    const b = stableStringify({ b: 2, a: 1 });
    assert.equal(a, b);
  });

  it("嵌套对象同样键序无关", () => {
    const a = stableStringify({ x: { m: 1, n: 2 }, y: 3 });
    const b = stableStringify({ y: 3, x: { n: 2, m: 1 } });
    assert.equal(a, b);
  });

  it("值不同则结果不同（不能过度归一化）", () => {
    assert.notEqual(stableStringify({ a: 1 }), stableStringify({ a: 2 }));
  });

  it("数组保序（顺序即语义）", () => {
    assert.notEqual(stableStringify([1, 2]), stableStringify([2, 1]));
  });

  it("null / undefined / 基本类型不崩", () => {
    assert.equal(stableStringify(null), "null");
    assert.equal(stableStringify(undefined), "null");
    assert.equal(stableStringify(7), "7");
    assert.equal(stableStringify("s"), '"s"');
  });
});
