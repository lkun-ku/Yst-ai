import { request } from "../../utils/api.js";

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
};

const MAX_PER_TYPE = 30; // 与后端 doc_max_q_per_task 对齐上限

let timer = null;

Page({
  data: {
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
      status: "pending",
      statusText: STATUS_TEXT.pending,
      questions: [],
      error: "",
      done: 0,
      totalTask: 0,
    });

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
        status: "failed",
        statusText: "提交失败",
        error: (e && e.message) || String(e),
      });
      wx.showToast({ title: "提交失败", icon: "none" });
    }
  },

  _startPoll() {
    this._stopPoll();
    timer = setInterval(() => this._poll(), 2000);
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
    try {
      const st = await request(`/api/kb/task/${id}`, { loading: false, lock: false });
      this.setData({
        status: st.status,
        statusText: STATUS_TEXT[st.status] || st.status,
        done: st.done || 0,
        totalTask: st.total || 0,
        error: st.error || "",
      });

      if (st.status === "done") {
        this._stopPoll();
        await this._loadQuestions();
        this.setData({ generating: false });
        wx.showToast({ title: "出题完成", icon: "success" });
      } else if (st.status === "failed") {
        this._stopPoll();
        this.setData({ generating: false });
        wx.showToast({ title: "出题失败", icon: "none" });
      }
    } catch (e) {
      // 网络抖动：忽略，等待下次轮询
    }
  },

  async _loadQuestions() {
    const id = this.data.taskId;
    const raw = await request(`/api/kb/task/${id}/questions`, { loading: false, lock: false });
    const questions = (raw || []).map((q) => {
      // options / answer 是 JSON 字符串（QuestionOut 用 str 字段），需解析后展示
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

      // source_chunk：工单 17 起为切片 id 清单 JSON；兼容早期的切片文本
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

      return { ...q, options: opts, answerText: (ans || []).join("、"), sourceText };
    });
    this.setData({ questions });
  },
});
