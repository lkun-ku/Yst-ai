<template>
  <view class="page-wrap benefits">
    <!-- 当前额度 -->
    <view class="card">
      <view class="row-between">
        <text class="card-title">今日额度</text>
        <view v-if="q.is_vip" class="tag tag-brand">VIP</view>
        <view v-else class="tag tag-accent">免费版</view>
      </view>
      <view class="row quota-row">
        <text class="quota-num">{{ q.used_today }}</text>
        <text class="quota-total">/ {{ q.free_daily_limit }} 题</text>
      </view>
      <view class="progress-track">
        <view class="progress-fill" :style="{ width: progressPct + '%' }" />
      </view>
      <text class="t-dim">{{ q.reset_rule || "额度每日 0 点重置" }}</text>
    </view>

    <!-- 免费 vs VIP -->
    <text class="section-title">免费版 vs VIP</text>
    <view class="card plan-card">
      <view class="row-between plan-head">
        <text class="plan-name">免费版</text>
        <text class="plan-price">每日 20 题</text>
      </view>
      <view v-for="f in freeFeatures" :key="f" class="row feat">
        <text class="feat-dot free">·</text>
        <text class="feat-text">{{ f }}</text>
      </view>
    </view>

    <view class="card plan-card vip">
      <view class="row-between plan-head">
        <text class="plan-name">VIP</text>
        <text class="plan-price vip-price">不限量</text>
      </view>
      <view v-for="f in vipFeatures" :key="f" class="row feat">
        <text class="feat-dot">·</text>
        <text class="feat-text">{{ f }}</text>
      </view>
    </view>

    <view v-if="!q.is_vip" class="btn-primary" @click="activate">开通 VIP（内测免费体验）</view>
    <view v-else class="card done-card">
      <text class="done-text">已是 VIP，不限量刷题中</text>
    </view>

    <text class="t-dim pay-tip">内测阶段不涉及真实支付（个人主体限制）；正式版将接入合规支付。</text>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import Taro from "@tarojs/taro";
import { api, ensureIdentity } from "@/utils/api";

const q = ref<any>({ is_vip: false, free_daily_limit: 20, used_today: 0, remaining: 20, reset_rule: "" });

const progressPct = computed(() => {
  const total = q.value.free_daily_limit || 20;
  return Math.min(100, Math.round(((q.value.used_today || 0) / total) * 100));
});

const freeFeatures = ["每日 20 题额度", "全部模块与考点可刷", "复盘报告与掌握度", "错题本与专项重练"];
const vipFeatures = ["不限量刷题", "内容与免费版完全一致", "复盘报告与掌握度", "错题本与专项重练"];

async function load() {
  await ensureIdentity();
  q.value = await api("/api/quota");
}
load();

async function activate() {
  const r = (await api("/api/quota/vip-activate", { method: "POST" })) as any;
  q.value.is_vip = r.is_vip;
  Taro.showToast({ title: "已开通 VIP", icon: "success" });
}
</script>

<style>
.quota-row {
  margin: 8rpx 0 16rpx;
}
.quota-num {
  font-size: 56rpx;
  font-weight: 700;
  color: var(--brand-dark);
  line-height: 1.2;
}
.quota-total {
  font-size: var(--fs-sm);
  color: var(--text-3);
  margin-left: 8rpx;
}
.progress-track {
  height: 12rpx;
  background: var(--bg);
  border-radius: var(--r-pill);
  overflow: hidden;
  margin-bottom: 12rpx;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--brand) 0%, var(--brand-dark) 100%);
  border-radius: var(--r-pill);
}

.plan-card {
  border: 2rpx solid var(--border);
}
.plan-card.vip {
  border-color: var(--brand);
  background: var(--brand-light);
}
.plan-head {
  margin-bottom: 16rpx;
}
.plan-name {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text-1);
}
.plan-price {
  font-size: var(--fs-sm);
  color: var(--text-2);
}
.vip-price {
  color: var(--brand-dark);
  font-weight: 600;
}
.feat {
  margin-bottom: 10rpx;
}
.feat-dot {
  color: var(--brand);
  font-size: var(--fs-lg);
  margin-right: 12rpx;
}
.feat-dot.free {
  color: var(--text-3);
}
.feat-text {
  font-size: var(--fs-sm);
  color: var(--text-2);
  flex: 1;
}
.done-card {
  text-align: center;
}
.done-text {
  font-size: var(--fs);
  font-weight: 600;
  color: var(--brand-dark);
}
.pay-tip {
  display: block;
  text-align: center;
  padding: 24rpx 16rpx 8rpx;
}
</style>
