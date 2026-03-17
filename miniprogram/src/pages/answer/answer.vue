<template>
  <view class="page-wrap answer">
    <!-- 顶部栏：退出拦截入口（R8 / O-01） -->
    <view class="answer-bar">
      <text class="t-strong">闯关答题</text>
      <view class="exit-btn" hover-class="exit-btn--press" @click="confirmExit">退出</view>
    </view>

    <!-- 顶部进度 -->
    <view class="card progress-card">
      <view class="row-between">
        <text class="t-strong">闯关进度</text>
        <text class="progress-num">{{ answeredCount }} / {{ questions.length }}</text>
      </view>
      <view class="progress-track">
        <view class="progress-fill" :style="{ width: progressPct + '%' }" />
      </view>
    </view>

    <!-- 题目卡片 -->
    <view v-for="(q, qi) in questions" :key="q.id" class="card qcard">
      <view class="row qhead">
        <text v-if="q.aigc_flag" class="tag tag-brand qtag">AI</text>
        <text class="qindex">第 {{ qi + 1 }} 题</text>
        <text v-if="isMultiple(q)" class="tag tag-accent qtag">多选</text>
      </view>

      <text class="stem">{{ q.stem }}</text>
      <text class="kp">考点：{{ q.knowledge_point }}</text>

      <view
        v-for="o in opts(q)"
        :key="o.key"
        class="option"
        :class="optionClass(q, o)"
        @click="choose(qi, o.key)"
      >
        <view class="opt-key" :class="optionKeyClass(q, o)">{{ o.key }}</view>
        <text class="opt-text">{{ o.text }}</text>
        <text v-if="revealed[q.id] && correctSet(q).has(o.key)" class="opt-mark">✓</text>
        <text v-else-if="revealed[q.id] && (answers[q.id] || []).includes(o.key)" class="opt-mark wrong"
          >✕</text
        >
      </view>

      <!-- 解析区 -->
      <view v-if="revealed[q.id]" class="explain">
        <view class="explain-head">
          <text class="explain-label">解析</text>
          <text v-if="isMultiple(q)" class="state" :class="isCorrect(q) ? 'state-ok' : 'state-bad'">{{
            stateText(q)
          }}</text>
          <text v-else class="state" :class="isCorrect(q) ? 'state-ok' : 'state-bad'">{{
            isCorrect(q) ? "回答正确" : "回答错误"
          }}</text>
        </view>
        <text class="explain-text">{{ q.explanation }}</text>
        <text class="report" hover-class="press" hover-stay-time="80" @click="reportError(q)">题目有误？报错</text>
      </view>
    </view>

    <view class="submit-wrap">
      <view class="btn-primary" hover-class="btn-primary--press" hover-stay-time="80" @click="submitAll">交卷并查看复盘</view>
    </view>

    <!-- F-05：提交中全局遮罩，防止重复提交（与 api 去重锁配合） -->
    <view v-if="requesting > 0" class="loading-mask">
      <view class="loading-spinner" />
      <text class="loading-text">提交中…</text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from "vue";
import Taro from "@tarojs/taro";
import { useSessionStore } from "@/stores/session";
import { api, requesting, toastApiError, redirectNav } from "@/utils/api";
import { optionState } from "@/utils/answerState";

const DRAFT_KEY = "quest_draft";
const store = useSessionStore();

const questions = ref<any[]>([]);
const answers = reactive<Record<number, string[]>>({});
const revealed = reactive<Record<number, boolean>>({});

const progressPct = computed(() => {
  if (!questions.value.length) return 0;
  return Math.round((answeredCount.value / questions.value.length) * 100);
});

function opts(q: any) {
  return JSON.parse(q.options);
}
function isMultiple(q: any) {
  return q.type === "multiple";
}
function correctSet(q: any): Set<string> {
  return new Set(JSON.parse(q.answer));
}
function setsEqual(a: Set<string>, b: Set<string>) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}
function isCorrect(q: any) {
  return setsEqual(new Set(answers[q.id] || []), correctSet(q));
}

function persist() {
  Taro.setStorageSync(DRAFT_KEY, {
    sessionId: store.sessionId,
    questions: questions.value,
    answers: { ...answers },
    revealed: { ...revealed },
  });
}

