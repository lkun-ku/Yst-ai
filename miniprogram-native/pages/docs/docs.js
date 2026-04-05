import { BASE, ensureIdentity, getUnionid, navTo, request, toastApiError } from "../../utils/api.js";

/**
 * 我的资料：上传 / 列表 / 删除。
 *
 * 官方文档（参数以官方为准）：
 *   chooseMessageFile https://developers.weixin.qq.com/miniprogram/dev/api/media/image/wx.chooseMessageFile.html
 *   uploadFile        https://developers.weixin.qq.com/miniprogram/dev/api/network/upload/wx.uploadFile.html
 * 关键约束：extension 仅在 type === 'file' 时生效；该 API 只能从**微信聊天会话**选文件，
 * 因此 UI 必须引导用户先把文件发到聊天里（A7）。
 */

const MAX_MB = 20;
const ALLOWED = ["pdf", "docx", "txt", "md"];

Page({
  data: {
    loading: true,
    docs: [],
    uploading: false,
    uploadName: "",
    uploadProgress: 0,
  },

  onLoad() {
    this.load();
  },

  onShow() {
    this.load();
  },

  async load() {
    try {
      await ensureIdentity();
      const docs = (await request("/api/documents")) || [];
      this.setData({ docs, loading: false });
    } catch (e) {
      toastApiError(e);
      this.setData({ loading: false });
    }
  },

  /** A7：只能从聊天记录选文件，先提示用户 */
  onChoose() {
    wx.showModal({
      title: "从微信聊天里选文件",
      content: "请先把 PDF / Word / TXT / Markdown 文件发到任意微信聊天（可发给自己），再点“选择文件”。",
      confirmText: "知道了",
      success: (r) => {
        if (r.confirm) this._chooseFile();
      },
    });
  },

  _chooseFile() {
    wx.chooseMessageFile({
      count: 9,
      type: "file",
      extension: ALLOWED, // 仅 type==='file' 时有效
      success: (res) => {
        const files = (res.tempFiles || []);
        if (files.length) this._uploadFiles(files.slice());
      },
      fail: (e) => {
        // 用户主动取消不打扰
        if (e && e.errMsg && /cancel/.test(e.errMsg)) return;
        wx.showToast({ title: "选择文件失败", icon: "none" });
      },
    });
  },

  /** 连续上传多份资料（#21）：逐份顺序上传，全部完成后统一刷新并提示可继续添加。 */
  async _uploadFiles(queue) {
    for (const f of queue) {
      const ok = await this._upload(f);
      if (!ok) break; // 失败已 toast/modal，停止本批剩余
    }
    this.load();
    wx.showToast({ title: "已上传，可继续添加", icon: "none" });
  },

  _upload(f) {
    return new Promise((resolve) => {
      if (f.size && f.size > MAX_MB * 1024 * 1024) {
        wx.showToast({ title: `文件不能超过 ${MAX_MB}MB`, icon: "none" });
        resolve(false);
        return;
      }
      (async () => {
        try {
          await ensureIdentity();
        } catch (e) {
          /* 身份失败也继续，由后端 401 兜底 */
        }
        this.setData({ uploading: true, uploadName: f.name || "文件", uploadProgress: 0 });
        const task = wx.uploadFile({
          url: `${BASE}/api/documents`,
          filePath: f.path,
          name: "file",
          header: { "X-Unionid": getUnionid() || "" },
          formData: {},
          timeout: 120000, // 20MB 弱网需要更长超时（C2）
          success: (r) => {
            if (r.statusCode >= 200 && r.statusCode < 300) {
              // 单份成功不弹 toast，批量结束后统一提示
            } else {
              let detail = "";
              try {
                detail = (JSON.parse(r.data) && JSON.parse(r.data).detail) || "";
              } catch (e) {
                detail = "";
              }
              wx.showModal({ title: "上传失败", content: detail || `服务返回 ${r.statusCode}`, showCancel: false });
              resolve(false);
              return;
            }
          },
          fail: () => {
            wx.showModal({
              title: "上传失败",
              content: "手机可能连不到 dev 机后端，请确认与电脑在同一 WiFi。",
              showCancel: false,
            });
            resolve(false);
            return;
          },
          complete: () => {
            this.setData({ uploading: false, uploadProgress: 0 });
            resolve(true);
          },
        });
        task.onProgressUpdate((p) => {
          this.setData({ uploadProgress: p.progress || 0 });
        });
      })();
    });
  },

  onGenerate(e) {
    const id = e.currentTarget.dataset.id;
    if (id) navTo(`/pages/generate/generate?doc_id=${id}`);
  },

  /** #27 查看资料：进入详情页（章节与切片内容） */
  onView(e) {
    const id = e.currentTarget.dataset.id;
    if (id) navTo(`/pages/doc-detail/doc-detail?doc_id=${id}`);
  },

  /** #27 资料重命名：showModal 原生输入框，PATCH 成功后本地更新（不重拉列表） */
  onRename(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.showModal({
      title: "重命名资料",
      editable: true,
      placeholderText: "输入新名称",
      success: async (r) => {
        if (!r.confirm) return;
        const title = (r.content || "").trim();
        if (!title) {
          wx.showToast({ title: "名称不能为空", icon: "none" });
          return;
        }
        try {
          await request(`/api/documents/${id}`, { method: "PATCH", data: { title } });
          this.setData({ docs: this.data.docs.map((d) => (d.id === id ? { ...d, title } : d)) });
          wx.showToast({ title: "已重命名", icon: "success" });
        } catch (err) {
          toastApiError(err);
        }
      },
    });
  },

  /** #22：「自建题库」组卷入口（首页入口已移入本页） */
  goKb() {
    navTo("/pages/kb/kb");
  },

  /** A5：删除会级联移除该资料生成的个人题，需二次确认 */
  onDelete(e) {
    const id = e.currentTarget.dataset.id;
    const name = e.currentTarget.dataset.name || "该资料";
    if (!id) return;
    wx.showModal({
      title: "删除资料？",
      content: `《${name}》及其已生成的题目将一并移除，不可恢复。`,
      confirmText: "删除",
      confirmColor: "#e54d42",
      success: async (r) => {
        if (!r.confirm) return;
        try {
          const res = await request(`/api/documents/${id}`, { method: "DELETE" });
          wx.showToast({ title: `已删除${res.removed_questions ? `，移除 ${res.removed_questions} 题` : ""}`, icon: "success" });
          this.load();
        } catch (err) {
          toastApiError(err);
        }
      },
    });
  },
});
