<template>
  <view class="page-wrap history">
    <view v-if="!loading && items.length === 0" class="empty-hint">
      <text>还没有完成的闯关局</text>
    </view>

    <template v-else>
      <text class="section-title">历史闯关局</text>
      <view v-for="s in items" :key="s.session_id" class="card hist-card" @click="openReview(s)">
        <view class="row-between">
          <text class="hist-title">#{{ s.session_id }} {{ s.module || "综合" }}</text>
          <text class="hist-rate" :class="rateClass(s)">{{ rateText(s) }}</text>
        </view>
        <text v-if="s.knowledge_point" class="t-muted hist-kp">考点《{{ s.knowledge_point }}》</text>
        <text class="t-dim hist-time">{{ fmt(s.submitted_at) }}</text>
      </view>
    </template>

    <nut-dialog
      v-if="review"
      :title="`复盘报告 #${review.session_id}`"
      :visible="showReview"
      @close="showReview = false"
    >
      <view class="review">
        <text class="line">五维掌握度：{{ fmtMastery(review.mastery) }}</text>
        <text class="line">薄弱考点：{{ review.weak_points.join("、") }}</text>
        <text class="line">{{ review.next_step }}</text>
        <text class="line dim">{{ review.paragraph }}</text>
        <text class="line dim">AI 生成内容仅供参考</text>
      </view>
    </nut-dialog>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Dialog as NutDialog } from "@nutui/nutui-taro";
import { api, ensureIdentity } from "@/utils/api";

const items = ref<any[]>([]);
const loading = ref(true);
const review = ref<any>(null);
const showReview = ref(false);

async function load() {
  await ensureIdentity();
  items.value = (await api("/api/sessions/history")) as any[];
  loading.value = false;
}
load();

/** 回看任意一局完整复盘报告：复用票 08 接口（验收 2）。 */
async function openReview(s: any) {
  review.value = await api(`/api/review/${s.session_id}`);
  showReview.value = true;
}

function rate(s: any) {
  if (!s.question_count) return 0;
  return Math.round((s.correct_count / s.question_count) * 100);
}
function rateText(s: any) {
  return `答对 ${s.correct_count}/${s.question_count}`;
}
function rateClass(s: any) {
  const r = rate(s);
  if (r >= 80) return "rate-good";
  if (r >= 60) return "rate-mid";
  return "rate-bad";
}

function fmt(iso: string | null) {
  return iso ? iso.slice(0, 16).replace("T", " ") : "-";
}

function fmtMastery(m: Record<string, number>) {
  return Object.entries(m)
    .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`)
    .join("，");
}
</script>

<style>
.hist-card:active {
  opacity: 0.85;
}
.hist-title {
  font-size: var(--fs);
  font-weight: 600;
  color: var(--text-1);
  flex: 1;
  min-width: 0;
}
.hist-rate {
  font-size: var(--fs-sm);
  font-weight: 600;
  flex-shrink: 0;
  padding: 4rpx 16rpx;
  border-radius: var(--r-pill);
}
.rate-good {
  background: var(--success-light);
  color: var(--success);
}
.rate-mid {
  background: var(--warn-light);
  color: var(--warn);
}
.rate-bad {
  background: var(--danger-light);
  color: var(--danger);
}
.hist-kp {
  display: block;
  margin-top: 10rpx;
}
.hist-time {
  display: block;
  margin-top: 6rpx;
}
.review {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.line {
  font-size: var(--fs-sm);
}
.dim {
  color: var(--text-3);
}
</style>
