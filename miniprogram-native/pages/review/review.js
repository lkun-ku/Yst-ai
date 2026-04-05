import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";
import { summarizeReview } from "../../utils/reviewShape.js";

Page({
  data: {
    loading: true,
    summary: null,
  },

  /**
   * 复盘页只负责「读取并展示复盘」。
   *
   * 修正说明：Taro 版用 `?submit=1` 让本页 onLoad 代答交卷（因答题页点击失效），
   * 原生下答题页已直接提交，该 workaround 删除（决策四）。
   */
  onLoad(options) {
    const sid = Number((options && options.session_id) || 0);
    if (!sid) {
      this.setData({ loading: false, summary: null });
      return;
    }
    this._load(sid);
  },

  async _load(sid) {
    try {
      await ensureIdentity();
      const data = await request(`/api/review/${sid}`);
      const summary = summarizeReview(data);
      // 补充本局战绩（hero 大数字）：从历史列表取该局正确数/总数；缺失不阻塞复盘展示
      try {
        const hist = (await request("/api/sessions/history")) || [];
        const hit = hist.find((x) => x.session_id === sid);
        if (hit && hit.question_count) {
          summary.correctCount = hit.correct_count || 0;
          summary.questionCount = hit.question_count;
          summary.rateText = Math.round(((hit.correct_count || 0) / hit.question_count) * 100);
        }
      } catch (e) {
        /* 战绩缺失时 hero 不显示数字，仅展示雷达与段落 */
      }
      this.setData({ summary, loading: false });
    } catch (e) {
      toastApiError(e);
      this.setData({ summary: null, loading: false });
    }
  },

  goMistakes() {
    wx.reLaunch({ url: "/pages/mistakes/mistakes" });
  },

  /** 重练薄弱考点（#27 方案 B）：复盘页直接发起该考点重练，不打断流程。 */
  async onRepracticeWeak(e) {
    const kp = e.currentTarget.dataset.kp;
    if (!kp) return;
    try {
      const count = 3;
      const data = await request("/api/sessions/start", {
        method: "POST",
        data: { knowledge_point: kp, question_count: count },
      });
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

  goQuest() {
    wx.reLaunch({ url: "/pages/quest/quest" });
  },
});