function choose(qi: number, key: string) {
  const q = questions.value[qi];
  if (revealed[q.id]) return; // 选中即揭示，一次性判定不再改
  if (isMultiple(q)) {
    const cur = answers[q.id] || [];
    answers[q.id] = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key];
  } else {
    answers[q.id] = [key];
  }
  revealed[q.id] = true; // 即时反馈（Implementation 23）
  persist(); // 本地持久化未提交局（票 07）
}

function optionClass(q: any, o: any) {
  const st = optionState({
    revealed: !!revealed[q.id],
    selected: answers[q.id] || [],
    correct: [...correctSet(q)],
    key: o.key,
  });
  return st === "idle" ? "" : `opt-${st}`; // opt-correct / opt-wrong / opt-missed / opt-dim
}

function optionKeyClass(q: any, o: any) {
  const st = optionState({
    revealed: !!revealed[q.id],
    selected: answers[q.id] || [],
    correct: [...correctSet(q)],
    key: o.key,
  });
  return st === "idle" ? "" : `key-${st}`;
}

function stateText(q: any) {
  if (isCorrect(q)) return "全对";
  const sel = new Set(answers[q.id] || []);
  const cor = correctSet(q);
  if (cor.size > 1 && sel.size > 0) return sel.size === cor.size ? "全对" : "部分对（漏选/错选）";
  return "错误";
}

const answeredCount = computed(() => questions.value.filter((q) => revealed[q.id]).length);

async function submitAll() {
  try {
    const payload = {
      session_id: store.sessionId,
      answers: questions.value.map((q) => ({ question_id: q.id, selected: answers[q.id] || [] })),
    };
    const data = (await api("/api/sessions/submit", { method: "POST", data: payload })) as any;
    const right = data.results.filter((r: any) => r.is_correct).length;
    Taro.removeStorageSync(DRAFT_KEY); // 提交后清除草稿（Implementation 6）
    Taro.showToast({ title: `答对 ${right}/${questions.value.length}`, icon: "none" });
    // R1 / G-01：提交后跳转复盘报告页（redirectTo 避免页面栈过深）
    redirectNav(`/pages/review/review?session_id=${store.sessionId}`);
  } catch (e) {
    // F-04：提交失败兜底；去重锁释放后用户可重试
    toastApiError(e);
  }
}

/** 题目纠错：进入审校队列（票 13）。 */
async function reportError(q: any) {
  try {
    await api("/api/reports", {
      method: "POST",
      data: { question_id: q.id, error_type: "explanation", detail: `题目《${q.stem}》疑似有误，请审校。` },
    });
    Taro.showToast({ title: "已提交纠错，感谢反馈", icon: "success" });
  } catch (e) {
    toastApiError(e);
  }
}

/** R8 / O-01：退出二次确认，避免误触返回丢失当前进度。 */
function confirmExit() {
  const unanswered = questions.value.filter((q) => !(answers[q.id] || []).length).length;
  const tip = unanswered > 0 ? `还有 ${unanswered} 题未作答，退出后进度已自动保存为草稿。` : "进度已自动保存为草稿。";
  Taro.showModal({
    title: "退出闯关？",
    content: `${tip}确定退出吗？`,
    confirmText: "退出",
    cancelText: "继续答题",
    confirmColor: "#e54d42",
  }).then((r) => {
    if (r.confirm) Taro.navigateBack();
  });
}

onMounted(() => {
  const d = Taro.getStorageSync(DRAFT_KEY);
  if (d && d.sessionId === store.sessionId && d.questions?.length) {
    questions.value = d.questions;
    Object.assign(answers, d.answers || {});
    Object.assign(revealed, d.revealed || {});
  } else if (store.questions?.length) {
    questions.value = store.questions;
  }
  persist();
  // 生产态最佳努力：微信系统返回前弹确认（H5/其他端无此 API，自动跳过）
  const g: any = typeof globalThis !== "undefined" ? globalThis : ({} as any);
  const wxAny = (Taro as any).getCurrentInstance?.()?.mini?.$scope?.enableAlertBeforeUnload
    || (typeof g.wx !== "undefined" && g.wx?.enableAlertBeforeUnload);
  if (typeof wxAny === "function") {
    wxAny({ message: "当前进度已保存为草稿，确定退出？" });
  }
});
</script>

