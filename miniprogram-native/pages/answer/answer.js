import { request, ensureIdentity, toastApiError, redirectNav, onRequesting } from "../../utils/api.js";
import { optionState } from "../../utils/answerState.js";

const DRAFT_KEY = "quest_draft";

Page({
  data: {
    total: 0,
    curIdx: 0,
    answeredCount: 0,
    progressPct: 0,
    allAnswered: false,
    hasPrev: false,
    hasNext: false,

    // 答题卡（#26）：题号网格，支持从任意题直达
    sheetOpen: false,
    sheet: [],
    cur: null, // 当前题视图模型（含 stem/kp/explanation/aigc/isMultiple）
    curKind: "option", // option（单选/多选/判断） / blank（填空） / short（简答）
    curOptions: [], // 当前题选项视图模型（含预计算的四态 class 与对错标记）
    draft: "", // 填空 / 简答的输入草稿
    curRevealed: false,
    curCorrect: false,
    curStateText: "",
    loadError: "",
    loadModule: "",
    requesting: 0,
    isMock: false, // S3 模考：不即时判对错、不可回退、限时
    remainText: "", // 模考倒计时 mm:ss
  },

  // 非渲染态：不进 setData，避免每答一题都把整套题（含长解析）传一遍
  _questions: [],
  _answers: {},
  _revealed: {},
  _curIdx: 0,
  _offRequesting: null,

  onLoad(options) {
    this._offRequesting = onRequesting((n) => this.setData({ requesting: n }));

    // S3 模考：从 globalData 读取服务端下发的限时信息
    const exam = (getApp().globalData && getApp().globalData.exam) || {};
    const isMock = exam.mode === "mock";
    this.setData({ isMock });
    if (isMock) this._startCountdown(exam);

    // 启用系统返回拦截（原生可直接调用，不需要 Taro 的 mini.$scope hack）
    if (typeof wx.enableAlertBeforeUnload === "function") {
      wx.enableAlertBeforeUnload({ message: "当前进度已保存为草稿，确定退出？" });
    }

    const moduleParam = options && options.module ? decodeURIComponent(options.module) : "";
    const s = getApp().getSession();
    let questions = (s && s.questions) || [];

    if (!questions.length) {
      const d = this._readDraft();
      if (d && d.questions && d.questions.length) {
        questions = d.questions;
        this._answers = d.answers || {};
        this._revealed = d.revealed || {};
        if (d.sessionId) getApp().setSession({ session_id: d.sessionId, questions: d.questions });
      }
    }

    if (questions.length) {
      this._questions = questions;
      this._curIdx = this._resolveStartIdx();
      this._render();
      if (s && s.module) wx.setNavigationBarTitle({ title: `闯关 · ${s.module}` });
      return;
    }

    // 兜底：直接带 module 参数进入（如将来的深链）时自行发起
    if (moduleParam) {
      this.setData({ loadModule: moduleParam });
      this._startByModule(moduleParam);
      return;
    }

    wx.showToast({ title: "没有进行中的闯关局", icon: "none" });
    setTimeout(() => wx.redirectTo({ url: "/pages/quest/quest" }), 800);
  },

  onShow() {
    // 从其他页返回时同步已答/已揭示，不动题目本身
    const d = this._readDraft();
    if (d && d.questions && d.questions.length) {
      this._answers = Object.assign({}, d.answers || {});
      this._revealed = Object.assign({}, d.revealed || {});
      if (this._questions.length) this._render();
    }
  },

  onUnload() {
    this._stopCountdown();
    if (this._offRequesting) this._offRequesting();
    if (typeof wx.disableAlertBeforeUnload === "function") wx.disableAlertBeforeUnload();
  },

  /* ---------------- 数据 ---------------- */

  _readDraft() {
    try {
      return wx.getStorageSync(DRAFT_KEY) || null;
    } catch (e) {
      return null;
    }
  },

  _persist() {
    const s = getApp().getSession();
    try {
      wx.setStorageSync(DRAFT_KEY, {
        sessionId: s.sessionId,
        questions: this._questions,
        answers: this._answers,
        revealed: this._revealed,
      });
    } catch (e) {
      /* 存储失败不阻塞答题 */
    }
  },

  _qtype(q) {
    return String((q.type && q.type.value) || q.type || "single");
  },
  _opts(q) {
    try {
      return JSON.parse(q.options);
    } catch (e) {
      return [];
    }
  },
  _isMultiple(q) {
    return q.type === "multiple";
  },
  _correctSet(q) {
    try {
      return new Set(JSON.parse(q.answer));
    } catch (e) {
      return new Set();
    }
  },
  _isCorrect(q) {
    const sel = new Set(this._answers[q.id] || []);
    const cor = this._correctSet(q);
    if (sel.size !== cor.size) return false;
    for (const k of sel) if (!cor.has(k)) return false;
    return true;
  },
  _stateText(q) {
    const qtype = this._qtype(q);
    // 填空/简答：可接受答案可能多个，乱写不应显「部分对」；以提交后后端 _judge 为准（#20）
    if (qtype === "blank" || qtype === "short") {
      return this._isCorrect(q) ? "全对" : "已作答（参考答案见解析）";
    }
    if (this._isCorrect(q)) return "全对";
    const sel = new Set(this._answers[q.id] || []);
    const cor = this._correctSet(q);
    if (cor.size > 1 && sel.size > 0) return sel.size === cor.size ? "全对" : "部分对（漏选/错选）";
    return "错误";
  },
  _resolveStartIdx() {
    const n = this._questions.length;
    const first = this._questions.findIndex((q) => !this._revealed[q.id]);
    return first >= 0 ? first : n - 1;
  },
  _nextIdx() {
    const n = this._questions.length;
    for (let i = this._curIdx + 1; i < n; i++) {
      if (!this._revealed[this._questions[i].id]) return i;
    }
    return this._curIdx + 1 < n ? this._curIdx + 1 : -1;
  },

  /** 只把「当前题」的视图模型送进 setData，切换题目时不必重传整套题。 */
  _render() {
    const qs = this._questions;
    const n = qs.length;
    if (!n) return;
    this._curIdx = Math.min(Math.max(this._curIdx, 0), n - 1);
    const q = qs[this._curIdx];
    const qRevealed = !!this._revealed[q.id];
    const sel = this._answers[q.id] || [];
    const cor = this._correctSet(q);

    const isMock = this.data.isMock;
    const curOptions = this._opts(q).map((o) => {
      // 模考：不揭示对错，但必须显式标记"已选"——否则用户点了却看不到选中，
      // 会以为没点上（optionState 在 revealed=false 时一律返回 idle，无法复用）。
      if (isMock && !qRevealed) {
        const picked = sel.indexOf(o.key) >= 0;
        return {
          key: o.key,
          text: o.text,
          cls: picked ? "opt-picked" : "",
          keyCls: picked ? "key-picked" : "",
          mark: "",
        };
      }
      const st = optionState({ revealed: qRevealed, selected: sel, correct: [...cor], key: o.key });
      let mark = "";
      if (qRevealed && cor.has(o.key)) mark = "ok";
      else if (qRevealed && sel.indexOf(o.key) >= 0) mark = "wrong";
      return {
        key: o.key,
        text: o.text,
        cls: st === "idle" ? "" : `opt-${st}`,
        keyCls: st === "idle" ? "" : `key-${st}`,
        mark,
      };
    });

    const answeredCount = qs.filter((x) => this._revealed[x.id]).length;

    // 答题卡：cur=当前 / done=已作答 / todo=未作答
    const sheet = qs.map((x, i) => ({
      i,
      n: i + 1,
      state: i === this._curIdx ? "cur" : this._revealed[x.id] ? "done" : "todo",
    }));

    const qtype = this._qtype(q);

    this.setData({
      curKind: qtype === "blank" ? "blank" : qtype === "short" ? "short" : "option",
      draft: (sel && sel[0]) || "",
      cur: {
        id: q.id,
        stem: q.stem,
        kp: q.knowledge_point,
        explanation: q.explanation || "",
        aigc: !!q.aigc_flag,
        isMultiple: this._isMultiple(q),
      },
      curOptions,
      curRevealed: qRevealed,
      curCorrect: this._isCorrect(q),
      curStateText: this._stateText(q),
      curIdx: this._curIdx,
      total: n,
      answeredCount,
      sheet,
      progressPct: Math.round((answeredCount / n) * 100),
      allAnswered: qs.every((x) => this._revealed[x.id]),
      hasPrev: this._curIdx > 0,
      hasNext: this._nextIdx() >= 0,
      loadError: "",
    });
    this._persist();
  },

  /* ---------------- 交互 ---------------- */

  /** 选项点击：原生 bindtap 直接派发（删除了 Taro 版 pickUrl/applyPick/redirect 重载） */
  onPick(e) {
    const key = e.currentTarget.dataset.key;
    const q = this._questions[this._curIdx];
    if (!q) return;

    // 模考：只记录作答，不即时揭示对错（统一交卷后才看结果）
    if (this.data.isMock) {
      if (this._isMultiple(q)) {
        const cur = this._answers[q.id] || [];
        this._answers[q.id] = cur.indexOf(key) >= 0 ? cur.filter((k) => k !== key) : cur.concat([key]);
      } else {
        this._answers[q.id] = [key];
      }
      this._render();
      return;
    }

    if (this._revealed[q.id]) return; // 选中即揭示，一次性判定不再改
    if (this._isMultiple(q)) {
      const cur = this._answers[q.id] || [];
      this._answers[q.id] = cur.indexOf(key) >= 0 ? cur.filter((k) => k !== key) : cur.concat([key]);
    } else {
      this._answers[q.id] = [key];
    }
    this._revealed[q.id] = true; // 即时反馈（Implementation 23）
    this._render();
  },

  /** 填空 / 简答的输入草稿 */
  onDraftInput(e) {
    this.setData({ draft: e.detail.value });
  },

  /** 填空 / 简答没有选项，需显式提交后才揭示解析（简答由后端标记 self_review，不自动判分） */
  onConfirmAnswer() {
    const q = this._questions[this._curIdx];
    if (!q || this._revealed[q.id]) return;
    const text = (this.data.draft || "").trim();
    if (!text) {
      wx.showToast({ title: "请先作答", icon: "none" });
      return;
    }
    this._answers[q.id] = [text];
    this._revealed[q.id] = true;
    this._render();
  },

  onPrev() {
    // 模考允许回看/修改已答题（核心是限时 + 统一交卷，不是禁止导航）
    if (this._curIdx > 0) {
      this._curIdx -= 1;
      this._render();
    }
  },

  /* ---------------- S3 模考倒计时 ---------------- */

  _startCountdown(exam) {
    // 用服务端时间校正本地时钟：offset = 服务端 - 本地，之后所有计算都加上它
    const serverNow = exam.serverNow ? Date.parse(exam.serverNow) : Date.now();
    this._serverOffset = serverNow - Date.now();
    this._examDeadline = exam.deadlineAt ? Date.parse(exam.deadlineAt) : 0;
    this._tickCountdown();
    this._stopCountdown();
    this._countdownTimer = setInterval(() => this._tickCountdown(), 1000);
  },

  _tickCountdown() {
    if (!this._examDeadline) return;
    const remainMs = this._examDeadline - (Date.now() + (this._serverOffset || 0));
    if (remainMs <= 0) {
      this.setData({ remainText: "00:00" });
      this._stopCountdown();
      wx.showToast({ title: "时间到，自动交卷", icon: "none" });
      this._doSubmit(); // 超时自动交卷（服务端也会判定 timeout）
      return;
    }
    const totalSec = Math.floor(remainMs / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    this.setData({ remainText: `${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}` });
  },

  _stopCountdown() {
    if (this._countdownTimer) {
      clearInterval(this._countdownTimer);
      this._countdownTimer = null;
    }
  },

  onNext() {
    const ni = this._nextIdx();
    if (ni >= 0) {
      this._curIdx = ni;
      this._render();
    }
  },

  /** 答题卡展开 / 收起（#26） */
  onToggleSheet() {
    this.setData({ sheetOpen: !this.data.sheetOpen });
  },

  /** 从答题卡直达任意题（#26）：选完即收起弹层 */
  goTo(e) {
    const i = Number(e.currentTarget.dataset.i);
    if (Number.isNaN(i) || i < 0 || i >= this._questions.length) return;
    this._curIdx = i;
    this.setData({ sheetOpen: false });
    this._render();
  },

  /** 阻止弹层面板点击冒泡到遮罩 */
  noop() {},

  /**
   * 重练当前题的考点（#27 方案 A）：弹窗警示后发起同考点新局。
   * 用 redirectTo 替换当前页——当前局作废（globalData.session 被新局覆盖），
   * 返回键不会回到已失效的旧局。
   */
  onRepracticeCurrent() {
    const q = this._questions && this._questions[this._curIdx];
    if (!q || !q.kp) {
      wx.showToast({ title: "该题暂无考点信息", icon: "none" });
      return;
    }
    wx.showModal({
      title: "重练此题考点？",
      content: `将离开本次闯关去重练《${q.kp}》，当前答题进度不会保存。`,
      confirmText: "去重练",
      cancelText: "留下",
      success: async (r) => {
        if (!r.confirm) return;
        try {
          const count = 3;
          const data = await request("/api/sessions/start", {
            method: "POST",
            data: { knowledge_point: q.kp, question_count: count },
          });
          if ((data.questions || []).length < count) {
            wx.showToast({
              title: `可用题目不足，已按 ${data.question_count} 题开局`,
              icon: "none",
              duration: 2200,
            });
          }
          getApp().setSession(data);
          redirectNav("/pages/answer/answer");
        } catch (e) {
          toastApiError(e);
        }
      },
    });
  },

  /** 允许部分提交（#26）：未答完时二次确认，未作答的不计入完成题数 */
  onSubmitPartial() {
    const unanswered = this._questions.filter((q) => !this._revealed[q.id]).length;
    if (!unanswered) return this.onSubmit();
    wx.showModal({
      title: "未答完，确定交卷？",
      content: `还有 ${unanswered} 题未作答；未作答的不计入完成题数，交卷后不可修改。`,
      confirmText: "继续交卷",
      cancelText: "继续答题",
      success: (r) => {
        if (r.confirm) this.onSubmit();
      },
    });
  },

  /** 交卷入口：模考允许随时交卷（二次确认），普通闯关需答完所有题 */
  onSubmit() {
    if (this.data.isMock) {
      const unanswered = this._questions.filter((q) => !(this._answers[q.id] || []).length).length;
      wx.showModal({
        title: "交卷？",
        content: unanswered > 0 ? `还有 ${unanswered} 题未作答，交卷后不可修改。` : "交卷后不可修改，确定交卷吗？",
        confirmText: "交卷",
        success: (r) => {
          if (r.confirm) this._doSubmit();
        },
      });
      return;
    }
    this._doSubmit();
  },

  /** 实际提交：本页按钮直接触发（Taro 版因点击失效被前移到 review 的 onLoad，此处恢复） */
  async _doSubmit() {
    this._stopCountdown();
    const s = getApp().getSession();
    const payload = {
      session_id: s.sessionId,
      answers: this._questions.map((q) => ({ question_id: q.id, selected: this._answers[q.id] || [] })),
    };
    try {
      const data = await request("/api/sessions/submit", { method: "POST", data: payload });
      const right = ((data && data.results) || []).filter((r) => r.is_correct).length;
      try {
        wx.removeStorageSync(DRAFT_KEY);
      } catch (e) {
        /* 清草稿失败不影响交卷结果 */
      }
      wx.showToast({ title: `答对 ${right}/${this._questions.length}`, icon: "none" });
      redirectNav(`/pages/review/review?session_id=${s.sessionId}`);
    } catch (e) {
      toastApiError(e); // 去重锁释放后用户可重试
    }
  },

  async onReport() {
    const q = this._questions[this._curIdx];
    if (!q) return;
    try {
      await request("/api/reports", {
        method: "POST",
        data: { question_id: q.id, error_type: "explanation", detail: `题目《${q.stem}》疑似有误，请审校。` },
      });
      wx.showToast({ title: "已提交纠错，感谢反馈", icon: "success" });
    } catch (e) {
      toastApiError(e);
    }
  },

  confirmExit() {
    const unanswered = this._questions.filter((q) => !(this._answers[q.id] || []).length).length;
    const tip = unanswered > 0 ? `还有 ${unanswered} 题未作答，退出后进度已自动保存为草稿。` : "进度已自动保存为草稿。";
    wx.showModal({
      title: "退出闯关？",
      content: `${tip}确定退出吗？`,
      confirmText: "退出",
      cancelText: "继续答题",
      confirmColor: "#e54d42",
      success: (r) => {
        if (r.confirm) wx.navigateBack();
      },
    });
  },

  /** 兜底：直接带 module 进入时自行发起闯关（正常路径由 quest 页发起） */
  async _startByModule(module) {
    try {
      await ensureIdentity();
      const data = await request("/api/sessions/start", {
        method: "POST",
        data: { module, question_count: 10 },
      });
      getApp().setSession(data);
      this._questions = data.questions || [];
      this._answers = {};
      this._revealed = {};
      this._curIdx = 0;
      this._render();
      wx.setNavigationBarTitle({ title: `闯关 · ${module}` });
    } catch (e) {
      const msg = (e && e.message) || "出题失败";
      this.setData({
        loadError: /timeout|request:fail|超时|失败|网络/.test(msg)
          ? "手机可能连不到 dev 机后端，请确认与电脑在同一 WiFi"
          : `出题失败：${msg}`,
      });
      toastApiError(e);
    }
  },

  onRetry() {
    if (this.data.loadModule) this._startByModule(this.data.loadModule);
  },

  goQuest() {
    wx.redirectTo({ url: "/pages/quest/quest" });
  },
});
