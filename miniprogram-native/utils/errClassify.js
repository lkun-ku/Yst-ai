/** R4 / O-03：HTTP 错误统一分类（纯逻辑，零依赖，便于回归测试）。 */

/**
 * @typedef {"unauthorized"|"conflict"|"quota_exceeded"|"task_expired"|"network"|"server"} ApiErrorKind
 */

/**
 * @param {number} status
 * @param {string} detail
 */
export function classifyRequestError(status, detail) {
  const d = (detail || "").trim();

  switch (status) {
    case 0:
      return { kind: "network", message: d || "网络异常，请检查网络后重试" };
    case 401:
      return { kind: "unauthorized", message: d || "身份已失效，请重新进入小程序" };
    case 409:
      return { kind: "conflict", message: d || "已有一局正在进行，请先完成或返回", action: "restart" };
    case 410:
      return { kind: "task_expired", message: d || "任务已超时（仅当日有效），明天再来吧" };
    case 429:
      return { kind: "quota_exceeded", message: d || "今日免费额度已用完，明天再来" };
    default:
      if (status >= 500) return { kind: "server", message: d || "服务器开小差了，请稍后重试" };
      return { kind: "server", message: d || "请求失败，请稍后重试" };
  }
}

/** api 层抛出的统一错误类型，携带分类信息。 */
export class ApiRequestError extends Error {
  constructor(status, detail) {
    const info = classifyRequestError(status, detail);
    super(info.message);
    this.name = "ApiRequestError";
    this.status = status;
    this.kind = info.kind;
    this.action = info.action;
  }
}
