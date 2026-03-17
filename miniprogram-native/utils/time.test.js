/** R6 / K-02：任务剩余 12h 倒计时格式化的回归测试（平移自 Taro 版，行为不变）。 */
import { describe, expect, it } from "vitest";
import { remainingText } from "./time.js";

describe("remainingText 任务剩余时间", () => {
  const NOW = 1700000000000;

  it("超过 1 小时显示 x小时y分", () => {
    const d = new Date(NOW + (3 * 3600 + 25 * 60) * 1000).toISOString();
    expect(remainingText(d, NOW)).toBe("剩余 3 小时 25 分");
  });

  it("不足 1 小时显示 x分", () => {
    const d = new Date(NOW + 45 * 60 * 1000).toISOString();
    expect(remainingText(d, NOW)).toBe("剩余 45 分");
  });

  it("已过期显示已超时", () => {
    const d = new Date(NOW - 60 * 1000).toISOString();
    expect(remainingText(d, NOW)).toBe("已超时");
  });
});
