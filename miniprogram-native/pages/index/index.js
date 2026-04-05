import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";
import { shouldShowAigcNotice } from "../../utils/notice.js";
import { remainingText } from "../../utils/time.js";

const DRAFT_KEY = "quest_draft";

/** 功能入口。#27：每日任务页删除——考期/连胜上首页，今日任务并入错题本。 */
const ENTRIES = [
  { icon: "闯", title: "进入闯关", desc: "按模块或考点发起", bg: "#e6f7f6", url: "/pages/quest/quest" },
  { icon: "错", title: "错题本", desc: "今日任务与考点重练", bg: "#fff4e5", url: "/pages/mistakes/mistakes" },
  { icon: "史", title: "历史闯关", desc: "回看往期复盘", bg: "#f3ecfd", url: "/pages/history/history" },
  { icon: "料", title: "我的资料", desc: "上传资料出试题", bg: "#e8f8ee", url: "/pages/docs/docs" },
  { icon: "我", title: "我的设置", desc: "管理学习数据", bg: "#eef1f4", url: "/pages/settings/settings" },
];

Page({
  data: {
    acked: false,
    showNotice: false,
    quota: { used_today: 0, free_daily_limit: 1000, remaining: 1000, is_vip: false, reset_rule: "" },
    progressPct: 0,
    examDate: "",
    countdown: null,
    dailyTask: null,
    taskRemain: "",
    streak: null, // #27：连胜徽章（原 daily 页能力上移）
    resume: null,
    entries: ENTRIES,
  },

  onLoad() {
    this.load();
  },

  /** R5 / C-05：返回首页时刷新"继续进度" */
  onShow() {
    this.loadResume();
  },

  /** R2 / B-02：AIGC 首次说明确认，以后端状态为准 */
  async onAck() {
    try {
      await ensureIdentity();
      const me = await request("/api/identity/ack-aigc", { method: "POST" });
      const acked = !!(me && me.aigc_notice_acked);
      this.setData({ acked, showNotice: shouldShowAigcNotice(acked) });
    } catch (e) {
      toastApiError(e);
    }
  },

  loadResume() {
    let d = null;
    try {
      d = wx.getStorageSync(DRAFT_KEY);
    } catch (e) {
      d = null;
    }
    if (d && d.sessionId && d.questions && d.questions.length) {
      const answered = Object.keys(d.answers || {}).length;
      this.setData({ resume: { sessionId: d.sessionId, answered, total: d.questions.length } });
    } else {
      this.setData({ resume: null });
    }
  },

  async load() {
    // 后端不可达时页面仍可用（本地草稿/静态入口不阻塞），故逐项容错
    try {
      await ensureIdentity();
    } catch (e) {
      /* 非阻塞 */
    }
    try {
      const me = await request("/api/identity/me");
      const acked = !!(me && me.aigc_notice_acked);
      this.setData({ acked, showNotice: shouldShowAigcNotice(acked) });
    } catch (e) {
      /* 非阻塞 */
    }
    try {
      const quota = await request("/api/quota");
      const total = (quota && quota.free_daily_limit) || 20;
      const used = (quota && quota.used_today) || 0;
      this.setData({ quota, progressPct: Math.min(100, Math.round((used / total) * 100)) });
    } catch (e) {
      /* 非阻塞 */
    }
    try {
      const d = await request("/api/daily");
      const task = (d && d.task) || null;
      this.setData({
        examDate: (d && d.exam_date) || "",
        countdown: d ? d.countdown_days : null,
        dailyTask: task,
        taskRemain: task && task.deadline ? remainingText(task.deadline) : "",
      });
    } catch (e) {
      /* 非阻塞 */
    }
    try {
      const streak = await request("/api/streak");
      this.setData({ streak });
    } catch (e) {
      /* 非阻塞 */
    }
    this.loadResume();
  },

  /** #27：首页直接设置/修改考期（原需跳 daily 页），picker 选完即存 */
  async onPickExamDate(e) {
    const v = (e.detail && e.detail.value) || "";
    if (!v) return;
    try {
      const r = await request("/api/daily/exam-date", { method: "POST", data: { exam_date: v } });
      this.setData({ examDate: (r && r.exam_date) || v, countdown: r ? r.countdown_days : null });
      wx.showToast({ title: `已保存，倒计时 ${r.countdown_days} 天`, icon: "success" });
    } catch (err) {
      toastApiError(err);
    }
  },

  /** #27：补签（原 daily 页能力上移） */
  onMakeup() {
    const st = this.data.streak;
    if (!st || !st.can_makeup || !st.makeup_date) return;
    wx.showModal({
      title: "补签",
      content: `使用 1 张补签卡补上 ${st.makeup_date}（当前剩余 ${st.cards} 张）？`,
      confirmText: "补签",
      success: async (r) => {
        if (!r.confirm) return;
        try {
          const res = await request("/api/streak/makeup", {
            method: "POST",
            data: { date: st.makeup_date },
          });
          wx.showToast({ title: `补签成功，连胜 ${res.current} 天`, icon: "success" });
          this.setData({ streak: res });
        } catch (err) {
          toastApiError(err);
        }
      },
    });
  },

  goMistakes() {
    navTo("/pages/mistakes/mistakes");
  },

  goResume() {
    let d = null;
    try {
      d = wx.getStorageSync(DRAFT_KEY);
    } catch (e) {
      d = null;
    }
    if (!d) return;
    getApp().setSession({ session_id: d.sessionId, questions: d.questions });
    navTo("/pages/answer/answer");
  },

  onEntryTap(e) {
    const url = e.currentTarget.dataset.url;
    if (url) navTo(url);
  },
});