<style>
.progress-card {
  padding: 24rpx;
}
.progress-num {
  font-size: var(--fs-sm);
  color: var(--brand-dark);
  font-weight: 600;
}
.progress-track {
  height: 12rpx;
  background: var(--bg);
  border-radius: var(--r-pill);
  overflow: hidden;
  margin-top: 16rpx;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--brand) 0%, var(--brand-dark) 100%);
  border-radius: var(--r-pill);
  transition: width 0.3s;
}

/* 题目卡 */
.qhead {
  margin-bottom: 12rpx;
}
.qtag {
  margin-right: 8rpx;
}
.qindex {
  font-size: var(--fs-xs);
  color: var(--text-3);
  flex: 1;
}
.stem {
  display: block;
  font-size: 30rpx;
  font-weight: 600;
  color: var(--text-1);
  line-height: 1.7;
}
.kp {
  display: block;
  color: var(--text-3);
  font-size: var(--fs-xs);
  margin: 12rpx 0 20rpx;
}

/* 选项 */
.option {
  display: flex;
  align-items: center;
  padding: 20rpx;
  border: 2rpx solid var(--border);
  border-radius: var(--r-md);
  margin-bottom: 16rpx;
}
.opt-key {
  width: 48rpx;
  height: 48rpx;
  line-height: 44rpx;
  text-align: center;
  border-radius: 50%;
  border: 2rpx solid var(--border);
  font-size: var(--fs-sm);
  color: var(--text-2);
  margin-right: 16rpx;
  flex-shrink: 0;
}
.opt-text {
  flex: 1;
  font-size: var(--fs);
  color: var(--text-1);
  min-width: 0;
}
.opt-mark {
  font-size: var(--fs);
  color: var(--success);
  margin-left: 12rpx;
  flex-shrink: 0;
}
.opt-mark.wrong {
  color: var(--danger);
}

.opt-correct {
  background: var(--success-light);
  border-color: var(--success);
}
.key-correct {
  background: var(--success);
  border-color: var(--success);
  color: #fff;
}
.opt-wrong {
  background: var(--danger-light);
  border-color: var(--danger);
}
.key-wrong {
  background: var(--danger);
  border-color: var(--danger);
  color: #fff;
}
/* 漏选态（R3/E-02）：未选但正确——橙色虚线边框，与已选正确(绿)明确区分 */
.opt-missed {
  background: var(--warn-light);
  border-color: var(--warn);
  border-style: dashed;
}
.key-missed {
  background: var(--warn);
  border-color: var(--warn);
  border-style: dashed;
  color: #fff;
}
.opt-dim {
  opacity: 0.5;
}
.key-dim {
  opacity: 0.6;
}

/* 解析区 */
.explain {
  margin-top: 20rpx;
  padding: 20rpx;
  background: var(--bg);
  border-radius: var(--r-md);
}
.explain-head {
  display: flex;
  align-items: center;
  margin-bottom: 8rpx;
}
.explain-label {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--brand-dark);
  margin-right: 12rpx;
}
.state {
  font-size: var(--fs-xs);
  padding: 2rpx 14rpx;
  border-radius: var(--r-pill);
}
.state-ok {
  background: var(--success-light);
  color: var(--success);
}
.state-bad {
  background: var(--warn-light);
  color: var(--warn);
}
.explain-text {
  display: block;
  font-size: var(--fs-sm);
  color: var(--text-2);
  line-height: 1.7;
}
.report {
  display: inline-block;
  margin-top: 14rpx;
  color: var(--accent);
  font-size: var(--fs-xs);
}

.submit-wrap {
  margin: 32rpx 0 8rpx;
}

/* 顶部栏 + 退出（R8 / O-01） */
.answer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp);
}
.exit-btn {
  font-size: var(--fs-xs);
  color: var(--text-2);
  padding: 8rpx 24rpx;
  border: 2rpx solid var(--border);
  border-radius: var(--r-pill);
}
.exit-btn--press {
  background: var(--bg);
  opacity: 0.85;
}

/* F-05 提交遮罩 */
.loading-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 99;
}
.loading-spinner {
  width: 64rpx;
  height: 64rpx;
  border: 6rpx solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
.loading-text {
  color: #fff;
  font-size: var(--fs-sm);
  margin-top: 20rpx;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
