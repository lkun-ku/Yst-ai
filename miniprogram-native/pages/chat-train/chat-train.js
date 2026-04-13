import { request, toastApiError } from "../../utils/api.js";

/** #31 AI 模拟答：配置 → 聊天对话（追问式评分）→ 汇总/趋势。 */
const MODULES = ["职业理念", "职业道德", "教育法律法规", "文化素养", "基本能力"];
const MAX_Q = 5;
const ACTIVE_KEY = "chat_active_session";

Page({
  data: {
    mode: "config", // config | chat
    modules: MODULES,
    module: MODULES[0],
    difficulty: "medium",
    persona: "coach",
    msgs: [], // {seq, role, turnType, content, shown, typing, score, hit, missed, wrong, suggestion}
    input: "",
    thinking: false,
    finished: false,
    summary: null,
    progress: { cur: 1, total: MAX_Q },
    scrollInto: "",
  },

  onLoad() {
    // 会话恢复：切后台被回收后回前台，重放未结束的对话
    const sid = wx.getStorageSync(ACTIVE_KEY);
    if (sid) this.resume(sid);
  },

  onUnload() {
    this._stopTyping();
  },

  onHide() {
    this._stopTyping();
  },

  /** ---- 配置态 ---- */
  onPickModule(e) {
    this.setData({ module: e.currentTarget.dataset.m });
  },
  onPickDifficulty(e) {
    this.setData({ difficulty: e.currentTarget.dataset.d });
  },
  onPickPersona(e) {
    this.setData({ persona: e.currentTarget.dataset.p });
  },

  async onStart() {
    try {
      wx.showLoading({ title: "准备中", mask: true });
      const body = await request("/api/chat/start", {
        method: "POST",
        data: { module: this.data.module, difficulty: this.data.difficulty, persona: this.data.persona },
      });
      wx.setStorageSync(ACTIVE_KEY, body.session_id);
      this.setData({
        mode: "chat",
        msgs: [],
        finished: false,
        summary: null,
        progress: body.progress,
      });
      for (const m of body.messages) {
        await this._pushTyping(m);
      }
    } catch (e) {
      toastApiError(e);
    } finally {
      wx.hideLoading();
    }
  },

  /** ---- 会话恢复：重放历史消息（无打字机，直接渲染） ---- */
  async resume(sid) {
    try {
      const body = await request(`/api/chat/session/${sid}`);
      if (body.status !== "active") {
        wx.removeStorageSync(ACTIVE_KEY);
        return;
      }
      const msgs = body.messages.map((m) => this._toMsg(m, true));
      this.setData({ mode: "chat", msgs, progress: body.progress, finished: false });
      this._scrollBottom();
    } catch (e) {
      wx.removeStorageSync(ACTIVE_KEY);
    }
  },

  /** ---- 对话 ---- */
  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  async onSend() {
    const content = (this.data.input || "").trim();
    if (!content || this.data.thinking || this.data.finished) return;
    // 同步互斥锁（#32）：setData 异步，data 检查挡不住连点；实例标志同 Breakeven 置位
    if (this._sending) return;
    this._sending = true;
    try {
      this.setData({
        msgs: [...this.data.msgs, this._toMsg({ role: "user", turn_type: "user", content }, true)],
        input: "",
      });
      await this._reply({ content });
    } finally {
      this._sending = false;
    }
  },

  /** 跳过追问，直接看评分 */
  async onForceFinal() {
    if (this.data.thinking || this.data.finished || this._sending) return;
    this._sending = true;
    try {
      await this._reply({ content: "（我想直接看评分）", forceFinal: true, local: false });
    } finally {
      this._sending = false;
    }
  },

  async _reply({ content, forceFinal = false, local = true }) {
    const sid = wx.getStorageSync(ACTIVE_KEY);
    if (!sid) return;
    if (local) this.setData({ msgs: [...this.data.msgs, this._toMsg({ role: "user", turn_type: "user", content }, true)] });
    this.setData({ thinking: true });
    this._scrollBottom();
    try {
      const body = await request("/api/chat/reply", {
        method: "POST",
        data: { session_id: sid, content, force_final: forceFinal },
      });
      const extra = {};
      if (body.finished) {
        this.setData({ finished: true, summary: body.session_summary });
        wx.removeStorageSync(ACTIVE_KEY);
      } else {
        extra.progress = body.progress;
        this.setData(extra);
      }
      for (const m of body.messages) {
        if (m.role === "user") continue; // 用户消息已在本地渲染
        await this._pushTyping(m);
      }
      if (body.finished) {
        await this._pushSummary(body.session_summary);
      }
    } catch (e) {
      // 评分失败：给出可重试的提示气泡（不落库，与后端 503 语义一致）
      this.setData({
        msgs: [
          ...this.data.msgs,
          this._toMsg({ role: "ai", turn_type: "error", content: "网络开小差了，评分没能完成——请点输入框右侧「重试」或重新发送你的回答。" }, true),
        ],
      });
      this._scrollBottom();
      toastApiError(e);
    } finally {
      this.setData({ thinking: false });
      this._scrollBottom();
    }
  },

  /** ---- 消息渲染工具 ---- */
  _toMsg(m, full) {
    return {
      seq: m.seq || 0,
      role: m.role,
      turnType: m.turn_type,
      content: m.content || "",
      shown: full ? m.content || "" : "",
      typing: !full && m.role === "ai",
      score: m.score,
      hit: m.points_hit || [],
      missed: m.points_missed || [],
      wrong: m.points_wrong || [],
      suggestion: m.suggestion || "",
    };
  },

  /** 打字机：AI 消息逐字渲染（30ms/2字，消息 <300 字无性能风险） */
  _pushTyping(m) {
    return new Promise((resolve) => {
      const idx = this.data.msgs.length;
      const msg = this._toMsg(m, false);
      this.setData({ msgs: [...this.data.msgs, msg], scrollInto: "" });
      this._scrollBottom();
      if (m.role !== "ai") {
        resolve();
        return;
      }
      const full = msg.content;
      let i = 0;
      const timer = setInterval(() => {
        i = Math.min(i + 2, full.length);
        const key = `msgs[${idx}].shown`;
        this.setData({ [key]: full.slice(0, i) });
        if (i % 20 === 0) this._scrollBottom();
        if (i >= full.length) {
          clearInterval(timer);
          this.setData({ [`msgs[${idx}].typing`]: false });
          this._scrollBottom();
          resolve();
        }
      }, 30);
    });
  },

  _pushSummary(summary) {
    return new Promise((resolve) => {
      this.setData({ summary: { ...summary } });
      this._scrollBottom();
      resolve();
    });
  },

  _scrollBottom() {
    // #32 修复：锚点 id 固定为 chat-anchor-a/b，值交替触发重滚（同值 scroll-into-view 不生效）
    this._anchorFlip = !this._anchorFlip;
    this.setData({ scrollInto: this._anchorFlip ? "chat-anchor-a" : "chat-anchor-b" });
  },

  _stopTyping() {
    // 页面隐藏时无需持久处理；打字 interval 在 setData 后自然失效，
    // 恢复时走 resume() 全量重放，无状态损坏。
  },

  /** 结束态操作 */
  onRestart() {
    this.setData({ mode: "config", msgs: [], finished: false, summary: null, input: "" });
  },
});
