import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";

const MODULES = [
  { name: "职业理念", icon: "念", desc: "教育观学生观教师观", bg: "#e6f7f6" },
  { name: "职业道德", icon: "德", desc: "教师职业道德规范", bg: "#eaf2fd" },
  { name: "教育法律法规", icon: "法", desc: "教育法教师法未保法", bg: "#fff4e5" },
  { name: "文化素养", icon: "文", desc: "常识历史科技", bg: "#f3ecfd" },
  { name: "基本能力", icon: "能", desc: "阅读理解逻辑写作", bg: "#e8f8ee" },
];

const QUESTION_COUNT = 10;

Page({
  data: {
    modules: MODULES,
    sessionId: 0,
    questionCount: 0,
    starting: false,
  },

  onShow() {
    const s = getApp().getSession();
    this.setData({ sessionId: s.sessionId, questionCount: (s.questions || []).length });
  },

  /**
   * 选中模块 → 直接发起闯关 → 跳答题页。
   *
   * 修正说明：Taro 版因 @click 失效，改用 navigator 直达答题页并把
   * start_session 前移到 answer 的 onLoad。原生 bindtap 可用，故恢复
   * 「点击即发起、失败可感知」的正确职责划分（决策四）。
   */
  async onSelectModule(e) {
    const name = e.currentTarget.dataset.module;
    if (!name || this.data.starting) return;

    this.setData({ starting: true });
    wx.showLoading({ title: "发起中…", mask: true });
    try {
      await ensureIdentity();
      const data = await request("/api/sessions/start", {
        method: "POST",
        data: { module: name, question_count: QUESTION_COUNT },
      });
      getApp().setSession(data);
      wx.hideLoading();
      this.setData({ starting: false });
      navTo("/pages/answer/answer");
    } catch (err) {
      wx.hideLoading();
      this.setData({ starting: false });
      toastApiError(err);
    }
  },

  goAnswer() {
    navTo("/pages/answer/answer");
  },

  /** S3 模考：按官方权重抽题 + 45 分钟限时，统一交卷 */
  async onStartMock() {
    if (this.data.starting) return;
    this.setData({ starting: true });
    wx.showLoading({ title: "组卷中…", mask: true });
    try {
      await ensureIdentity();
      const data = await request("/api/sessions/start", {
        method: "POST",
        data: { mode: "mock", question_count: 30 },
      });
      getApp().setSession(data);
      wx.hideLoading();
      this.setData({ starting: false });
      wx.showModal({
        title: "模考开始",
        content: "共 30 题，限时 45 分钟。答题过程中不显示对错，交卷后统一查看结果。",
        showCancel: false,
        success: () => navTo("/pages/answer/answer"),
      });
    } catch (err) {
      wx.hideLoading();
      this.setData({ starting: false });
      toastApiError(err);
    }
  },
});
