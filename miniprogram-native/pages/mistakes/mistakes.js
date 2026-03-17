import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";

/** R9 / I-05：正向反馈语——把"错题"转化为"可攻克的进度"。 */
function feedbackText(g) {
  const n = g.wrong_count || 0;
  if (n >= 3) return "高频易错点 · 攻下它，这类题就能稳稳拿分 👊";
  if (n === 2) return "再练一遍，基本就能记牢啦";
  return "小失误 · 顺手巩固一下就好";
}

Page({
  data: {
    loading: true,
    groups: [],
  },

  onLoad() {
    this.load();
  },

  onShow() {
    this.load(); // 重练返回后刷新错误次数
  },

  async load() {
    try {
      await ensureIdentity();
      const list = (await request("/api/mistakes")) || [];
      const groups = list.map((g) => ({
        ...g,
        module: g.module || "综合",
        feedback: feedbackText(g), // WXML 不能调函数，预先算好
      }));
      this.setData({ groups, loading: false });
    } catch (e) {
      toastApiError(e);
      this.setData({ loading: false });
    }
  },

  /** 一键发起该考点重练（复用按考点发起闯关入口）。 */
  async onRepractice(e) {
    const kp = e.currentTarget.dataset.kp;
    const count = Number(e.currentTarget.dataset.count || 2);
    if (!kp) return;
    try {
      const data = await request("/api/sessions/start", {
        method: "POST",
        data: { knowledge_point: kp, question_count: Math.max(2, count) },
      });
      getApp().setSession(data);
      wx.showToast({ title: `重练《${kp}》${(data.questions || []).length} 题`, icon: "success" });
      navTo("/pages/answer/answer");
    } catch (e) {
      toastApiError(e);
    }
  },
});
