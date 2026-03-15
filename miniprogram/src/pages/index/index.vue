<template>
  <view class="page-wrap index">
    <!-- AIGC 首次说明：仅展示一次（Implementation 28 / 用户故事 #5） -->
    <view v-if="!ack" class="aigc-bar">
      <view class="aigc-icon">AI</view>
      <view class="col aigc-body">
        <text class="aigc-title">题目由 AI 生成，仅供参考</text>
        <text class="aigc-desc">答案与解析请以官方教材为准</text>
      </view>
      <view class="aigc-btn" @click="onAck">我已知晓</view>
    </view>

    <!-- 今日进度概览 -->
    <view class="card hero">
      <view class="row-between">
        <view class="col">
          <text class="hero-label">今日已刷</text>
          <view class="row hero-num-row">
            <text class="hero-num">{{ quota.used_today }}</text>
            <text class="hero-total">/ {{ quota.free_daily_limit }} 题</text>
          </view>
        </view>
        <view v-if="quota.is_vip" class="tag tag-brand">VIP 不限量</view>
        <view v-else class="tag" :class="quota.remaining > 0 ? 'tag-brand' : 'tag-warn'">
          剩余 {{ quota.remaining }} 题
        </view>
      </view>
      <view class="progress-track">
        <view class="progress-fill" :style="{ width: progressPct + '%' }" />
      </view>
      <text class="t-dim hero-reset">{{ quota.reset_rule || "额度每日 0 点重置" }}</text>
    </view>

    <!-- 功能入口：卡片栅格（响应式两列） -->
    <text class="section-title">开始学习</text>
    <view class="entry-grid">
      <view v-for="e in entries" :key="e.title" class="entry-card" @click="e.go">
        <view class="entry-icon" :style="{ background: e.bg }">{{ e.icon }}</view>
        <text class="entry-title">{{ e.title }}</text>
        <text class="entry-desc">{{ e.desc }}</text>
      </view>
    </view>

    <text class="t-dim build-tip">脚手架就绪（票 01）· 身份就绪（票 02）</text>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import Taro from "@tarojs/taro";
import { api, ensureIdentity } from "@/utils/api";

const noticeAcked = ref(false);
const ack = ref(false);
const quota = ref<any>({ used_today: 0, free_daily_limit: 20, remaining: 20, is_vip: false, reset_rule: "" });

const progressPct = computed(() => {
  const total = quota.value.free_daily_limit || 20;
  return Math.min(100, Math.round(((quota.value.used_today || 0) / total) * 100));
});

const entries = [
  { icon: "闯", title: "进入闯关", desc: "按模块或考点发起", bg: "#e6f7f6", go: goQuest },
  { icon: "每", title: "每日任务", desc: "考期倒计时与新题", bg: "#eaf2fd", go: goDaily },
  { icon: "错", title: "错题本", desc: "按考点聚合重练", bg: "#fff4e5", go: goMistakes },
  { icon: "史", title: "历史闯关", desc: "回看往期复盘", bg: "#f3ecfd", go: goHistory },
  { icon: "权", title: "权益说明", desc: "免费额度与 VIP", bg: "#e8f8ee", go: goBenefits },
  { icon: "我", title: "我的设置", desc: "管理学习数据", bg: "#eef1f4", go: goSettings },
];

async function onAck() {
  await ensureIdentity();
  await api("/api/identity/ack-aigc", { method: "POST" });
  ack.value = true;
  noticeAcked.value = true;
}

async function load() {
  await ensureIdentity();
  try {
    quota.value = await api("/api/quota");
  } catch {
    /* 额度查询失败不阻塞首页 */
  }
}
load();

function goQuest() {
  Taro.navigateTo({ url: "/pages/quest/quest" });
}
function goDaily() {
  Taro.navigateTo({ url: "/pages/daily/daily" });
}
function goMistakes() {
  Taro.navigateTo({ url: "/pages/mistakes/mistakes" });
}
function goHistory() {
  Taro.navigateTo({ url: "/pages/history/history" });
}
function goBenefits() {
  Taro.navigateTo({ url: "/pages/benefits/benefits" });
}
function goSettings() {
  Taro.navigateTo({ url: "/pages/settings/settings" });
}
</script>

<style>
.index {
  display: block;
}

/* AIGC 说明条 */
.aigc-bar {
  display: flex;
  align-items: center;
  background: var(--brand-light);
  border-radius: var(--r-lg);
  padding: 24rpx;
  margin-bottom: var(--sp);
}
.aigc-icon {
  width: 56rpx;
  height: 56rpx;
  line-height: 56rpx;
  text-align: center;
  border-radius: var(--r-sm);
  background: var(--brand);
  color: #fff;
  font-size: var(--fs-xs);
  font-weight: 600;
  margin-right: 16rpx;
  flex-shrink: 0;
}
.aigc-body {
  flex: 1;
  min-width: 0;
}
.aigc-title {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--brand-dark);
}
.aigc-desc {
  font-size: var(--fs-xs);
  color: var(--text-2);
}
.aigc-btn {
  flex-shrink: 0;
  background: var(--brand);
  color: #fff;
  font-size: var(--fs-xs);
  padding: 10rpx 24rpx;
  border-radius: var(--r-pill);
}

/* 今日进度 */
.hero-label {
  font-size: var(--fs-sm);
  color: var(--text-2);
}
.hero-num-row {
  margin-top: 4rpx;
}
.hero-num {
  font-size: 56rpx;
  font-weight: 700;
  color: var(--brand-dark);
  line-height: 1.2;
}
.hero-total {
  font-size: var(--fs-sm);
  color: var(--text-3);
  margin-left: 8rpx;
}
.progress-track {
  height: 12rpx;
  background: var(--bg);
  border-radius: var(--r-pill);
  overflow: hidden;
  margin: 20rpx 0 12rpx;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--brand) 0%, var(--brand-dark) 100%);
  border-radius: var(--r-pill);
  transition: width 0.3s;
}
.hero-reset {
  display: block;
}

/* 入口栅格：两列自适应 */
.entry-grid {
  display: flex;
  flex-wrap: wrap;
  margin: 0 -12rpx;
}
.entry-card {
  width: calc(50% - 24rpx);
  margin: 0 12rpx 24rpx;
  background: var(--card);
  border-radius: var(--r-lg);
  padding: 28rpx 24rpx;
  box-shadow: var(--shadow-card);
}
.entry-icon {
  width: 72rpx;
  height: 72rpx;
  line-height: 72rpx;
  text-align: center;
  border-radius: var(--r-md);
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 16rpx;
}
.entry-title {
  display: block;
  font-size: var(--fs);
  font-weight: 600;
  color: var(--text-1);
}
.entry-desc {
  display: block;
  font-size: var(--fs-xs);
  color: var(--text-3);
  margin-top: 4rpx;
}
.build-tip {
  display: block;
  text-align: center;
  padding: 16rpx 0 8rpx;
}
</style>
