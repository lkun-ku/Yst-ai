<template>
  <view class="page-wrap settings">
    <text class="section-title">数据与隐私</text>

    <view class="card setting-card" @click="onView">
      <view class="row-between">
        <view class="col">
          <text class="set-title">查看我的学习数据</text>
          <text class="set-desc">错题、每日任务、掌握度</text>
        </view>
        <text class="arrow">›</text>
      </view>
    </view>

    <view class="card setting-card danger" @click="onDelete">
      <view class="row-between">
        <view class="col">
          <text class="set-title danger-text">删除我的学习数据</text>
          <text class="set-desc">彻底清除本机账号的全部学习记录</text>
        </view>
        <text class="arrow">›</text>
      </view>
    </view>

    <text class="t-dim warn-tip">删除后不可恢复（用户故事 #6）</text>
  </view>
</template>

<script setup lang="ts">
import Taro from "@tarojs/taro";
import { api } from "@/utils/api";

function onView() {
  Taro.showToast({ title: "查看（后续票）", icon: "none" });
}

async function onDelete() {
  const res = await Taro.showModal({ title: "确认删除", content: "将清除全部学习数据，不可恢复。" });
  if (!res.confirm) return;
  await api("/api/identity/data", { method: "DELETE" });
  Taro.removeStorageSync("unionid");
  Taro.showToast({ title: "已删除", icon: "success" });
}
</script>

<style>
.setting-card:active {
  opacity: 0.85;
}
.set-title {
  font-size: var(--fs);
  font-weight: 600;
  color: var(--text-1);
}
.set-desc {
  font-size: var(--fs-xs);
  color: var(--text-3);
  margin-top: 4rpx;
}
.danger {
  border: 2rpx solid var(--danger-light);
}
.danger-text {
  color: var(--danger);
}
.arrow {
  font-size: 40rpx;
  color: var(--text-3);
  line-height: 1;
}
.warn-tip {
  display: block;
  text-align: center;
  padding: 16rpx;
}
</style>
