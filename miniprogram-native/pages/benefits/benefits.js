import { request, ensureIdentity, toastApiError } from "../../utils/api.js";

/**
 * 权益说明页。
 *
 * 注意：本页当前处于「休眠」状态——首页入口已下线（需求变更），
 * 页面与后端 vip-activate 接口均保留但不可达。
 */
Page({
  data: {
    q: { is_vip: false, free_daily_limit: 1000, used_today: 0, remaining: 1000, reset_rule: "" },
    progressPct: 0,
    dims: [],
  },

  onLoad() {
    this.load();
  },

  async load() {
    try {
      await ensureIdentity();
      const q = await request("/api/quota");
      const total = q.free_daily_limit || 20;
      this.setData({
        q,
        progressPct: Math.min(100, Math.round(((q.used_today || 0) / total) * 100)),
        // 每日题量以服务端额度为准（B6：内测期 1000 题），不再硬编码 20
        dims: [
          { label: "每日题量", free: `${total} 题`, vip: "不限量" },
          { label: "复盘报告", free: "基础版", vip: "深度版（五维+薄弱+下一步）" },
          { label: "错题变式", free: "3 次/日", vip: "不限量" },
          { label: "RAG 检索", free: "无", vip: "错题本专属变式" },
        ],
      });
    } catch (e) {
      toastApiError(e);
    }
  },

  async onActivate() {
    try {
      const r = await request("/api/quota/vip-activate", { method: "POST" });
      this.setData({ q: { ...this.data.q, is_vip: !!r.is_vip } });
      wx.showToast({ title: "已开通 VIP", icon: "success" });
    } catch (e) {
      toastApiError(e);
    }
  },
});
