/** R3 / E-02 多选题四态标记的回归测试（平移自 Taro 版，行为不变）。 */
import { describe, expect, it } from "vitest";
import { optionState } from "./answerState.js";

describe("optionState 四态", () => {
  const correct = ["A", "B"];

  it("未揭示时为 idle", () => {
    expect(optionState({ revealed: false, selected: [], correct, key: "A" })).toBe("idle");
  });

  it("已选且正确为 correct", () => {
    expect(optionState({ revealed: true, selected: ["A"], correct, key: "A" })).toBe("correct");
  });

  it("已选但错误为 wrong", () => {
    expect(optionState({ revealed: true, selected: ["C"], correct, key: "C" })).toBe("wrong");
  });

  it("未选但正确为 missed（漏选态，核心修复点）", () => {
    expect(optionState({ revealed: true, selected: ["A"], correct, key: "B" })).toBe("missed");
  });

  it("未选且错误为 dim", () => {
    expect(optionState({ revealed: true, selected: ["A"], correct, key: "C" })).toBe("dim");
  });

  it("漏选态与已选正确态必须不同（四态可区分）", () => {
    const picked = optionState({ revealed: true, selected: ["A"], correct, key: "A" });
    const missed = optionState({ revealed: true, selected: ["A"], correct, key: "B" });
    expect(missed).not.toBe(picked);
  });
});
