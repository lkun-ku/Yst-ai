import { request, toastApiError } from "../../utils/api.js";

/** #31 AI 模拟答：配置 → 聊天对话（追问式评分）→ 汇总/趋势。#32 纯 LLM 生成：无模块选择。 */
const MAX_Q = 5;
// #32 v2：旧 key 的会话（纯生成改造前）没有评分要点，会被后端拒；升版本自动废弃
const ACTIVE_KEY = "chat_active_session_v2";

Page({
  data: {
    mode: "config", // config | chat
    difficulty: "medium",
    persona: "coach",
    msgs: [], // {seq, role, turnType, content, shown, typing, score, hit, missed, wrong, suggestion}
    input: "",
    thinking: false,
    finished: false,
    summary: null,
    progress: { cur: 1, total: null }, // #33 无限问答，不设上限
    scrollInto: "",
    history: [], // #32 多会话：全部对话（含未完成）
    recording: false, // #37 按住说话
    speakingSeq: null, // #37 正在朗读的消息
    voiceMode: false, // #38 语音模式：开=按住说话+AI 自动朗读；关=文字输出
  },

  async onLoad() {
    // #33 中间层：进入页面一律先显示对话列表（继续旧对话 / 新建由用户选），
    // 不再自动跳进上次对话（此前退出重进被强制拉回最新一场，无法选择）
    this.loadHistory();
  },

  async onShow() {
    if (this.data.mode === "config") {
      this.loadHistory();
      return;
    }
    // 对话中被切后台回收：内存清空但 mode 仍为 chat → 静默重放当前场
    if (this.data.mode === "chat" && this.data.msgs.length === 0) {
      const sid = wx.getStorageSync(ACTIVE_KEY);
      if (sid) await this.resume(sid, true);
    }
  },

  /** #32 多会话：加载全部对话（含 unfinished） */
  async loadHistory() {
    try {
      const rows = await request("/api/chat/history");
      const history = (rows || []).map((r) => ({
        ...r,
        title: r.status === "active" ? `进行中 · 第 ${r.question_count + 1} 题` : `已完成 · 均分 ${r.score_avg}`,
        time: (r.started_at || "").slice(5, 16).replace("T", " ") || "",
      }));
      this.setData({ history });
    } catch (e) {
      /* 非阻塞 */
    }
  },

  /** #32 继续某场对话 */
  async onContinue(e) {
    const sid = Number(e.currentTarget.dataset.sid);
    if (!sid) return;
    await this.resume(sid, true);
  },

  /** #32 新开对话（当前场保留在历史中，可随时回去继续） */
  onNewChat() {
    this.setData({ msgs: [], finished: false, summary: null, input: "" });
    wx.removeStorageSync(ACTIVE_KEY);
    this.onStart();
  },

  onUnload() {
    this._stopTyping();
  },

  onHide() {
    this._stopTyping();
  },

  /** ---- 配置态 ---- */
  onPickDifficulty(e) {
    this.setData({ difficulty: e.currentTarget.dataset.d });
  },
  onPickPersona(e) {
    this.setData({ persona: e.currentTarget.dataset.p });
  },

  async onStart() {
    try {
      wx.showLoading({ title: "准备中", mask: true });
      const body = await request("/api/chat/start", {
        method: "POST",
        data: { difficulty: this.data.difficulty, persona: this.data.persona },
      });
      wx.setStorageSync(ACTIVE_KEY, body.session_id);
      this.setData({
        mode: "chat",
        msgs: [],
        finished: false,
        summary: null,
        progress: body.progress,
      });
      for (const m of body.messages) {
        await this._pushTyping(m);
      }
    } catch (e) {
      toastApiError(e);
    } finally {
      wx.hideLoading();
    }
  },

  /** ---- 会话恢复：重放历史消息（无打字机，直接渲染） ---- */
  async resume(sid, silent) {
    try {
      const body = await request(`/api/chat/session/${sid}`);
      if (body.status !== "active") {
        wx.removeStorageSync(ACTIVE_KEY);
        return;
      }
      const msgs = body.messages.map((m) => this._toMsg(m, true));
      wx.setStorageSync(ACTIVE_KEY, sid);
      this.setData({ mode: "chat", msgs, progress: body.progress, finished: false });
      this._scrollBottom();
    } catch (e) {
      wx.removeStorageSync(ACTIVE_KEY);
      if (!silent) toastApiError(e);
    }
  },

  /** #32 从对话返回配置态（当前场保留在历史列表，可继续） */
  onBackToList() {
    this.setData({ mode: "config" });
    this.loadHistory();
  },

  /** #35 删除一场对话（二次确认；删掉当前场时清本地缓存） */
  async onDeleteChat(e) {
    const sid = Number(e.currentTarget.dataset.sid);
    if (!sid) return;
    const isActive = e.currentTarget.dataset.status === "active";
    wx.showModal({
      title: "删除这场对话？",
      content: isActive ? "这场还没结束，删除后无法继续。" : "删除后历史记录与本局数据都会移除。",
      confirmText: "删除",
      confirmColor: "#C25450",
      success: async (r) => {
        if (!r.confirm) return;
        try {
          await request(`/api/chat/session/${sid}`, { method: "DELETE" });
          if (Number(wx.getStorageSync(ACTIVE_KEY)) === sid) wx.removeStorageSync(ACTIVE_KEY);
          wx.showToast({ title: "已删除", icon: "success" });
          this.loadHistory();
        } catch (err) {
          toastApiError(err);
        }
      },
    });
  },

  /** #33 主动结束本场：显示本局汇总（持续问答模式下的收官入口） */
  async onFinishChat() {
    const sid = wx.getStorageSync(ACTIVE_KEY);
    if (!sid) return;
    wx.showModal({
      title: "结束本场训练？",
      content: "结束后可查看本局汇总，这场会移入历史对话。",
      confirmText: "结束",
      success: async (r) => {
        if (!r.confirm) return;
        try {
          const raw = await request("/api/chat/finish", { method: "POST", data: { session_id: sid } });
          const scores = raw.scores || [];
          wx.removeStorageSync(ACTIVE_KEY);
          this.setData({ finished: true, summary: { ...raw, bestScore: scores.length ? Math.max(...scores) : 0 } });
          this._scrollBottom();
        } catch (e) {
          toastApiError(e);
        }
      },
    });
  },

  /** ---- 对话 ---- */
  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  async onSend() {
    const content = (this.data.input || "").trim();
    if (!content || this.data.thinking || this.data.finished) return;
    // 同步互斥锁（#32）：setData 异步，data 检查挡不住连点；实例标志同 Breakeven 置位
    if (this._sending) return;
    this._sending = true;
    try {
      // #32 修复双气泡：用户气泡统一由 _reply 内部 push（此处不可再 push 一次）
      this.setData({ input: "" });
      await this._reply({ content, local: true });
    } finally {
      this._sending = false;
    }
  },

  /** 跳过追问，直接看评分 */
  async onForceFinal() {
    if (this.data.thinking || this.data.finished || this._sending) return;
    this._sending = true;
    try {
      await this._reply({ content: "（我想直接看评分）", forceFinal: true, local: false });
    } finally {
      this._sending = false;
    }
  },

  async _reply({ content, forceFinal = false, local = true }) {
    const sid = wx.getStorageSync(ACTIVE_KEY);
    if (!sid) return;
    if (local) this.setData({ msgs: [...this.data.msgs, this._toMsg({ role: "user", turn_type: "user", content }, true)] });
    this.setData({ thinking: true });
    this._scrollBottom();
    try {
      const body = await request("/api/chat/reply", {
        method: "POST",
        data: { session_id: sid, content, force_final: forceFinal },
      });
      if (body.finished) {
        this.setData({ finished: true, summary: body.session_summary });
        wx.removeStorageSync(ACTIVE_KEY);
      } else {
        this.setData({ progress: body.progress });
      }
      for (const m of body.messages) {
        if (m.role === "user") continue; // 用户消息已在本地渲染
        await this._pushTyping(m);
        // #38 语音模式：AI 消息打字完成后自动朗读（错误提示不读）
        if (this.data.voiceMode && m.role === "ai" && m.turn_type !== "error") {
          this._speak(m.seq, m.content);
        }
      }
      // #36 下一题出题失败（终评已生效）：提示重发即自愈，不再让用户以为断网
      if (body.next_failed) {
        await this._pushTyping({
          role: "ai",
          turn_type: "error",
          content: "这道题已经评分完成，但下一题出题时网络抖了一下。直接再发一条消息，我会自动补上下一题。",
        });
      }
      if (body.finished) {
        await this._pushSummary(body.session_summary);
      }
    } catch (e) {
      // 评分失败：只提示不落库（与后端 503 语义一致）；透传后端真实原因，不再硬编码文案
      const reason = (e && e.message) || "网络开小差了，评分没能完成。";
      this.setData({
        msgs: [
          ...this.data.msgs,
          this._toMsg({ role: "ai", turn_type: "error", content: reason }, true),
        ],
      });
      this._scrollBottom();
      toastApiError(e);
    } finally {
      this.setData({ thinking: false });
      this._scrollBottom();
    }
  },

  /** ---- 消息渲染工具 ---- */
  _toMsg(m, full) {
    return {
      seq: m.seq || 0,
      role: m.role,
      turnType: m.turn_type,
      content: m.content || "",
      shown: full ? m.content || "" : "",
      typing: !full && m.role === "ai",
      score: m.score,
      hit: m.points_hit || [],
      missed: m.points_missed || [],
      wrong: m.points_wrong || [],
      suggestion: m.suggestion || "",
    };
  },

  /** 打字机：AI 消息逐字渲染（30ms/2字，消息 <300 字无性能风险） */
  _pushTyping(m) {
    return new Promise((resolve) => {
      const idx = this.data.msgs.length;
      const msg = this._toMsg(m, false);
      this.setData({ msgs: [...this.data.msgs, msg], scrollInto: "" });
      this._scrollBottom();
      if (m.role !== "ai") {
        resolve();
        return;
      }
      const full = msg.content;
      let i = 0;
      const timer = setInterval(() => {
        i = Math.min(i + 2, full.length);
        const key = `msgs[${idx}].shown`;
        this.setData({ [key]: full.slice(0, i) });
        if (i % 20 === 0) this._scrollBottom();
        if (i >= full.length) {
          clearInterval(timer);
          this.setData({ [`msgs[${idx}].typing`]: false });
          this._scrollBottom();
          resolve();
        }
      }, 30);
    });
  },

  _pushSummary(summary) {
    return new Promise((resolve) => {
      this.setData({ summary: { ...summary } });
      this._scrollBottom();
      resolve();
    });
  },

  _scrollBottom() {
    // #32 修复：锚点 id 固定为 chat-anchor-a/b，值交替触发重滚（同值 scroll-into-view 不生效）
    this._anchorFlip = !this._anchorFlip;
    this.setData({ scrollInto: this._anchorFlip ? "chat-anchor-a" : "chat-anchor-b" });
  },

  _stopTyping() {
    // 页面隐藏时无需持久处理；打字 interval 在 setData 后自然失效，
    // 恢复时走 resume() 全量重放，无状态损坏。
  },

  /** 结束态操作：再来一场（直接新开，旧场保留在历史列表） */
  onRestart() {
    this.onNewChat();
  },

  /** ---- #37 语音输入：按住说话，松开即转写发送 ---- */
  _ensureRecorder() {
    if (this._recorder) return this._recorder;
    const rec = wx.getRecorderManager();
    rec.onStart(() => this.setData({ recording: true }));
    rec.onStop((res) => {
      this.setData({ recording: false });
      if (res.duration && res.duration < 600) {
        wx.showToast({ title: "说话时间太短", icon: "none" });
        return;
      }
      this._transcribe(res.tempFilePath);
    });
    rec.onError(() => {
      this.setData({ recording: false });
      wx.showToast({ title: "录音失败，请重试", icon: "none" });
    });
    this._recorder = rec;
    return rec;
  },

  onVoiceStart() {
    if (this.data.thinking || this.data.finished || this._sending) return;
    const rec = this._ensureRecorder();
    wx.authorize({
      scope: "scope.record",
      success: () => rec.start({ format: "mp3", duration: 60000, sampleRate: 16000, encodeBitRate: 96000 }),
      fail: () => {
        // 授权被拒：引导去设置页开启
        wx.showModal({
          title: "需要麦克风权限",
          content: "请在设置中允许使用麦克风，以便语音输入",
          confirmText: "去设置",
          success: (r) => {
            if (r.confirm) wx.openSetting();
          },
        });
      },
    });
  },

  onVoiceEnd() {
    if (this.data.recording) this._recorder.stop();
  },

  /** 转写 → 松开即发：识别文本直接进入发送流程 */
  async _transcribe(filePath) {
    wx.showLoading({ title: "识别中", mask: true });
    try {
      const res = await new Promise((resolve, reject) => {
        wx.uploadFile({
          url: `${"http://127.0.0.1:8000"}/api/chat/voice`,
          filePath,
          name: "file",
          header: { "X-Unionid": wx.getStorageSync("unionid") || "" },
          success: (r) => {
            const body = JSON.parse(r.data || "{}");
            if (r.statusCode >= 200 && r.statusCode < 300) resolve(body);
            else reject(new Error(body.detail || "语音识别失败"));
          },
          fail: () => reject(new Error("网络异常，语音上传失败")),
        });
      });
      wx.hideLoading();
      if (this._sending) return;
      this._sending = true;
      try {
        await this._reply({ content: res.text, local: true });
      } finally {
        this._sending = false;
      }
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: (e && e.message) || "识别失败", icon: "none" });
    }
  },

  /** #38 语音模式开关：开=按住说话 + AI 自动朗读；关=文字输出（并停止朗读/录音） */
  onToggleVoice() {
    const next = !this.data.voiceMode;
    if (!next) {
      // 关闭语音模式：停掉录音与朗读
      if (this.data.recording) this._recorder.stop();
      if (this._audio) this._audio.stop();
      this.setData({ voiceMode: false, recording: false, speakingSeq: null });
      return;
    }
    this.setData({ voiceMode: true });
    wx.showToast({ title: "语音模式已开启", icon: "none" });
  },

  /** #37/#38 朗读指定消息（onSpeak 入口 + 语音模式自动朗读共用） */
  _speak(seq, text) {
    if (!text) return;
    if (this._audio) this._audio.stop(); // 新朗读打断旧朗读
    wx.request({
      url: "http://127.0.0.1:8000/api/chat/tts",
      method: "POST",
      data: { text },
      responseType: "arraybuffer",
      header: { "X-Unionid": wx.getStorageSync("unionid") || "", "Content-Type": "application/json" },
      success: (r) => {
        if (r.statusCode !== 200) {
          if (!this.data.voiceMode) wx.showToast({ title: "朗读暂时不可用", icon: "none" });
          return;
        }
        const path = `${wx.env.USER_DATA_PATH}/tts_${seq}_${Date.now()}.mp3`;
        wx.getFileSystemManager().writeFile({
          filePath: path,
          data: r.data,
          encoding: "binary",
          success: () => {
            if (!this._audio) {
              this._audio = wx.createInnerAudioContext();
              this._audio.onEnded(() => this.setData({ speakingSeq: null }));
              this._audio.onError(() => this.setData({ speakingSeq: null }));
            }
            this._audio.src = path;
            this._audio.play();
            this.setData({ speakingSeq: seq });
          },
          fail: () => wx.showToast({ title: "播放失败", icon: "none" }),
        });
      },
      fail: () => {
        if (!this.data.voiceMode) wx.showToast({ title: "网络异常，合成失败", icon: "none" });
      },
    });
  },

  /** #37 朗读按钮入口（手动点按） */
  onSpeak(e) {
    const seq = Number(e.currentTarget.dataset.seq);
    const text = e.currentTarget.dataset.text;
    if (!text) return;
    if (this.data.speakingSeq === seq) {
      this._audio.stop();
      this.setData({ speakingSeq: null });
      return;
    }
    this._speak(seq, text);
  },
});
