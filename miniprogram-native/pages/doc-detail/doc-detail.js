import { request, ensureIdentity, toastApiError, requestChunk, getUnionid, BASE } from "../../utils/api.js";

/** 可用微信原生预览的文件类型；txt/md 无排版，直接看切片全文（按 seq 拼接即原文顺序） */
const PREVIEWABLE = new Set(["pdf", "docx", "doc"]);

/**
 * 资料详情页（#27 资料管理）：文档信息 + 按章节分组的切片列表。
 * 切片默认只展示 120 字预览（后端 DetailOut 只给预览），点开按需拉全文（/api/documents/{id}/chunks/{cid}），
 * 与时间线的两级分离（D1）同一哲学：流量按需，信任不打折。
 */
Page({
  data: {
    loading: true,
    doc: null,
    groups: [],
    canPreview: false,
  },

  onLoad(options) {
    this._id = Number((options && options.doc_id) || 0);
    if (!this._id) {
      this.setData({ loading: false });
      return;
    }
    this.load();
  },

  async load() {
    try {
      await ensureIdentity();
      const d = await request(`/api/documents/${this._id}`);
      // 按章节分组；无章节归属的切片归入「正文」
      const byHead = new Map();
      for (const c of d.chunks || []) {
        const h = c.heading_path || "正文";
        if (!byHead.has(h)) byHead.set(h, []);
        byHead.get(h).push({ ...c, isOpen: false, content: "" });
      }
      const groups = [...byHead.entries()].map(([heading, items]) => ({ heading, items }));
      this.setData({
        doc: d,
        groups,
        loading: false,
        canPreview: PREVIEWABLE.has((d.file_type || "").toLowerCase()),
      });
    } catch (e) {
      toastApiError(e);
      this.setData({ loading: false });
    }
  },

  /** 展开 / 收起切片：首次展开时按需拉全文 */
  async onToggleChunk(e) {
    const { gi, ci, cid } = e.currentTarget.dataset;
    const item = this.data.groups[gi] && this.data.groups[gi].items[ci];
    if (!item) return;
    if (item.content) {
      this.setData({ [`groups[${gi}].items[${ci}].isOpen`]: !item.isOpen });
      return;
    }
    try {
      const full = await requestChunk(this._id, cid);
      this.setData({
        [`groups[${gi}].items[${ci}].content`]: full.content || "（无内容）",
        [`groups[${gi}].items[${ci}].isOpen`]: true,
      });
    } catch (err) {
      toastApiError(err);
    }
  },

  /** 查看原文件（#27 需求 2a）：下载上传时的原始文件后交给微信原生预览 */
  onOpenOriginal() {
    wx.showLoading({ title: "下载中" });
    wx.downloadFile({
      url: `${BASE}/api/documents/${this._id}/file`,
      header: { "X-Unionid": getUnionid() || "" },
      success: (res) => {
        wx.hideLoading();
        if (res.statusCode !== 200 || !res.tempFilePath) {
          toastApiError({ message: `下载失败(${res.statusCode})` });
          return;
        }
        wx.openDocument({
          filePath: res.tempFilePath,
          showMenu: true,
          fail: (err) => toastApiError({ message: (err && err.errMsg) || "该文件类型不支持预览" }),
        });
      },
      fail: (e) => {
        wx.hideLoading();
        toastApiError({ message: (e && e.errMsg) || "下载失败" });
      },
    });
  },
});
