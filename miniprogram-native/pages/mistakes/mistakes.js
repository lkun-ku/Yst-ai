import { request, ensureIdentity, toastApiError, navTo } from "../../utils/api.js";
import { remainingText } from "../../utils/time.js";

/** R9 / I-05：正向反馈语——把"错题"转化为"可攻克的进度"（无 emoji，规范 3.D）。 */
function feedbackText(g) {
  const n = g.wrong_count || 0;
  if (n >= 3) return "高频易错点，攻下它，这类题就能稳稳拿分";
  if (n === 2) return "再练一遍，基本就能记牢啦";
  return "小失误，顺手巩固一下就好";
}

Page({
  data: {
    loading: true,
    groups: [],
    task: null, // #27：今日任务（原 daily 页能力并入）
    taskRemain: "",
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
    try {
      const d = await request("/api/daily");
      const task = (d && d.task) || null;
      this.setData({
        task,
        taskRemain: task && task.deadline ? remainingText(task.deadline) : "",
      });
    } catch (e) {
      /* 任务加载失败不阻塞错题列表 */
    }
  },

  /** 一键发起该考点重练（复用按考点发起闯关入口）。 */
  async onRepractice(e) {
    const kp = e.currentTarget.dataset.kp;
    const count = Number(e.currentTarget.dataset.count || 2);
    if (kp) await this._startRepractice(kp, count);
  },

  /** 今日任务「开始」：直接发起第一个考点组的重练，做题即计入任务进度（#27）。 */
  async onStartTask() {
    const first = (this.data.groups || [])[0];
    if (!first) {
      wx.showToast({ title: "暂无错题可练", icon: "none" });
      return;
    }
    await this._startRepractice(first.knowledge_point, first.question_count);
  },

  async _startRepractice(kp, count) {
    if (!kp) return;
    const want = Math.max(2, Number(count || 2));
    try {
      const data = await request("/api/sessions/start", {
        method: "POST",
        data: { knowledge_point: kp, question_count: want },
      });
      if ((data.questions || []).length < want) {
        wx.showToast({
          title: `可用题目不足，已按 ${data.question_count} 题开局`,
          icon: "none",
          duration: 2200,
        });
      }
      getApp().setSession(data);
      navTo("/pages/answer/answer");
    } catch (e) {
      toastApiError(e);
    }
  },
});
