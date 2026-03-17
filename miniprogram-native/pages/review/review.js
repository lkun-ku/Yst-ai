import { request, ensureIdentity, toastApiError } from "../../utils/api.js";
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
      this.setData({ summary: summarizeReview(data), loading: false });
    } catch (e) {
      toastApiError(e);
      this.setData({ summary: null, loading: false });
    }
  },

  goMistakes() {
    wx.reLaunch({ url: "/pages/mistakes/mistakes" });
  },

  goQuest() {
    wx.reLaunch({ url: "/pages/quest/quest" });
  },
});
