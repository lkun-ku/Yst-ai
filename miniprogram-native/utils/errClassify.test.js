/** R4 / O-03 全局错误分类的回归测试（平移自 Taro 版，行为不变）。 */
import { describe, expect, it } from "vitest";
import { classifyRequestError } from "./errClassify.js";

describe("classifyRequestError 统一错误分类", () => {
  it("401 身份失效：兜底文案引导重新进入", () => {
    const r = classifyRequestError(401, "");
    expect(r.kind).toBe("unauthorized");
    expect(r.message).toContain("重新进入");
  });

  it("409 并发冲突：兜底文案不恐慌", () => {
    const r = classifyRequestError(409, "");
    expect(r.kind).toBe("conflict");
    expect(r.message).toContain("已有一局");
  });

  it("429 额度耗尽：兜底明示明天再来（VIP 已下线，不再引导）", () => {
    const r = classifyRequestError(429, "");
    expect(r.kind).toBe("quota_exceeded");
    expect(r.message).toContain("明天");
    expect(r.message).not.toContain("VIP");
  });

  it("410 任务超时：兜底提示已超时", () => {
    const r = classifyRequestError(410, "");
    expect(r.kind).toBe("task_expired");
    expect(r.message).toContain("超时");
  });

  it("网络错误（无状态码）：兜底网络异常文案", () => {
    const r = classifyRequestError(0, "");
    expect(r.kind).toBe("network");
    expect(r.message).toContain("网络");
  });

  it("其他 5xx：兜底服务端异常", () => {
    const r = classifyRequestError(500, "");
    expect(r.kind).toBe("server");
    expect(r.message).toContain("稍后");
  });

  it("后端 detail 优先作为展示文案", () => {
    const r = classifyRequestError(429, "今天已经刷满啦");
    expect(r.message).toBe("今天已经刷满啦");
  });
});
