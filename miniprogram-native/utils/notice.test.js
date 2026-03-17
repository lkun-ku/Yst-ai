/** R2 / B-02：AIGC 首次说明展示判定（平移自 Taro 版，行为不变）。 */
import { describe, expect, it } from "vitest";
import { shouldShowAigcNotice } from "./notice.js";

describe("shouldShowAigcNotice", () => {
  it("未确认过 → 展示", () => {
    expect(shouldShowAigcNotice(false)).toBe(true);
  });

  it("已确认 → 不再展示（B-02 核心）", () => {
    expect(shouldShowAigcNotice(true)).toBe(false);
  });
});
