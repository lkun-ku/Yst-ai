<template>
  <view class="page-wrap mistakes">
    <view v-if="!loading && groups.length === 0" class="empty-hint">
      <text>暂无错题，继续保持 👏</text>
    </view>

    <template v-else>
      <text class="section-title">错题本 · 按考点聚合</text>
      <view
        v-for="g in groups"
        :key="g.knowledge_point"
        class="card mistake-card"
        @click="repractice(g)"
      >
        <view class="row-between">
          <text class="kp-name">{{ g.knowledge_point }}</text>
          <view class="tag tag-danger">错 {{ g.wrong_count }} 次</view>
        </view>
        <view class="row-between meta-row">
          <text class="t-dim">{{ g.module || "综合" }} · {{ g.question_count }} 题</text>
          <view class="retry-btn" @click.stop="repractice(g)">重练此考点</view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import Taro from "@tarojs/taro";
import { api, ensureIdentity } from "@/utils/api";
import { useSessionStore } from "@/stores/session";

const groups = ref<any[]>([]);
const loading = ref(true);
const store = useSessionStore();

async function load() {
  await ensureIdentity();
  groups.value = (await api("/api/mistakes")) as any[];
  loading.value = false;
}
load();

/** 一键发起该考点重练：复用按考点发起闯关入口（票 03）。 */
async function repractice(g: any) {
  const data = (await api("/api/sessions/start", {
    method: "POST",
    data: { knowledge_point: g.knowledge_point, question_count: Math.max(2, g.question_count) },
  })) as any;
  store.setSession(data);
  Taro.showToast({ title: `重练《${g.knowledge_point}》${data.questions.length} 题`, icon: "success" });
  Taro.navigateTo({ url: "/pages/answer/answer" });
}
</script>

<style>
.mistake-card:active {
  opacity: 0.85;
}
.kp-name {
  font-size: var(--fs);
  font-weight: 600;
  color: var(--text-1);
  flex: 1;
  min-width: 0;
}
.meta-row {
  margin-top: 16rpx;
}
.retry-btn {
  flex-shrink: 0;
  background: var(--warn-light);
  color: var(--warn);
  font-size: var(--fs-xs);
  padding: 10rpx 24rpx;
  border-radius: var(--r-pill);
}
</style>
