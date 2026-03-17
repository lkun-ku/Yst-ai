/** R1 / G-01 复盘报告数据塑形的回归测试（平移自 Taro 版，行为不变）。 */
import { describe, expect, it } from "vitest";
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
    expect(s.radar[0].module).toBe("教育法律法规");
    expect(s.radar[0].percent).toBe(30);
    expect(s.radar[2].module).toBe("职业理念");
  });

  it("薄弱前 3 考点透传", () => {
    const s = summarizeReview(sample);
    expect(s.weakTop3).toEqual(["教育法律法规", "职业道德"]);
  });

  it("下一步与段落透传", () => {
    const s = summarizeReview(sample);
    expect(s.nextStep).toBe("优先巩固教育法律法规");
    expect(s.paragraph).toBe("本次复盘 AI 仅供参考");
  });

  it("AIGC 标识保留（内容安全必现点）", () => {
    expect(summarizeReview(sample).aigc).toBe(true);
  });

  it("空掌握度不崩", () => {
    const s = summarizeReview({ mastery: {}, weak_points: [], next_step: "", paragraph: "", aigc_flag: false });
    expect(s.radar).toEqual([]);
    expect(s.aigc).toBe(false);
  });
});
