<template>
  <view class="settings">
    <nut-cell-group title="数据与隐私">
      <nut-cell title="查看我的学习数据" description="错题、每日任务、掌握度" @click="onView" />
      <nut-cell title="删除我的学习数据" description="彻底清除本机账号的全部学习记录" @click="onDelete" />
    </nut-cell-group>
    <text class="tip">删除后不可恢复（用户故事 #6）</text>
  </view>
</template>

<script setup lang="ts">
import { Cell as NutCell, CellGroup as NutCellGroup } from "@nutui/nutui-taro";
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
.settings {
  padding: 24rpx;
}
.tip {
  color: #999;
  font-size: 24rpx;
  padding: 16rpx;
}
</style>
