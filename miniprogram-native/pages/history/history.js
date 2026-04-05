import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";

function rateOf(s) {
  if (!s.question_count) return 0;
  return Math.round((s.correct_count / s.question_count) * 100);
}
function rateClassOf(s) {
  const r = rateOf(s);
  if (r >= 80) return "rate-good";
  if (r >= 60) return "rate-mid";
  return "rate-bad";
}
function fmtTime(iso) {
  return iso ? iso.slice(0, 16).replace("T", " ") : "-";
}

Page({
  data: {
    loading: true,
    items: [],
  },

  onShow() {
    this.load();
  },

  async load() {
    try {
      await ensureIdentity();
      const list = (await request("/api/sessions/history")) || [];
      const items = list.map((s) => ({
        ...s,
        rateText: `答对 ${s.correct_count}/${s.question_count}`,
        rateClass: rateClassOf(s),
        time: fmtTime(s.submitted_at),
      }));
      this.setData({ items, loading: false });
    } catch (e) {
      toastApiError(e);
      this.setData({ loading: false });
    }
  },

  /**
   * 回看某局复盘：直接跳转复盘页（S5 迁移）。
   *
   * 修正说明：Taro 版用 nut-dialog 弹窗展示摘要；原生改为 navigateTo 复盘页，
   * 信息更完整、可滚动，且无需自定义弹窗（零 npm 依赖）。
   */
  onOpen(e) {
    const sid = e.currentTarget.dataset.sid;
    if (sid) navTo(`/pages/review/review?session_id=${sid}`);
  },

  /**
   * 重练本局（#27）：有考点按考点，模块局按模块发起。
   * 后端不足时降级开局，此处提示实际题数。
   */
  async onRepractice(e) {
    const kp = e.currentTarget.dataset.kp;
    const mod = e.currentTarget.dataset.module;
    const count = Math.max(2, Number(e.currentTarget.dataset.count || 3));
    if (!kp && !mod) return;
    const body = { question_count: count };
    if (kp) body.knowledge_point = kp;
    else body.module = mod;
    try {
      const data = await request("/api/sessions/start", { method: "POST", data: body });
      if ((data.questions || []).length < count) {
        wx.showToast({
          title: `可用题目不足，已按 ${data.question_count} 题开局`,
          icon: "none",
          duration: 2200,
        });
      }
      getApp().setSession(data);
      navTo("/pages/answer/answer");
    } catch (err) {
      toastApiError(err);
    }
  },
});
