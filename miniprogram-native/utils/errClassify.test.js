/** R4 / O-03 全局错误分类的回归测试（平移自 Taro 版，行为不变；runner 见 api.test.js 的说明）。 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { classifyRequestError } from "./errClassify.js";

describe("classifyRequestError 统一错误分类", () => {
  it("401 身份失效：兜底文案引导重新进入", () => {
    const r = classifyRequestError(401, "");
    assert.equal(r.kind, "unauthorized");
    assert.ok(r.message.includes("重新进入"), r.message);
  });

  it("409 并发冲突：兜底文案不恐慌", () => {
    const r = classifyRequestError(409, "");
    assert.equal(r.kind, "conflict");
    assert.ok(r.message.includes("已有一局"), r.message);
  });

  it("429 额度耗尽：兜底明示明天再来（VIP 已下线，不再引导）", () => {
    const r = classifyRequestError(429, "");
    assert.equal(r.kind, "quota_exceeded");
    assert.ok(r.message.includes("明天"), r.message);
    assert.ok(!r.message.includes("VIP"), r.message);
  });

  it("410 任务超时：兜底提示已超时", () => {
    const r = classifyRequestError(410, "");
    assert.equal(r.kind, "task_expired");
    assert.ok(r.message.includes("超时"), r.message);
  });

  it("网络错误（无状态码）：兜底网络异常文案", () => {
    const r = classifyRequestError(0, "");
    assert.equal(r.kind, "network");
    assert.ok(r.message.includes("网络"), r.message);
  });

  it("其他 5xx：兜底服务端异常", () => {
    const r = classifyRequestError(500, "");
    assert.equal(r.kind, "server");
    assert.ok(r.message.includes("稍后"), r.message);
  });

  it("后端 detail 优先作为展示文案", () => {
    const r = classifyRequestError(429, "今天已经刷满啦");
    assert.equal(r.message, "今天已经刷满啦");
  });
});
