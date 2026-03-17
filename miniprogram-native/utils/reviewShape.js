/** R1 / G-01：复盘报告数据塑形（纯逻辑，零依赖，便于回归测试）。 */

/**
 * @param {{mastery?:Record<string,number>, weak_points?:string[], next_step?:string, paragraph?:string, aigc_flag?:boolean}} r
 */
export function summarizeReview(r) {
  const src = r || {};
  const mastery = src.mastery || {};
  const radar = Object.entries(mastery)
    .map(([module, v]) => ({ module, percent: Math.round((v || 0) * 100) }))
    .sort((a, b) => a.percent - b.percent); // 最弱在前，便于优先关注
  return {
    radar,
    weakTop3: (src.weak_points || []).slice(0, 3),
    nextStep: src.next_step || "",
    paragraph: src.paragraph || "",
    aigc: !!src.aigc_flag,
  };
}
