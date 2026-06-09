import { request, ensureIdentity, toastApiError, requestChunk, getUnionid, BASE } from "../../utils/api.js";

/** 可用微信原生预览的文件类型；txt/md 无排版，直接看切片全文（按 seq 拼接即原文顺序） */
const PREVIEWABLE = new Set(["pdf", "docx", "doc"]);

/**
 * 资料详情页（#27 资料管理）：文档信息 + 按章节分组的切片列表。
 * 切片默认只展示 120 字预览（后端 DetailOut 只给预览），点开按需拉全文（/api/documents/{id}/chunks/{cid}），
 * 与时间线的两级分离（D1）同一哲学：流量按需，信任不打折。
 */
/**
 * 全文视图的如实说明。
 *
 * ⚠️ 这段文字不是客套：上传时的原文件按 A3 决策**不保留**（只留切片，降低版权风险），
 * 所以这里的"全文"是**由切片按原文顺序拼回来的重建文本**。不说清楚，用户会拿它当原文件
 * 逐字核对（发现段落被合并了），然后以为产品把资料弄坏了。
 */
function _fullNote(f) {
  const parts = ["以下全文由该资料的切片按原文顺序拼接（已合并重叠部分）"];
  if (!f.has_original_file) {
    parts.push("上传时的原文件未保留，此处为重建文本，分段与排版可能与原文件略有差异");
  }
  // 实测（上传 7980 字 → 全文 7935 字）：少掉的正是**章节标题行** ——
  // 解析时被提取成章节信息（「按片段」视图里就显示为分组标题），正文中不再重复。
  // 不说这一句，用户会数着字数以为丢了内容。
  parts.push("章节标题在解析时被提取为章节信息，正文中不再重复");
  if (f.truncated) parts.push("内容过长，此处只展示前一部分");
  return `${parts.join("；")}。`;
}

Page({
  data: {
    loading: true,
    doc: null,
    groups: [],
    canPreview: false,
    //: 视图：chunks（按片段，默认）/ full（连续全文，按需拉取）
    view: "chunks",
    full: null,
    fullLoading: false,
    fullNote: "",
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

  /**
   * 切换「按片段 / 连续全文」。
   *
   * 全文**按需拉取**（与切片全文同一个"流量按需"哲学）：一次拼接之后复用，不重复请求。
   * 失败时只提示、不影响「按片段」视图继续用 —— 两种读法是同一份数据的两个视图。
   */
  async onSwitchView(e) {
    const v = (e.currentTarget.dataset || {}).v;
    if (v !== "full" && v !== "chunks") return;
    this.setData({ view: v });
    if (v !== "full" || this.data.full || this.data.fullLoading) return;
    this.setData({ fullLoading: true });
    try {
      const f = await request(`/api/documents/${this._id}/full`);
      this.setData({ full: f, fullLoading: false, fullNote: _fullNote(f) });
    } catch (err) {
      this.setData({ fullLoading: false });
      toastApiError(err);
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
