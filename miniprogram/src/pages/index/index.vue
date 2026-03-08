<template>
  <view class="index">
    <!-- AIGC 首次说明占位：仅展示一次（Implementation 28 / 用户故事 #5） -->
    <nut-noticebar v-if="!ack" :text="noticeText" />
    <nut-button v-if="!ack" type="primary" @click="onAck">我已知晓</nut-button>

    <nut-cell title="进入闯关" description="按模块或考点发起一次闯关" @click="goQuest" />
    <nut-cell title="我的 / 设置" description="查看或删除学习数据" @click="goSettings" />
    <text class="tip">脚手架就绪（票 01）· 身份就绪（票 02）</text>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Button as NutButton, Noticebar as NutNoticebar, Cell as NutCell } from "@nutui/nutui-taro";
import Taro from "@tarojs/taro";
import { api, ensureIdentity } from "@/utils/api";

const noticeText = "题目由 AI 生成，仅供参考。点击「我已知晓」后不再提示。";
const ack = ref(false);

async function onAck() {
  await ensureIdentity();
  await api("/api/identity/ack-aigc", { method: "POST" });
  ack.value = true;
}

function goQuest() {
  Taro.showToast({ title: "闯关（票 03）", icon: "none" });
}

function goSettings() {
  Taro.navigateTo({ url: "/pages/settings/settings" });
}
</script>

<style>
.index {
  padding: 24rpx;
}
.tip {
  color: #999;
  font-size: 24rpx;
}
</style>
