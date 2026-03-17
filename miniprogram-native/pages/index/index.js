import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";
import { shouldShowAigcNotice } from "../../utils/notice.js";
import { remainingText } from "../../utils/time.js";

const DRAFT_KEY = "quest_draft";

/** 功能入口。VIP/权益说明入口已下线（页面与接口保留休眠）。 */
const ENTRIES = [
  { icon: "闯", title: "进入闯关", desc: "按模块或考点发起", bg: "#e6f7f6", url: "/pages/quest/quest" },
  { icon: "每", title: "每日任务", desc: "考期倒计时与新题", bg: "#eaf2fd", url: "/pages/daily/daily" },
  { icon: "错", title: "错题本", desc: "按考点聚合重练", bg: "#fff4e5", url: "/pages/mistakes/mistakes" },
  { icon: "史", title: "历史闯关", desc: "回看往期复盘", bg: "#f3ecfd", url: "/pages/history/history" },
  { icon: "料", title: "我的资料", desc: "上传资料出试题", bg: "#e8f8ee", url: "/pages/docs/docs" },
  { icon: "卷", title: "新建试卷", desc: "按范围跨资料出题", bg: "#fdeaf0", url: "/pages/kb/kb" },
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
    this.loadResume();
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

  goDaily() {
    navTo("/pages/daily/daily");
  },
});
