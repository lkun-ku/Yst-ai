import { request, toastApiError } from "../../utils/api.js";

/**
 * 主观题作答与批改：**RAG for Evaluation**（三种 RAG 形态里最少见的一种）。
 *
 * ## 两个必须写在代码里的口径
 *
 * 1. **题目由用户录入**，不是从题库抽的 —— 后端没有「按题型列题」的接口，
 *    而 `MarkIn` 本来就要求 `stem`。所以这里的折叠区是「把题目/材料粘进来」。
 *    （从题库抽主观题需要后端新增列表接口，已登记为未完成项。）
 *
 * 2. **不显示官方单题分值以外的数字**。材料分析（14）与写作（50）的分值口径明确；
 *    简答/辨析/教学设计的分值本仓未核实，所以显示「—」而不是猜一个。
 *    本产品自定的四维度是**百分制**，与卷面分不是一回事，界面上分开写。
 */
const QTYPES = [
  { key: "material", name: "材料分析", score: "14 分/题" },
  { key: "writing", name: "写作", score: "50 分" },
  { key: "short", name: "简答", score: "—" },
  { key: "design", name: "教学设计", score: "—" },
  { key: "default", name: "其它主观题", score: "—" },
];

/** 与后端 `routers/marking.py` 对齐 */
const MAX_ANSWER = 2000;
const MAX_STEM = 2000;

/** 提交态文案：顺序与后端实际动作一致（先检索依据 → 再批改 → 再校验引用） */
const STAGES = ["检索评分标准", "分项批改", "校验引用"];

/** 维度键 → 中文名。与后端 `marking.DIMENSION_LABELS` 保持一致。 */
const DIM_LABELS = {
  relevance: "切题度",
  evidence: "论据",
  structure: "结构",
  language: "语言",
};

/** 分段渲染的间隔：先给分、再给评语、最后展开依据（一次性铺开会把首屏淹没）。 */
const REVEAL_COMMENTS_MS = 220;
const REVEAL_EVIDENCE_MS = 520;

