/** R3 / E-02：多选题四态判定（纯逻辑，零依赖，便于回归测试）。 */

/**
 * 返回单选项在判定后的视觉态：
 * - idle   : 未揭示（无反馈）
 * - correct: 已选且正确
 * - wrong  : 已选但错误
 * - missed : 未选但正确（漏选）—— 与 correct 必须视觉可区分
 * - dim    : 未选且错误（淡化）
 *
 * @param {{revealed:boolean, selected:string[], correct:string[], key:string}} input
 * @returns {"idle"|"correct"|"wrong"|"missed"|"dim"}
 */
export function optionState(input) {
  const { revealed, selected, correct, key } = input;
  if (!revealed) return "idle";
  const isSelected = selected.includes(key);
  const isCorrect = correct.includes(key);
  if (isSelected && isCorrect) return "correct";
  if (isSelected && !isCorrect) return "wrong";
  if (!isSelected && isCorrect) return "missed";
  return "dim";
}

/**
 * 多选整体判定：全对 / 漏选 / 错选。
 * @param {string[]} selected
 * @param {string[]} correct
 * @returns {"all-correct"|"partial"|"wrong"}
 */
export function multiState(selected, correct) {
  const sel = new Set(selected);
  const cor = new Set(correct);
  if (sel.size !== cor.size) return "partial";
  for (const k of sel) if (!cor.has(k)) return "wrong";
  for (const k of cor) if (!sel.has(k)) return "wrong";
  return "all-correct";
}
