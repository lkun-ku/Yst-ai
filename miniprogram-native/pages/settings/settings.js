import { request, toastApiError } from "../../utils/api.js";

Page({
  data: {
    showData: false,
    summary: null,
  },

  /** R10：聚合历史局数、错题考点数、最近一局掌握度（无新增接口） */
  async onView() {
    try {
      const [hist, mistakes] = await Promise.all([
        request("/api/sessions/history"),
        request("/api/mistakes"),
      ]);
      const sessions = Array.isArray(hist) ? hist : [];
      const last = sessions[0];
      const mastery = (last && last.mastery_overview) || {};
      this.setData({
        summary: {
          sessionCount: sessions.length,
          mistakeGroups: Array.isArray(mistakes) ? mistakes.length : 0,
          // WXML 不能调函数，掌握度文案预先拼好
          masteryText: Object.entries(mastery)
            .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`)
            .join("，"),
          hasMastery: Object.keys(mastery).length > 0,
        },
        showData: true,
      });
    } catch (e) {
      toastApiError(e);
    }
  },

  onCloseData() {
    this.setData({ showData: false });
  },

  /** 破坏性操作用系统弹窗，用户更信任且零样式风险 */
  onDelete() {
    wx.showModal({
      title: "确认删除",
      content: "将清除全部学习数据，不可恢复。",
      success: async (r) => {
        if (!r.confirm) return;
        try {
          await request("/api/identity/data", { method: "DELETE" });
          try {
            wx.removeStorageSync("unionid");
          } catch (e) {
            /* 清理本地失败不阻塞 */
          }
          wx.showToast({ title: "已删除", icon: "success" });
        } catch (e) {
          toastApiError(e);
        }
      },
    });
  },
});
