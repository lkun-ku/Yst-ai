import { cancelTask, request, requestChunk, requestEvents, retryTask } from "../../utils/api.js";
import { TIMELINE_ICONS } from "../../utils/timeline_icons.js";

/** 题型定义：用户自主决定各题型数量（为 0 的题型不出）。 */
const SPEC_DEF = [
  { type: "single", label: "单选题", count: 3 },
  { type: "multiple", label: "多选题", count: 0 },
  { type: "judge", label: "判断题", count: 0 },
  { type: "blank", label: "填空题", count: 0 },
  { type: "short", label: "简答题", count: 0 },
];

const STATUS_TEXT = {
  pending: "排队中",
  running: "生成中",
  done: "已完成",
  failed: "失败",
  cancelled: "已停止",
};

/** #25：事件类型 → 时间线图标（纸卷 v1：Reicon 线性 data-URI，替代 emoji） */
const TYPE_ICON = {
  ...TIMELINE_ICONS,
  cancelled: TIMELINE_ICONS.failed,
};

const MAX_PER_TYPE = 100; // 与后端 doc_max_q_per_task 对齐上限（#23）
const POLL_MS = 2000; // 增量事件轮询间隔（阶段耗时 10~20s，2s 粒度无感）
const TYPE_MS = 26; // 打字机间隔

let timer = null;

