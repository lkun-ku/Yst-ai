import { request, ensureIdentity, toastApiError } from "../../utils/api.js";
import { remainingText } from "../../utils/time.js";

Page({
  data: {
    examDate: "",
    examInput: "",
    countdown: null,
    task: null,
    taskRemain: "",
    feedback: "",
    streak: null, // S1 连胜
  },

  onLoad() {
    this.load();
  },

  /** R6 / K-06：从答题返回后刷新任务状态与倒计时 */
  onShow() {
    this.load();
    this.loadStreak();
  },

  /** S1：连胜信息（失败不阻塞，连胜是锦上添花） */
  async loadStreak() {
    try {
      const s = await request("/api/streak");
      this.setData({ streak: s });
    } catch (e) {
      /* 静默失败 */
    }
  },

  /** S1：补签（使用一张补签卡补最近漏掉的一天） */
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
          this.loadStreak();
        } catch (e) {
          toastApiError(e);
        }
      },
    });
  },

  async load() {
    try {
      await ensureIdentity();
      const d = await request("/api/daily");
      const task = (d && d.task) || null;
      this.setData({
        examDate: (d && d.exam_date) || "",
        countdown: d && d.countdown_days !== undefined && d.countdown_days !== null ? d.countdown_days : null,
        task,
        taskRemain: task && task.deadline ? remainingText(task.deadline) : "",
      });
    } catch (e) {
      toastApiError(e);
    }
  },

  /** v-model 的原生等价写法 */
  onExamInput(e) {
    this.setData({ examInput: e.detail.value });
  },

  async onSaveExamDate() {
    const v = (this.data.examInput || "").trim();
    if (!v) {
      wx.showToast({ title: "请输入考试日期", icon: "none" });
      return;
    }
    try {
      const r = await request("/api/daily/exam-date", { method: "POST", data: { exam_date: v } });
      this.setData({ examDate: (r && r.exam_date) || "", countdown: r ? r.countdown_days : null });
      wx.showToast({ title: `已保存，倒计时 ${r.countdown_days} 天`, icon: "success" });
    } catch (e) {
      toastApiError(e);
    }
  },

  /** 完成反馈（验收 4）；超时由后端拦截返回 410（K-03） */
  async onComplete() {
    const task = this.data.task;
    if (!task) return;
    try {
      const r = await request("/api/daily/complete", { method: "POST", data: { task_id: task.task_id } });
      this.setData({
        task: { ...task, completed: true },
        feedback: (r && r.feedback) || "",
      });
      wx.showToast({ title: (r && r.feedback) || "已完成", icon: "success", duration: 2500 });
    } catch (e) {
      toastApiError(e);
    }
  },
});
