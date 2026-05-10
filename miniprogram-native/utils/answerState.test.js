/** R3 / E-02 多选题四态标记的回归测试（平移自 Taro 版，行为不变；runner 见 api.test.js 的说明）。 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { optionState } from "./answerState.js";

describe("optionState 四态", () => {
  const correct = ["A", "B"];

  it("未揭示时为 idle", () => {
    assert.equal(optionState({ revealed: false, selected: [], correct, key: "A" }), "idle");
  });

  it("已选且正确为 correct", () => {
    assert.equal(optionState({ revealed: true, selected: ["A"], correct, key: "A" }), "correct");
  });

  it("已选但错误为 wrong", () => {
    assert.equal(optionState({ revealed: true, selected: ["C"], correct, key: "C" }), "wrong");
  });

  it("未选但正确为 missed（漏选态，核心修复点）", () => {
    assert.equal(optionState({ revealed: true, selected: ["A"], correct, key: "B" }), "missed");
  });

  it("未选且错误为 dim", () => {
    assert.equal(optionState({ revealed: true, selected: ["A"], correct, key: "C" }), "dim");
  });

  it("漏选态与已选正确态必须不同（四态可区分）", () => {
    const picked = optionState({ revealed: true, selected: ["A"], correct, key: "A" });
    const missed = optionState({ revealed: true, selected: ["A"], correct, key: "B" });
    assert.notEqual(missed, picked);
  });
});