function _fmtElapsed(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

Page({
  data: {
    QTYPES,
    STAGES,
    qtype: "material",
    qtypeScore: QTYPES[0].score,
    stem: "",
    stemPreview: "",
    stemOpen: true,
    answer: "",
    canSubmit: false,
    loading: false,
    stageIdx: -1,
    result: null,
    dims: [],
    consistDims: [],
    consistency: null,
    revealComments: false,
    revealEvidence: false,
    rubricOpen: false,
    elapsedText: "0:00",
  },

  onLoad() {
    this._timer = null;
    this._elapsed = 0;
    this._stages = []; // 阶段推进的 setTimeout 句柄，卸载时必须清掉
    this._reveals = [];
    this._startTimer();
  },

  onUnload() {
    this._clearTimers();
  },

  onHide() {
    // 切后台停表：计时是"这道题写了多久"，不是挂机时长
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  },

  onShow() {
    if (!this._timer && !this.data.result) this._startTimer();
  },

  _startTimer() {
    this._timer = setInterval(() => {
      this._elapsed += 1;
      this.setData({ elapsedText: _fmtElapsed(this._elapsed) });
    }, 1000);
  },

  _clearTimers() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    this._stages.forEach((t) => clearTimeout(t));
    this._reveals.forEach((t) => clearTimeout(t));
    this._stages = [];
    this._reveals = [];
  },

  /* ---------------- 表单 ---------------- */

  onPickType(e) {
    const key = e.currentTarget.dataset.key;
    const picked = QTYPES.find((t) => t.key === key);
    if (!picked) return;
    this.setData({ qtype: picked.key, qtypeScore: picked.score });
  },

  onToggleStem() {
    this.setData({ stemOpen: !this.data.stemOpen });
  },

  onStemInput(e) {
    const stem = e.detail.value || "";
    this.setData({
      stem,
      stemPreview: stem.length > 80 ? `${stem.slice(0, 80)}…` : stem,
      canSubmit: this._canSubmit(stem, this.data.answer),
    });
  },

  onAnswerInput(e) {
    const answer = e.detail.value || "";
    this.setData({ answer, canSubmit: this._canSubmit(this.data.stem, answer) });
  },

  _canSubmit(stem, answer) {
    return !!(stem || "").trim() && !!(answer || "").trim() && !this.data.loading;
  },

  /* ---------------- 提交批改 ---------------- */

  async onSubmit() {
    const stem = (this.data.stem || "").trim();
    const answer = (this.data.answer || "").trim();
    if (!stem) {
      wx.showToast({ title: "请先录入题目或材料", icon: "none" });
      this.setData({ stemOpen: true });
      return;
    }
    if (!answer) {
      wx.showToast({ title: "请先写下你的作答", icon: "none" });
      return;
    }
    if (this.data.loading) return;

    this._clearTimers();
    this._stages = [];
    this.setData({
      loading: true,
      stageIdx: 0,
      result: null,
      dims: [],
      consistency: null,
      consistDims: [],
      revealComments: false,
      revealEvidence: false,
      rubricOpen: false,
      canSubmit: false,
    });
    // 阶段文案按固定节奏推进：后端是一次性返回，没有真实进度可订阅，
    // 所以这里只做"正在做什么"的说明，不假装是进度百分比。
    STAGES.forEach((_s, i) => {
      if (i === 0) return;
      this._stages.push(setTimeout(() => this.setData({ stageIdx: i }), i * 700));
    });

    try {
      const body = await request("/api/marking/evaluate", {
        method: "POST",
        data: { stem, answer, qtype: this.data.qtype, include_official: true },
      });
      this.setData({
        loading: false,
        result: body,
        dims: this._dimsOf(body.dimensions, body.comments),
      });
      this._reveal(body);
    } catch (e) {
      this.setData({ loading: false, stageIdx: -1 });
      toastApiError(e);
      // 失败后允许直接重试（canSubmit 依赖 loading，故此处重算）
      this.setData({ canSubmit: this._canSubmit(this.data.stem, this.data.answer) });
    }
  },

  /**
   * 把后端的两个 dict 合并成一维的展示结构。
   *
   * ⚠️ 后端 `MarkOut` 里 `dimensions` 与 `comments` 是**两个独立 dict**（都按维度键索引），
   * 不是「每个维度一个对象」。第一版只取了 `dimensions`，于是界面上的评语会全是 `—`
   * —— 这种字段错配不报错、不白屏，只是内容悄悄空掉。
   */
  _dimsOf(dimensions, comments) {
    const d = dimensions || {};
    const c = comments || {};
    return Object.keys(DIM_LABELS).map((key) => {
      const value = typeof d[key] === "number" ? d[key] : 0;
      return {
        key,
        name: DIM_LABELS[key],
        value,
        comment: c[key] || "",
        // 后端分数是 0~100 的百分制，直接当进度条宽度用
        percent: Math.max(0, Math.min(100, value)),
      };
    });
  },

  _reveal(body) {
    if (body.refused) {
      // 拒答时没有分项分，直接把依据区放出来（用户要知道"为什么没法批"）
      this.setData({ revealComments: false, revealEvidence: true, stageIdx: -1 });
      return;
    }
    this._reveals.push(
      setTimeout(() => this.setData({ revealComments: true }), REVEAL_COMMENTS_MS),
      setTimeout(() => this.setData({ revealEvidence: true, stageIdx: -1, canSubmit: true }), REVEAL_EVIDENCE_MS)
    );
  },

  onToggleRubric() {
    this.setData({ rubricOpen: !this.data.rubricOpen });
  },

  /* ---------------- 一致性度量（N+1 次调用，显式触发） ---------------- */

  async onMeasure() {
    const stem = (this.data.stem || "").trim();
    const answer = (this.data.answer || "").trim();
    if (!stem || !answer) return;
    this.setData({ canSubmit: false });
    wx.showLoading({ title: "重复批改中", mask: true });
    try {
      const body = await request("/api/marking/consistency", {
        method: "POST",
        data: { stem, answer, qtype: this.data.qtype, include_official: true, n: 3 },
      });
      const std = body.per_dim_std || {};
      this.setData({
        consistency: body,
        consistDims: Object.keys(DIM_LABELS).map((key) => ({
          key,
          name: DIM_LABELS[key],
          std: typeof std[key] === "number" ? std[key] : "—",
        })),
      });
    } catch (e) {
      toastApiError(e);
    } finally {
      wx.hideLoading();
      this.setData({ canSubmit: true });
    }
  },
});
