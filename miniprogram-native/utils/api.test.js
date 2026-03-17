/** D8 修复的回归测试：stableStringify 必须键序无关，否则去重锁会漏判。 */
import { describe, expect, it } from "vitest";
import { stableStringify } from "./api.js";

describe("stableStringify（去重锁 key 的稳定性）", () => {
  it("对象键顺序不同，序列化结果必须一致", () => {
    const a = stableStringify({ a: 1, b: 2 });
    const b = stableStringify({ b: 2, a: 1 });
    expect(a).toBe(b);
  });

  it("嵌套对象同样键序无关", () => {
    const a = stableStringify({ x: { m: 1, n: 2 }, y: 3 });
    const b = stableStringify({ y: 3, x: { n: 2, m: 1 } });
    expect(a).toBe(b);
  });

  it("值不同则结果不同（不能过度归一化）", () => {
    expect(stableStringify({ a: 1 })).not.toBe(stableStringify({ a: 2 }));
  });

  it("数组保序（顺序即语义）", () => {
    expect(stableStringify([1, 2])).not.toBe(stableStringify([2, 1]));
  });

  it("null / undefined / 基本类型不崩", () => {
    expect(stableStringify(null)).toBe("null");
    expect(stableStringify(undefined)).toBe("null");
    expect(stableStringify(7)).toBe("7");
    expect(stableStringify("s")).toBe('"s"');
  });
});
