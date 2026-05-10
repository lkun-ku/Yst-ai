/** R1 / G-01 复盘报告数据塑形的回归测试（平移自 Taro 版，行为不变；runner 见 api.test.js 的说明）。 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { summarizeReview } from "./reviewShape.js";

const sample = {
  session_id: 1,
  mastery: { 职业理念: 0.8, 职业道德: 0.5, 教育法律法规: 0.3 },
  weak_points: ["教育法律法规", "职业道德"],
  next_step: "优先巩固教育法律法规",
  paragraph: "本次复盘 AI 仅供参考",
  aigc_flag: true,
};

describe("summarizeReview 复盘塑形", () => {
  it("雷达项按掌握度升序排，最弱在前", () => {
    const s = summarizeReview(sample);
    assert.equal(s.radar[0].module, "教育法律法规");
    assert.equal(s.radar[0].percent, 30);
    assert.equal(s.radar[2].module, "职业理念");
  });

  it("薄弱前 3 考点透传", () => {
    const s = summarizeReview(sample);
    assert.deepEqual(s.weakTop3, ["教育法律法规", "职业道德"]);
  });

  it("下一步与段落透传", () => {
    const s = summarizeReview(sample);
    assert.equal(s.nextStep, "优先巩固教育法律法规");
    assert.equal(s.paragraph, "本次复盘 AI 仅供参考");
  });

  it("AIGC 标识保留（内容安全必现点）", () => {
    assert.equal(summarizeReview(sample).aigc, true);
  });

  it("空掌握度不崩", () => {
    const s = summarizeReview({
      mastery: {},
      weak_points: [],
      next_step: "",
      paragraph: "",
      aigc_flag: false,
    });
    assert.deepEqual(s.radar, []);
    assert.equal(s.aigc, false);
  });
});
