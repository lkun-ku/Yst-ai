<template>
  <view class="daily">
    <!-- 考期与倒计时（验收 1） -->
    <nut-cell-group title="考期倒计时">
      <nut-cell v-if="countdown !== null" :title="`距考试还有 ${countdown} 天`" :desc="examDate" />
      <view v-else class="row">
        <input v-model="examInput" class="input" placeholder="输入考期，如 2026-06-16" />
        <nut-button size="small" type="primary" @click="saveExamDate">保存</nut-button>
      </view>
    </nut-cell-group>

    <!-- 今日任务（验收 2/3） -->
    <nut-cell-group v-if="task" title="今日任务（12 小时内有效）">
      <nut-cell
        v-for="m in task.items.mistake_review"
        :key="'m' + m.question_id"
        :title="'错题复习：' + m.stem"
        :desc="`《${m.knowledge_point}》已错 ${m.wrong_count} 次`"
      />
      <nut-cell
        v-for="q in task.items.new_questions"
        :key="'n' + q.id"
        :title="'新题：' + q.stem"
        :desc="`考点《${q.knowledge_point}》`"
      />
      <nut-cell
        v-if="task.items.mistake_review.length === 0 && task.items.new_questions.length === 0"
        title="暂无任务内容"
        desc="先去闯关或导入题目吧"
      />
    </nut-cell-group>

    <nut-button v-if="task && !task.completed" type="primary" block @click="complete"
      >完成今日任务</nut-button
    >
    <text v-if="feedback" class="feedback">{{ feedback }}</text>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Cell as NutCell, CellGroup as NutCellGroup, Button as NutButton } from "@nutui/nutui-taro";
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
.daily {
  padding: 24rpx;
}
.row {
  display: flex;
  gap: 16rpx;
  padding: 16rpx;
  align-items: center;
}
.input {
  flex: 1;
  border: 1rpx solid #ddd;
  border-radius: 8rpx;
  padding: 12rpx 16rpx;
}
.feedback {
  display: block;
  color: #fa2c19;
  font-size: 26rpx;
  padding: 16rpx;
}
</style>
