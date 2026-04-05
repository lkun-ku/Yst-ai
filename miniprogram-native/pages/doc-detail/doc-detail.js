import { request, ensureIdentity, toastApiError, requestChunk } from "../../utils/api.js";

/**
 * 资料详情页（#27 资料管理）：文档信息 + 按章节分组的切片列表。
 * 切片默认只展示 120 字预览（后端 DetailOut 只给预览），点开按需拉全文（/api/kb/chunk/{id}），
 * 与时间线的两级分离（D1）同一哲学：流量按需，信任不打折。
 */
Page({
  data: {
    loading: true,
    doc: null,
    groups: [],
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
      this.setData({ doc: d, groups, loading: false });
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
      const full = await requestChunk(cid);
      this.setData({
        [`groups[${gi}].items[${ci}].content`]: full.content || "（无内容）",
        [`groups[${gi}].items[${ci}].isOpen`]: true,
      });
    } catch (err) {
      toastApiError(err);
    }
  },
});
