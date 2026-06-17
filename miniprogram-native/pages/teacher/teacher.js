import { request, toastApiError } from "../../utils/api.js";

/**
 * 问答老师：**有据答疑**（RAG for QA）。
 *
 * 与 `pages/chat-train` 是两件事：那边是「面试官提问 → 我回答 → 给分」，训练"答"；
 * 这边是「我提问 → 检索考纲/法条 → 带引用作答」，解决"问"。两个页面共用
 * `utils/api.js` 的请求层，但后端端点、数据形状完全不同（`/api/teacher/ask`）。
 *
 * ## 三档模式为什么必须都留着
 * `plain`（不检索）不是残留的旧路径 —— 计划书要求「保留无据版作对照」。
 * 它与 `grounded` 走**同一套提示词、同一个接缝、同一个引用校验**，
 * 唯一差异是有没有检索。这样对照出来的差异才能归因到"检索"本身，
 * 而不是混进了模型换代或提示词漂移。
 */
const MODES = [
  {
    key: "grounded",
    name: "有据快答",
    hint: "检索一次官方语料后作答。适合条文、定义这类有明确出处的考点。",
  },
  {
    key: "agent",
    name: "多跳追问",
    hint: "模型自己决定查什么、查几次。适合需要跨条文比较的问题（如两部法的同一概念）。",
  },
  {
    key: "plain",
    name: "无据对照",
    hint: "不检索、直接作答 —— 用来对照。此模式下回答很可能没有出处，仅供比较。",
  },
];

/** 快捷提问：对应计划书点名的四类口语场景（考点速览 / 易混辨析 / 法条原文 / 常见问法）。 */
const QUICKS = [
  "教师法第七条规定了哪些权利？",
  "「学生观」和「儿童观」有什么区别？",
  "未成年人保护法里关于学校保护有哪些条文？",
  "材料分析题一般从哪几个角度作答？",
];

/** 与后端 `routers/teacher.py` 的 MAX_QUESTION 对齐：前端先挡一次，省一次往返。 */
const MAX_QUESTION = 500;

/** 只保留可读字符 —— 与后端 `citation.normalize_for_match` 同一口径（见下方 _sourceOf 的说明）。 */
const _UNREADABLE = /[^\u4e00-\u9fff0-9A-Za-z]/g;

function _norm(s) {
  return String(s || "").replace(_UNREADABLE, "");
}

/**
 * 反查一条引用的出处（如「教师法 / 第二章 / 第七条」）。
 *
 * **为什么要在前端反查**：后端 `citations` 只有 `{quote, source}`，而 `source` 通常为空；
 * 真正带 `heading_path` 的是 `evidence`。两者不直接对应，所以用**归一化子串**去 evidence
 * 里找这条引文出自哪一段。
 *
 * 口径刻意与后端一致（只比汉字/字母/数字）—— 这样"前端标出的出处"与"后端校验通过的引用"
 * 是同一套判定：如果前端反查得到，就说明该引文确实落在某段依据里；反查不到（`matched=false`）
 * 也是一种信息，说明引文可能来自被截断的部分，前端会显式标注而不是编一个出处。
 */
function _resolveCite(cite, evidence) {
  const quote = (cite && cite.quote) || "";
  if (cite && cite.source) return { quote, source: cite.source, matched: true };
  const q = _norm(quote);
  if (!q) return { quote, source: "", matched: false };
  for (const ev of evidence || []) {
    if (_norm(ev.content).indexOf(q) >= 0) {
      return { quote, source: ev.heading_path || "官方语料", matched: true };
    }
  }
  return { quote, source: "", matched: false };
}

