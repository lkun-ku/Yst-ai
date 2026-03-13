<template>
  <view class="answer">
    <view class="progress">已答 {{ answeredCount }} / {{ questions.length }} 题</view>

    <view v-for="(q, qi) in questions" :key="q.id" class="qcard">
      <view class="stem">
        <text v-if="q.aigc_flag" class="aigc">AI</text>
        <text>{{ q.stem }}</text>
      </view>
      <view class="kp">考点：{{ q.knowledge_point }}</view>

      <view
        v-for="o in opts(q)"
        :key="o.key"
        class="option"
        :class="optionClass(q, o)"
        @click="choose(qi, o.key)"
      >
        {{ o.key }}. {{ o.text }}
      </view>

      <view v-if="revealed[q.id]" class="explain">
        <text>解析：{{ q.explanation }}</text>
        <text v-if="isMultiple(q)" class="state">本题状态：{{ stateText(q) }}</text>
        <text class="report" @click="reportError(q)">题目有误？报错</text>
      </view>
    </view>

    <nut-button type="primary" block @click="submitAll">交卷</nut-button>
  </view>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from "vue";
import { Button as NutButton } from "@nutui/nutui-taro";
import { useSessionStore } from "@/stores/session";
import { api } from "@/utils/api";
import Taro from "@tarojs/taro";

const DRAFT_KEY = "quest_draft";
const store = useSessionStore();

const questions = ref<any[]>([]);
const answers = reactive<Record<number, string[]>>({});
const revealed = reactive<Record<number, boolean>>({});

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
  if (!revealed[q.id]) return "";
  const correct = correctSet(q).has(o.key);
  const sel = (answers[q.id] || []).includes(o.key);
  if (correct) return "opt-correct";
  if (sel) return "opt-wrong";
  return "opt-dim";
}

function stateText(q: any) {
  if (isCorrect(q)) return "全对";
  const sel = new Set(answers[q.id] || []);
  const cor = correctSet(q);
  if (cor.size > 1 && sel.size > 0) return sel.size === cor.size ? "全对" : "部分对（漏选/错选）";
  return "错误";
}

function answeredCount() {
  return questions.value.filter((q) => revealed[q.id]).length;
}

async function submitAll() {
  const payload = {
    session_id: store.sessionId,
    answers: questions.value.map((q) => ({ question_id: q.id, selected: answers[q.id] || [] })),
  };
  const data = (await api("/api/sessions/submit", { method: "POST", data: payload })) as any;
  const right = data.results.filter((r: any) => r.is_correct).length;
  Taro.removeStorageSync(DRAFT_KEY); // 提交后清除草稿（Implementation 6）
  Taro.showToast({ title: `答对 ${right}/${questions.value.length}`, icon: "none" });
}

/** 题目纠错：进入审校队列（票 13）。 */
async function reportError(q: any) {
  try {
    await api("/api/reports", {
      method: "POST",
      data: { question_id: q.id, error_type: "explanation", detail: `题目《${q.stem}》疑似有误，请审校。` },
    });
    Taro.showToast({ title: "已提交纠错，感谢反馈", icon: "success" });
  } catch (e: any) {
    Taro.showToast({ title: e.message || "提交失败", icon: "none" });
  }
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
});
</script>

<style>
.answer {
  padding: 24rpx;
}
.progress {
  color: #666;
  margin-bottom: 12rpx;
}
.qcard {
  background: #fff;
  border-radius: 12rpx;
  padding: 20rpx;
  margin-bottom: 20rpx;
}
.stem {
  font-size: 30rpx;
  font-weight: 600;
}
.aigc {
  display: inline-block;
  background: #eee;
  color: #999;
  font-size: 20rpx;
  padding: 0 8rpx;
  border-radius: 6rpx;
  margin-right: 8rpx;
}
.kp {
  color: #999;
  font-size: 22rpx;
  margin: 8rpx 0;
}
.option {
  padding: 16rpx;
  border: 1rpx solid #eee;
  border-radius: 8rpx;
  margin-top: 10rpx;
}
.opt-correct {
  background: #e8f8ee;
  border-color: #52c41a;
}
.opt-wrong {
  background: #fff1f0;
  border-color: #ff4d4f;
}
.opt-dim {
  opacity: 0.5;
}
.explain {
  margin-top: 12rpx;
  color: #555;
  font-size: 24rpx;
}
.state {
  display: block;
  margin-top: 6rpx;
  color: #fa8c16;
}
.report {
  display: inline-block;
  margin-top: 10rpx;
  color: #1890ff;
  font-size: 22rpx;
}
</style>
