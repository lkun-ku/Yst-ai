<template>
  <view class="quest">
    <nut-cell-group title="选择模块发起闯关">
      <nut-cell v-for="m in modules" :key="m" :title="m" @click="start(m)" />
    </nut-cell-group>
    <text v-if="store.sessionId" class="tip"
      >已创建闯关局 #{{ store.sessionId }}，题目 {{ store.questions.length }} 道（答题见票 05/06）</text
    >
  </view>
</template>

<script setup lang="ts">
import { Cell as NutCell, CellGroup as NutCellGroup } from "@nutui/nutui-taro";
import { useSessionStore } from "@/stores/session";
import { api, ensureIdentity } from "@/utils/api";
import Taro from "@tarojs/taro";

const modules = ["职业理念", "职业道德", "教育法律法规", "文化素养", "基本能力"];
const store = useSessionStore();

async function start(module: string) {
  await ensureIdentity();
  const data = (await api("/api/sessions/start", {
    method: "POST",
    data: { module, question_count: 10 },
  })) as any;
  store.setSession(data);
  Taro.showToast({ title: `已拉取 ${data.questions.length} 题`, icon: "success" });
  Taro.navigateTo({ url: "/pages/answer/answer" });
}
</script>

<style>
.quest {
  padding: 24rpx;
}
.tip {
  color: #999;
  font-size: 24rpx;
  padding: 16rpx;
}
</style>