Page({
  data: {
    // 表单（stage=form 时可见）
    title: "",
    scope: "",
    focus: "",
    difficulty: "medium",
    difficulties: [
      { key: "easy", label: "简单" },
      { key: "medium", label: "中等" },
      { key: "hard", label: "较难" },
    ],
    spec: SPEC_DEF.map((x) => ({ ...x })),
    total: 3,

    // #25 过程区（stage=running 时全屏置顶）
    stage: "form", // form | running
    scopeText: "", // 任务目标常驻（对应 WorkBuddy 顶部标题）
    phases: [], // 时间线：{seq, icon, type, text, detail, expanded, done}
    currentPhase: "", // 底部状态行
    since: 0, // 增量游标
    stopped: false, // 是否由用户主动停止
    eventsDowngraded: false, // 事件接口不可用时降级为 done/total

    generating: false,
    status: "",
    statusText: "",
    done: 0,
    totalTask: 0,
    questions: [],
    taskId: "",
    error: "",
  },

  onLoad() {
    this._syncTotal();
    this._queue = []; // 待打字机渲染的事件队列（不进 data，避免 setData 开销）
    this._typing = false;
  },

  onUnload() {
    this._stopPoll();
  },

  onHide() {
    this._stopPoll();
  },

  _syncTotal() {
    const total = this.data.spec.reduce((s, x) => s + x.count, 0);
    this.setData({ total });
  },

  onTitle(e) {
    this.setData({ title: e.detail.value });
  },
  onScope(e) {
    this.setData({ scope: e.detail.value });
  },
  onFocus(e) {
    this.setData({ focus: e.detail.value });
  },
  onDifficulty(e) {
    this.setData({ difficulty: e.currentTarget.dataset.v });
  },
  onPlus(e) {
    const t = e.currentTarget.dataset.type;
    const spec = this.data.spec.map((x) =>
      x.type === t ? { ...x, count: Math.min(MAX_PER_TYPE, x.count + 1) } : x
    );
    this.setData({ spec }, () => this._syncTotal());
  },
  onMinus(e) {
    const t = e.currentTarget.dataset.type;
    const spec = this.data.spec.map((x) =>
      x.type === t ? { ...x, count: Math.max(0, x.count - 1) } : x
    );
    this.setData({ spec }, () => this._syncTotal());
  },
  goDocs() {
    wx.navigateTo({ url: "/pages/docs/docs" });
  },

  async onGenerate() {
    if (this.data.generating) return;

    const scope = (this.data.scope || "").trim();
    if (!scope) {
      wx.showToast({ title: "请填写出题范围", icon: "none" });
      return;
    }
    const spec = this.data.spec
      .filter((x) => x.count > 0)
      .map((x) => ({ type: x.type, count: x.count }));
    if (!spec.length) {
      wx.showToast({ title: "请至少选择一种题型", icon: "none" });
      return;
    }

    this.setData({
      generating: true,
      // #25：提交后折叠表单，过程区全屏置顶
      stage: "running",
      scopeText: scope,
      phases: [],
      currentPhase: "准备中…",
      since: 0,
      stopped: false,
      eventsDowngraded: false,
      questions: [],
      error: "",
      done: 0,
      totalTask: 0,
    });
    this._queue = [];

    try {
      const r = await request("/api/kb/generate", {
        method: "POST",
        data: {
          scope,
          spec,
          difficulty: this.data.difficulty,
          focus: (this.data.focus || "").trim() || undefined,
          route: "graph", // 路线③ LangGraph（生产默认）
        },
      });
      this.setData({ taskId: r.task_id });
      this._startPoll();
    } catch (e) {
      this.setData({
        generating: false,
        stage: "form", // 提交失败即回表单，配置仍在
        error: (e && e.message) || String(e),
      });
      wx.showToast({ title: "提交失败", icon: "none" });
    }
  },

  /* ---------------- #25 过程区 ---------------- */

  _startPoll() {
    this._stopPoll();
    timer = setInterval(() => this._poll(), POLL_MS);
    this._poll();
  },

  _stopPoll() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  },

  async _poll() {
    const id = this.data.taskId;
    if (!id) return;

    // 优先消费增量事件；事件接口不可用则降级为原有 done/total 轮询
    try {
      const r = await requestEvents(id, this.data.since);
      this.setData({ status: r.status, statusText: STATUS_TEXT[r.status] || r.status });
      if (r.events && r.events.length) {
        this._enqueue(r.events);
        this.setData({ since: r.latest_seq });
        this._tick();
      }
      if (r.status === "done" || r.status === "failed" || r.status === "cancelled") {
        this._stopPoll();
        this._finish(r.status, r.status === "cancelled");
        return;
      }
    } catch (e) {
      await this._fallbackPoll();
    }
  },

  /** 降级：事件接口异常时退回原有任务状态轮询，功能无损 */
  async _fallbackPoll() {
    const id = this.data.taskId;
    if (!id) return;
    try {
      const st = await request(`/api/kb/task/${id}`, { loading: false, lock: false });
      this.setData({
        status: st.status,
        statusText: STATUS_TEXT[st.status] || st.status,
        done: st.done || 0,
        totalTask: st.total || 0,
        error: st.error || "",
        eventsDowngraded: true,
        currentPhase: `已出 ${st.done || 0}/${st.total || 0} 题`,
      });
      if (st.status === "done" || st.status === "failed" || st.status === "cancelled") {
        this._stopPoll();
        this._finish(st.status, st.status === "cancelled");
      }
    } catch (e) {
      // 网络抖动：忽略，等待下次轮询
    }
  },

  _enqueue(events) {
    for (const ev of events) {
      this._queue.push(ev);
      if (ev.type === "question") {
        // 题卡渐现：先用事件里的题干预览占位，完成后由全量校准替换
        let d = null;
        try {
          d = ev.detail ? JSON.parse(ev.detail) : null;
        } catch (e) {
          d = null;
        }
        if (d && d.id) {
          const questions = this.data.questions.concat([
            { id: d.id, stem: d.stem || "（生成中…）", type: d.type, options: [], answerText: "", preview: true },
          ]);
          this.setData({ questions });
        }
      }
    }
    // 状态行跟随最新事件
    const last = events[events.length - 1];
    if (last) this.setData({ currentPhase: last.text });
  },

  /** 打字机：逐条逐字渲染时间线（流式感由前端补，与传输粒度解耦） */
  _tick() {
    if (this._typing) return;
    const ev = this._queue.shift();
    if (!ev) return;
    this._typing = true;

    // wxml 无法解析 JSON：retrieve 事件的切片列表在此展开为 _list 供渲染
    let list = [];
    if (ev.type === "retrieve" && ev.detail) {
      try {
        list = JSON.parse(ev.detail) || [];
      } catch (err) {
        list = [];
      }
    }

    const phases = this.data.phases.concat([
      {
        seq: ev.seq,
        icon: TYPE_ICON[ev.type] || "•",
        type: ev.type,
        text: "",
        detail: ev.detail,
        _list: list,
        expanded: false,
        done: false,
      },
    ]);
    this.setData({ phases });

    const full = ev.text || "";
    let i = 0;
    const step = () => {
      i += 2;
      const arr = this.data.phases.slice();
      const last = arr[arr.length - 1];
      if (last) {
        last.text = full.slice(0, i);
        last.done = i >= full.length;
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

  /** 展开 / 折叠动作卡片；展开切片时按需拉全文（D1） */
  async onTogglePhase(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const phases = this.data.phases.slice();
    const p = phases[idx];
    if (!p) return;
    p.expanded = !p.expanded;
    this.setData({ phases });
    if (!p.expanded || p.type !== "retrieve" || p._full) return;

    let list = [];
    try {
      list = JSON.parse(p.detail || "[]");
    } catch (err) {
      list = [];
    }
    if (!list.length) return;
    // 只拉首个切片全文即可满足「看看题出自哪段资料」的诉求，避免一次拉一堆
    try {
      const ch = await requestChunk(list[0].id);
      const arr = this.data.phases.slice();
      if (arr[idx]) {
        arr[idx]._full = ch.content || "";
        arr[idx]._fullHeading = ch.heading_path || "";
      }
      this.setData({ phases: arr });
    } catch (err) {
      /* 拉取失败保持摘要，不打扰用户 */
    }
  },

  /** 停止生成：协作式取消，已出题保留为部分卷，随后回填配置 */
  async onStop() {
    const id = this.data.taskId;
    if (!id) return;
    wx.showModal({
      title: "停止生成？",
      content: "已生成的题目会保留，未完成的将停止。",
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await cancelTask(id);
        } catch (e) {
          /* 取消失败也继续停止轮询 */
        }
        this._stopPoll();
        // 稍等服务端落到终态，再取一次部分卷
        setTimeout(() => this._finish("cancelled", true), 1500);
      },
    });
  },

  /** 回到表单：配置回填，可一键改完重新出题（D6 + 补充决策） */
  onBackToForm() {
    this._stopPoll();
    this.setData({ stage: "form", generating: false });
  },

  /** 失败后仅重试缺口 */
  async onRetry() {
    const id = this.data.taskId;
    if (!id) return;
    try {
      const r = await retryTask(id);
      this.setData({
        taskId: r.task_id,
        since: 0,
        generating: true,
        stage: "running",
        phases: [],
        currentPhase: "重试缺口中…",
        error: "",
      });
      this._queue = [];
      this._startPoll();
    } catch (e) {
      wx.showToast({ title: (e && e.message) || "重试失败", icon: "none" });
    }
  },

  async _finish(status, stopped) {
    this.setData({ status, statusText: STATUS_TEXT[status] || status, stopped: !!stopped });
    try {
      await this._loadQuestions();
    } catch (e) {
      /* 校准失败保留已渐现的预览卡 */
    }
    this.setData({ generating: false });
    if (status === "done") {
      wx.showToast({ title: "出题完成", icon: "success" });
    } else if (status === "failed") {
      wx.showToast({ title: "出题失败", icon: "none" });
    } else if (status === "cancelled") {
      wx.showToast({ title: "已停止生成", icon: "none" });
    }
  },

  async _loadQuestions() {
    const id = this.data.taskId;
    const raw = await request(`/api/kb/task/${id}/questions`, { loading: false, lock: false });
    const questions = (raw || []).map((q) => {
      let opts = [];
      try {
        opts = typeof q.options === "string" ? JSON.parse(q.options) : q.options || [];
      } catch (e) {
        opts = [];
      }
      let ans = [];
      try {
        ans = typeof q.answer === "string" ? JSON.parse(q.answer) : q.answer || [];
      } catch (e) {
        ans = [];
      }

      let sourceText = "";
      if (q.source_chunk) {
        try {
          const ids = JSON.parse(q.source_chunk);
          sourceText = Array.isArray(ids)
            ? "切片 " + ids.map((i) => `#${i}`).join("、")
            : String(q.source_chunk);
        } catch (e) {
          sourceText = String(q.source_chunk).slice(0, 60);
        }
      }

      return { ...q, options: opts, answerText: (ans || []).join("、"), sourceText, preview: false };
    });
    this.setData({ questions });
  },
});
