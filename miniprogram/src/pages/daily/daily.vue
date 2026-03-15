<template>
  <view class="page-wrap daily">
    <!-- 考期倒计时 -->
    <view class="card countdown-card">
      <view v-if="countdown !== null" class="col cd-body">
        <text class="cd-label">距考试还有</text>
        <view class="row cd-num-row">
          <text class="cd-num">{{ countdown }}</text>
          <text class="cd-unit">天</text>
        </view>
        <text class="t-dim">考期 {{ examDate }}</text>
      </view>
      <view v-else class="col">
        <text class="card-title">设置考期</text>
        <text class="card-sub">输入考试日期，开启倒计时</text>
        <view class="row input-row">
          <input v-model="examInput" class="field" placeholder="如 2026-06-16" />
          <view class="save-btn" @click="saveExamDate">保存</view>
        </view>
      </view>
    </view>

    <!-- 今日任务 -->
    <template v-if="task">
      <text class="section-title">今日任务 · 12 小时内有效</text>

      <view
        v-for="m in task.items.mistake_review"
        :key="'m' + m.question_id"
        class="card task-card"
      >
        <view class="row-between">
          <view class="tag tag-warn">错题复习</view>
          <text class="t-dim">已错 {{ m.wrong_count }} 次</text>
        </view>
        <text class="task-stem">{{ m.stem }}</text>
        <text class="t-dim">考点《{{ m.knowledge_point }}》</text>
      </view>

      <view v-for="q in task.items.new_questions" :key="'n' + q.id" class="card task-card">
        <view class="tag tag-brand">新题</view>
        <text class="task-stem">{{ q.stem }}</text>
        <text class="t-dim">考点《{{ q.knowledge_point }}》</text>
      </view>

      <view
        v-if="task.items.mistake_review.length === 0 && task.items.new_questions.length === 0"
        class="empty-hint"
      >
        <text>暂无任务内容，先去闯关或导入题目吧</text>
      </view>

      <view v-if="task.completed" class="card done-card">
        <text class="done-text">今日任务已完成 🎉</text>
        <text v-if="feedback" class="t-muted">{{ feedback }}</text>
      </view>
      <view v-else class="btn-primary" @click="complete">完成今日任务</view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import Taro from "@tarojs/taro";
import { api, ensureIdentity } from "@/utils/api";

const examDate = ref<string | null>(null);
const examInput = ref("");
const countdown = ref<number | null>(null);
const task = ref<any>(null);
const feedback = ref("");

async function load() {
  await ensureIdentity();
  const d = (await api("/api/daily")) as any;
  examDate.value = d.exam_date;
  countdown.value = d.countdown_days;
  task.value = d.task;
}
load();

async function saveExamDate() {
  const r = (await api("/api/daily/exam-date", {
    method: "POST",
    data: { exam_date: examInput.value.trim() },
  })) as any;
  examDate.value = r.exam_date;
  countdown.value = r.countdown_days;
  Taro.showToast({ title: `已保存，倒计时 ${r.countdown_days} 天`, icon: "success" });
}

/** 完成反馈（验收 4）；超时由后端拦截（验收 3）。 */
async function complete() {
  try {
    const r = (await api("/api/daily/complete", {
      method: "POST",
      data: { task_id: task.value.task_id },
    })) as any;
    task.value.completed = true;
    feedback.value = r.feedback;
    Taro.showToast({ title: r.feedback, icon: "success", duration: 2500 });
  } catch (e: any) {
    Taro.showToast({ title: e.message || "任务已超时", icon: "none" });
  }
}
</script>

<style>
.countdown-card {
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
  box-shadow: var(--shadow-brand);
}
.cd-body {
  align-items: center;
}
.cd-label {
  font-size: var(--fs-sm);
  color: rgba(255, 255, 255, 0.85);
}
.cd-num-row {
  margin: 4rpx 0 8rpx;
}
.cd-num {
  font-size: 80rpx;
  font-weight: 700;
  color: #fff;
  line-height: 1.1;
}
.cd-unit {
  font-size: var(--fs);
  color: rgba(255, 255, 255, 0.9);
  margin-left: 8rpx;
}
.countdown-card .t-dim {
  color: rgba(255, 255, 255, 0.8);
}
.countdown-card .card-title,
.countdown-card .card-sub {
  color: #fff;
}
.input-row {
  margin-top: 20rpx;
  gap: 16rpx;
}
.save-btn {
  flex-shrink: 0;
  background: #fff;
  color: var(--brand-dark);
  font-size: var(--fs-sm);
  font-weight: 600;
  padding: 18rpx 32rpx;
  border-radius: var(--r-pill);
}

.task-card:active {
  opacity: 0.85;
}
.task-stem {
  display: block;
  font-size: var(--fs);
  color: var(--text-1);
  line-height: 1.6;
  margin: 14rpx 0 8rpx;
}
.done-card {
  align-items: center;
}
.done-text {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--success);
  text-align: center;
}
</style>
