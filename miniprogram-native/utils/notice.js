/** R2 / B-02：AIGC 首次说明是否展示（纯逻辑，零依赖）。 */

export function shouldShowAigcNotice(acked) {
  return !acked;
}
