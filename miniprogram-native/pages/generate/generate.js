import { navTo, redirectNav, request, requestDocEvents, toastApiError } from "../../utils/api.js";

/** 过程事件类型到图标的映射（#26 第二批，与 kb 页一致） */
const EVENT_ICON = {
  retrieve: "🔍",
  grade: "⚖️",
  rewrite: "🔁",
  batch: "✏️",
  selfcheck: "🛡",
  question: "📝",
  stage: "⚙️",
  think: "💭", // 模型的构思 / 思考过程（真实内容，逐句展示）
};
const TYPE_MS = 26;

const TYPE_OPTIONS = [
  { type: "single", name: "单选题" },
  { type: "multiple", name: "多选题" },
  { type: "judge", name: "判断题" },
  { type: "blank", name: "填空题" },
  { type: "short", name: "简答题" },
];
const TYPE_NAME = TYPE_OPTIONS.reduce((m, t) => ((m[t.type] = t.name), m), {});
const MAX_TOTAL = 30; // 成本硬约束：单次题量上限
const POLL_MS = 1500;

Page({
  data: {
    docId: 0,
    doc: null,
    mode: "paper", // paper=整卷（章节配额） / spot=定点（Top-K）
    spec: [{ type: "single", name: "单选题", count: 10 }],
    difficulty: "medium",
    focus: "",
    selected: [], // 已勾选章节（scope）
    remainTypes: [], // 还可添加的题型
    total: 10,
    canSubmit: true,

    // 任务态
    taskId: 0,
    status: "", // running / done / failed
    done: 0,
    taskTotal: 0,
    error: "",
    questionCount: 0,
    stageText: "",

    // 过程时间线（#26 第二批）
    phases: [],
    since: 0,
    eventsDowngraded: false,
  },

  _timer: null,

  onLoad(options) {
    const id = Number(options.doc_id || 0);
    if (!id) {
      wx.showToast({ title: "缺少资料", icon: "none" });
      return;
    }
    this.setData({ docId: id });
    this._queue = []; // 待打字机渲染的事件队列（不进 data，避免 setData 开销）
    this._typing = false;
    this.loadDoc(id);
  },

  onUnload() {
    this._stopPoll(); // 页面离开必须清除定时器，避免后台空转
  },

  async loadDoc(id) {
    try {
      const doc = await request(`/api/documents/${id}`);
      this.setData({ doc, selected: doc.headings || [] });
      this._refresh();
    } catch (e) {
      toastApiError(e);
    }
  },

  /* ---------------- 配比编辑 ---------------- */

  _refresh() {
    const spec = this.data.spec;
    const total = spec.reduce((s, i) => s + i.count, 0);
    const used = spec.map((i) => i.type);
    const remainTypes = TYPE_OPTIONS.filter((t) => used.indexOf(t.type) < 0);
    this.setData({
      total,
      remainTypes,
      canSubmit: total > 0 && total <= MAX_TOTAL && spec.length > 0,
    });
  },

  onStep(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const delta = Number(e.currentTarget.dataset.delta);
    const spec = this.data.spec.map((i) => ({ ...i }));
    if (!spec[idx]) return;
    const next = spec[idx].count + delta;
    if (next < 1) return;
    if (this.data.total + delta > MAX_TOTAL) {
      wx.showToast({ title: `单次最多 ${MAX_TOTAL} 题`, icon: "none" });
      return;
    }
    spec[idx].count = next;
    this.setData({ spec }, () => this._refresh());
  },

  onRemoveType(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const spec = this.data.spec.filter((_, i) => i !== idx);
    this.setData({ spec }, () => this._refresh());
  },

  onAddType() {
    const remain = this.data.remainTypes;
    if (!remain.length) {
      wx.showToast({ title: "题型已全部添加", icon: "none" });
      return;
    }
    wx.showActionSheet({
      itemList: remain.map((t) => t.name),
      success: (r) => {
        const t = remain[r.tapIndex];
        if (!t) return;
        const spec = this.data.spec.concat([{ type: t.type, name: t.name, count: 5 }]);
        this.setData({ spec }, () => this._refresh());
      },
      fail: () => {},
    });
  },

  onModeChange(e) {
    this.setData({ mode: e.currentTarget.dataset.mode });
  },

  onDifficulty(e) {
    this.setData({ difficulty: e.currentTarget.dataset.v });
  },

  onFocusInput(e) {
    this.setData({ focus: e.detail.value });
  },

  onToggleHeading(e) {
    const h = e.currentTarget.dataset.h;
    const sel = this.data.selected.slice();
    const i = sel.indexOf(h);
    if (i >= 0) sel.splice(i, 1);
    else sel.push(h);
    this.setData({ selected: sel });
  },

  /* ---------------- 提交与进度 ---------------- */

  async onSubmit() {
    if (!this.data.canSubmit) return;
    const spec = this.data.spec.map((i) => ({ type: i.type, count: i.count }));
    const body = {
      mode: this.data.mode,
      spec,
      difficulty: this.data.difficulty,
      scope: this.data.mode === "paper" ? this.data.selected : undefined,
      focus: this.data.focus || undefined,
    };
    try {
      const r = await request(`/api/documents/${this.data.docId}/generate`, {
        method: "POST",
        data: body,
      });
      this.setData({
        taskId: r.task_id,
        taskTotal: r.total,
        status: "running",
        done: 0,
        stageText: "正在生成…",
        phases: [],
        since: 0,
        eventsDowngraded: false,
      });
      this._queue = [];
      this._typing = false;
      this._drawRing(0);
      this._startPoll();
    } catch (e) {
      toastApiError(e);
    }
  },

  _startPoll() {
    this._stopPoll();
    this._timer = setInterval(() => this._poll(), POLL_MS);
    this._poll();
  },

  _stopPoll() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  },

  /** 增量拉取过程事件；接口不可用时静默降级（保留原进度轮询） */
  async _pollEvents() {
    try {
      const r = await requestDocEvents(this.data.taskId, this.data.since);
      if (r.events && r.events.length) {
        this._enqueue(r.events);
        this.setData({ since: r.latest_seq });
        this._tick();
      }
    } catch (e) {
      if (!this.data.eventsDowngraded) this.setData({ eventsDowngraded: true });
    }
  },

  _enqueue(events) {
    const phases = this.data.phases.concat(
      events.map((e) => ({ seq: e.seq, icon: EVENT_ICON[e.type] || "•", text: "", done: false }))
    );
    this.setData({ phases });
    this._queue = (this._queue || []).concat(events);
  },

  /** 打字机逐字渲染：流式感由前端补，与传输粒度解耦 */
  _tick() {
    if (this._typing) return;
    const ev = (this._queue || []).shift();
    if (!ev) return;
    this._typing = true;
    const full = ev.text || "";
    let i = 0;
    const step = () => {
      i += 2;
      const arr = this.data.phases.slice();
      const idx = arr.findIndex((p) => p.seq === ev.seq);
      if (idx >= 0) {
        arr[idx].text = full.slice(0, i);
        arr[idx].done = i >= full.length;
      }
      this.setData({ phases: arr });
      if (i >= full.length) {
        this._typing = false;
        this._tick();
      } else {
        setTimeout(step, TYPE_MS);
      }
    };
    setTimeout(step, TYPE_MS);
  },

  async _poll() {
    if (!this.data.taskId) return;
    await this._pollEvents(); // 过程时间线优先；失败自动降级为纯进度
    try {
      const t = await request(`/api/tasks/${this.data.taskId}`, { loading: false, lock: false });
      const pct = t.total ? Math.round((t.done / t.total) * 100) : 0;
      this.setData({
        status: t.status,
        done: t.done,
        taskTotal: t.total,
        questionCount: t.question_count,
        error: t.error || "",
        stageText: t.status === "running" ? `生成中 ${t.done}/${t.total}` : "",
      });
      this._drawRing(pct);

      if (t.status === "done" || t.status === "failed") this._stopPoll();
    } catch (e) {
      /* 轮询失败不打断，下轮继续 */
    }
  },

  /** 环形进度：canvas 2d 绘制，失败也不影响文字进度显示 */
  _drawRing(pct) {
    const q = wx.createSelectorQuery().in(this);
    q.select("#ring")
      .fields({ node: true, size: true })
      .exec((res) => {
        try {
          const item = res && res[0];
          if (!item || !item.node) return;
          const canvas = item.node;
          const ctx = canvas.getContext("2d");
          const info = wx.getWindowInfo ? wx.getWindowInfo() : { pixelRatio: 2 };
          const dpr = info.pixelRatio || 2;
          const w = item.width || 160;
          const h = item.height || 160;
          canvas.width = w * dpr;
          canvas.height = h * dpr;
          ctx.scale(dpr, dpr);
          ctx.clearRect(0, 0, w, h);

          const cx = w / 2;
          const cy = h / 2;
          const r = Math.min(w, h) / 2 - 8;

          ctx.beginPath();
          ctx.arc(cx, cy, r, 0, Math.PI * 2);
          ctx.strokeStyle = "#e8eef2";
          ctx.lineWidth = 8;
          ctx.stroke();

          const end = -Math.PI / 2 + (Math.PI * 2 * Math.min(100, Math.max(0, pct))) / 100;
          ctx.beginPath();
          ctx.arc(cx, cy, r, -Math.PI / 2, end);
          ctx.strokeStyle = "#12b3a8";
          ctx.lineWidth = 8;
          ctx.lineCap = "round";
          ctx.stroke();
        } catch (e) {
          /* 绘制失败忽略，文字进度仍在 */
        }
      });
  },

  /** 用生成的题目发起个人题库闯关 */
  async onStartQuest() {
    // 用 taskTotal（生成任务的目标题数）而不是 questionCount（实际落库数）：
    // 后端会自动按可用题量截断（min(want, len(pool))），保证"想要多少就请求多少"。
    const want = this.data.taskTotal || 10;
    if (want <= 0) {
      wx.showToast({ title: "没有可用的题目", icon: "none" });
      return;
    }
    try {
      const r = await request("/api/sessions/start", {
        method: "POST",
        data: { doc_id: this.data.docId, question_count: want },
      });
      getApp().setSession(r);
      redirectNav("/pages/answer/answer");
    } catch (e) {
      toastApiError(e);
    }
  },

  onRetry() {
    this.setData({ taskId: 0, status: "", error: "" });
  },

  onBackDocs() {
    navTo("/pages/docs/docs");
  },
});
