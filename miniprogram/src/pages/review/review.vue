<template>
  <view class="page-wrap review">
    <view v-if="loading" class="card t-muted">正在生成复盘报告…</view>

    <template v-else-if="summary">
      <view class="card hero">
        <text class="hero-title">复盘报告</text>
        <text v-if="summary.aigc" class="tag tag-brand aigc">AI 生成内容，仅供参考</text>
      </view>

      <!-- 五维掌握度（G-02）：用横向条形雷达近似呈现 -->
      <text class="section-title">五维掌握度</text>
      <view class="card">
        <view v-for="m in summary.radar" :key="m.module" class="radar-row">
          <text class="radar-name">{{ m.module }}</text>
          <view class="radar-track">
            <view class="radar-fill" :class="m.percent < 60 ? 'low' : ''" :style="{ width: m.percent + '%' }" />
          </view>
          <text class="radar-pct">{{ m.percent }}%</text>
        </view>
      </view>

      <!-- 薄弱考点（G-03） -->
      <text class="section-title">建议优先巩固</text>
      <view class="card">
        <view v-for="w in summary.weakTop3" :key="w" class="weak-item">
          <text class="weak-dot">·</text>
          <text>{{ w }}</text>
        </view>
        <text v-if="!summary.weakTop3.length" class="t-muted">暂无薄弱考点，保持得不错</text>
      </view>

      <!-- 下一步一件事（G-04） -->
      <view class="card next">
        <text class="next-label">下一步</text>
        <text class="next-text">{{ summary.nextStep || "继续下一局闯关" }}</text>
      </view>

      <!-- 个性化段落（G-05~G-08） -->
      <view class="card para">
        <text class="para-text">{{ summary.paragraph }}</text>
        <text v-if="summary.aigc" class="para-aigc">（AI 生成，仅供参考）</text>
      </view>

      <view class="btn-row">
        <view class="btn-ghost" hover-class="btn-ghost--press" hover-stay-time="80" @click="goMistakes">去错题本</view>
        <view class="btn-primary" hover-class="btn-primary--press" hover-stay-time="80" @click="goQuest">再来一局</view>
      </view>
    </template>

    <view v-else class="card t-muted">未找到该局复盘，请返回首页</view>
  </view>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import Taro from "@tarojs/taro";
import { api, ensureIdentity, toastApiError, redirectNav } from "@/utils/api";
import { summarizeReview } from "@/utils/reviewShape";

const loading = ref(true);
const summary = ref<any>(null);

const sessionId = Number(Taro.getCurrentInstance().router?.params?.session_id || 0);

async function load() {
  await ensureIdentity();
  try {
    const data = (await api(`/api/review/${sessionId}`)) as any;
    summary.value = summarizeReview(data);
  } catch (e) {
    toastApiError(e);
    summary.value = null;
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function goMistakes() {
  redirectNav("/pages/mistakes/mistakes");
}
function goQuest() {
  redirectNav("/pages/quest/quest");
}
</script>

<style>
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.hero-title {
  font-size: var(--fs-xl);
  font-weight: 700;
  color: var(--text-1);
}
.aigc {
  flex-shrink: 0;
}
.radar-row {
  display: flex;
  align-items: center;
  margin-bottom: 18rpx;
}
.radar-name {
  width: 180rpx;
  font-size: var(--fs-xs);
  color: var(--text-2);
  flex-shrink: 0;
}
.radar-track {
  flex: 1;
  height: 16rpx;
  background: var(--bg);
  border-radius: var(--r-pill);
  overflow: hidden;
}
.radar-fill {
  height: 100%;
  background: var(--brand);
  border-radius: var(--r-pill);
}
.radar-fill.low {
  background: var(--warn);
}
.radar-pct {
  width: 80rpx;
  text-align: right;
  font-size: var(--fs-xs);
  color: var(--text-2);
  flex-shrink: 0;
}
.weak-item {
  display: flex;
  align-items: center;
  font-size: var(--fs-sm);
  color: var(--text-1);
  margin-bottom: 10rpx;
}
.weak-dot {
  color: var(--warn);
  margin-right: 10rpx;
  font-weight: 700;
}
.next-label {
  font-size: var(--fs-xs);
  color: var(--brand-dark);
  font-weight: 600;
}
.next-text {
  display: block;
  font-size: var(--fs);
  color: var(--text-1);
  margin-top: 6rpx;
}
.para-text {
  display: block;
  font-size: var(--fs-sm);
  color: var(--text-2);
  line-height: 1.7;
}
.para-aigc {
  display: block;
  font-size: var(--fs-xs);
  color: var(--text-3);
  margin-top: 8rpx;
}
.btn-row {
  display: flex;
  gap: 24rpx;
  margin-top: 32rpx;
}
.btn-ghost {
  flex: 1;
  text-align: center;
  padding: 0;
  height: 88rpx;
  line-height: 88rpx;
  border-radius: var(--r-pill);
  border: 2rpx solid var(--border);
  color: var(--text-2);
  font-size: var(--fs);
}
.btn-primary {
  flex: 1;
}
</style>
