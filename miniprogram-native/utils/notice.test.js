/** R2 / B-02：AIGC 首次说明展示判定（平移自 Taro 版，行为不变；runner 见 api.test.js 的说明）。 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { shouldShowAigcNotice } from "./notice.js";

describe("shouldShowAigcNotice", () => {
  it("未确认过 → 展示", () => {
    assert.equal(shouldShowAigcNotice(false), true);
  });

  it("已确认 → 不再展示（B-02 核心）", () => {
    assert.equal(shouldShowAigcNotice(true), false);
  });
});
