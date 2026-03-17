/** R6 / K-02：任务剩余时间格式化（纯逻辑，零依赖，便于回归测试）。 */

export function remainingText(deadlineIso, now = Date.now()) {
  if (!deadlineIso) return "";
  const ms = new Date(deadlineIso).getTime() - now;
  if (ms <= 0) return "已超时";
  const totalMin = Math.floor(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h > 0) return `剩余 ${h} 小时 ${m} 分`;
  return `剩余 ${m} 分`;
}