Page({
  data: {
    MODES,
    QUICKS,
    mode: "grounded",
    modeHint: MODES[0].hint,
    // {k, role, content, shown, typing, citations, citeOpen, confidence, refused, toolCalls, error}
    msgs: [],
    input: "",
    thinking: false,
    scrollInto: "",
  },

  onLoad() {
    this._seq = 0; // 消息唯一键：wx:key 用下标会在插入/重排时错位
    this._anchorFlip = false;
    this._timers = new Set();
    this._alive = true;
  },

  onUnload() {
    // 打字机的 setInterval 若不清理，页面销毁后仍会调 setData（无害但脏，且会拖住页面实例）
    this._alive = false;
    this._timers.forEach((t) => clearInterval(t));
    this._timers.clear();
  },

  /* ---------------- 模式与输入 ---------------- */

  onPickMode(e) {
    const key = e.currentTarget.dataset.key;
    const picked = MODES.find((m) => m.key === key);
    if (!picked || this.data.thinking) return;
    this.setData({ mode: picked.key, modeHint: picked.hint });
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  onQuick(e) {
    const q = e.currentTarget.dataset.q;
    if (!q || this.data.thinking) return;
    this.setData({ input: q });
    this.onSend();
  },

  onToggleCite(e) {
    const i = Number(e.currentTarget.dataset.idx);
    const row = this.data.msgs[i];
    if (!row) return;
    this.setData({ [`msgs[${i}].citeOpen`]: !row.citeOpen });
  },

  /**
   * 多轮：把已有对话整理成后端要的历史。
   *
   * 三个细节，都不是小事：
   * - 取 `content` 而不是 `shown` —— `shown` 是逐字动画的中间态，送出去会是半句话；
   * - 跳过错误气泡与仍在打字的那条 —— 把它们当历史会把"请求失败"喂给模型；
   * - **去掉当前这一问**：它在发请求前就已入气泡（见 `_ask`），不删会与 `question` 重复。
   *   （后端还会再裁一次轮数与单条长度，出口统一在 `teacher_agent._trim_history`。）
   */
  _history(current) {
    const rows = [];
    for (const m of this.data.msgs) {
      if (m.error || m.typing) continue;
      const content = (m.content || "").trim();
      if (!content) continue;
      rows.push({ role: m.role === "user" ? "user" : "ai", content });
    }
    const last = rows[rows.length - 1];
    if (last && last.role === "user" && last.content === String(current || "").trim()) {
      rows.pop();
    }
    return rows.slice(-6);
  },

  /* ---------------- 提问 ---------------- */

  async onSend() {
    const question = (this.data.input || "").trim();
    if (!question || this.data.thinking) return;
    if (question.length > MAX_QUESTION) {
      wx.showToast({ title: `问题请控制在 ${MAX_QUESTION} 字以内`, icon: "none" });
      return;
    }
    this.setData({ input: "" });
    await this._ask(question);
  },

  async _ask(question) {
    this.setData({
      msgs: [...this.data.msgs, { k: this._nextKey(), role: "user", content: question }],
      thinking: true,
    });
    this._scrollBottom();

    try {
      const body = await request("/api/teacher/ask", {
        method: "POST",
        data: {
          question,
          mode: this.data.mode,
          include_official: true,
          history: this._history(question),
        },
      });

      const evidence = body.evidence || [];
      const citations = (body.citations || []).map((c, i) => ({
        k: i,
        ..._resolveCite(c, evidence),
      }));

      // 拒答时后端 answer 为 null，用 refusal_reason 说明"为什么答不了"。
      // ⚠️ 产品口径已于 2026-03-04 变更：**拒答不再是"查不到"的默认出口** ——
      // 查不到会降级为「无依据·通识回答」（`ungrounded`，带显式标注）。
      // 真正的拒答现在只留给两种情况：回答解析失败、或引用无法在材料中定位（疑似编造）。
      // 别再按"宁可拒答"改回去：那会让用户什么都拿不到（见 services/teacher_agent.py 的同类注释）。
      const content = body.refused
        ? body.refusal_reason || "这个问题在官方语料里找不到可引用的依据，暂时不能作答。"
        : body.answer || "";

      await this._pushAnswer({
        role: "ai",
        content,
        citations,
        confidence: body.confidence || "medium",
        refused: !!body.refused,
        // 无据兜底：回答来自模型通识、**没有资料佐证**。带上标记，让卡片与有据回答
        // 明显不同（后端已把 citations 清空、confidence 压到 low）。
        ungrounded: !!body.ungrounded,
        notice: body.notice || "",
        toolCalls: body.tool_calls || 0,
      });
    } catch (e) {
      await this._pushAnswer({
        role: "ai",
        content: (e && e.message) || "请求失败，请稍后重试",
        error: true,
      });
      toastApiError(e);
    } finally {
      if (this._alive) this.setData({ thinking: false });
      this._scrollBottom();
    }
  },

  /* ---------------- 渲染 ---------------- */

  _nextKey() {
    this._seq += 1;
    return this._seq;
  },

  /** AI 气泡：逐字渲染（比整段闪现更接近"正在查资料"的体感）。 */
  _pushAnswer(msg) {
    return new Promise((resolve) => {
      const idx = this.data.msgs.length;
      const full = msg.content || "";
      const typing = !msg.error && !!full;
      this.setData({
        msgs: [...this.data.msgs, { ...msg, k: this._nextKey(), shown: typing ? "" : full, typing, citeOpen: false }],
      });
      this._scrollBottom();

      if (!typing) {
        resolve();
        return;
      }
      let i = 0;
      const timer = setInterval(() => {
        if (!this._alive) {
          clearInterval(timer);
          resolve();
          return;
        }
        i = Math.min(i + 3, full.length);
        this.setData({ [`msgs[${idx}].shown`]: full.slice(0, i) });
        if (i >= full.length) {
          clearInterval(timer);
          this._timers.delete(timer);
          this.setData({ [`msgs[${idx}].typing`]: false });
          this._scrollBottom();
          resolve();
        }
      }, 24);
      this._timers.add(timer);
    });
  },

  _scrollBottom() {
    this._anchorFlip = !this._anchorFlip;
    this.setData({ scrollInto: this._anchorFlip ? "teacher-anchor-a" : "teacher-anchor-b" });
  },
});
