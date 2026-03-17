/**
 * 微信原生小程序入口（弃用 Taro 后的重写版）。
 *
 * 对应原 Taro 版 `miniprogram/src/app.js`：
 * - 原版需要 `process` polyfill（Taro Vue3 dist 在小程序环境引用 Node 全局），原生不需要，已删除
 * - 原版用 Pinia 管理跨页闯关局上下文，此处改用 `globalData`（原 store 实际只消费 sessionId + questions）
 */

App({
  globalData: {
    /** 当前闯关局上下文，跨页面传递（替代原 Pinia useSessionStore） */
    session: {
      sessionId: 0,
      questions: [],
      module: "",
      knowledgePoint: "",
    },
    /** S3 模考：mode / durationSec / deadlineAt / serverNow（服务端绝对时间，防客户端改表） */
    exam: {
      mode: "normal",
      durationSec: 0,
      deadlineAt: "",
      serverNow: "",
    },
    /** 全局请求中计数（O-07），由 utils/api.js 维护，此处仅作镜像便于页面读取 */
    requesting: 0,
  },

  onLaunch() {
    // 原生环境无 Taro 运行时，无需任何 polyfill
  },

  /**
   * 写入当前闯关局。字段名沿用后端 snake_case 入参，内部转 camel。
   */
  setSession(payload) {
    const p = payload || {};
    this.globalData.session = {
      sessionId: p.session_id || 0,
      questions: p.questions || [],
      module: p.module || "",
      knowledgePoint: p.knowledge_point || "",
    };
    this.globalData.exam = {
      mode: p.mode || "normal",
      durationSec: p.duration_sec || 0,
      deadlineAt: p.deadline_at || "",
      serverNow: p.server_now || "",
    };
  },

  /** 清空当前闯关局（交卷后调用）。 */
  clearSession() {
    this.globalData.session = { sessionId: 0, questions: [], module: "", knowledgePoint: "" };
    this.globalData.exam = { mode: "normal", durationSec: 0, deadlineAt: "", serverNow: "" };
  },

  /** 供页面读取当前闯关局。 */
  getSession() {
    return this.globalData.session;
  },
});
