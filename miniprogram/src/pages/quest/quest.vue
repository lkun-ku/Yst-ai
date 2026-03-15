<template>
  <view class="page-wrap quest">
    <text class="section-title">选择模块发起闯关</text>

    <view class="mod-grid">
      <view
        v-for="m in modules"
        :key="m.name"
        class="mod-card"
        :style="{ background: m.bg }"
        @click="start(m.name)"
      >
        <view class="mod-icon">{{ m.icon }}</view>
        <text class="mod-name">{{ m.name }}</text>
        <text class="mod-desc">{{ m.desc }}</text>
      </view>
    </view>

    <view v-if="store.sessionId" class="card session-tip">
      <view class="row-between">
        <view class="col">
          <text class="t-strong">已创建闯关局 #{{ store.sessionId }}</text>
          <text class="t-muted">题目 {{ store.questions.length }} 道</text>
        </view>
        <view class="go-btn" @click="goAnswer">继续答题</view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import Taro from "@tarojs/taro";
import { useSessionStore } from "@/stores/session";
import { api, ensureIdentity } from "@/utils/api";

const modules = [
  { name: "职业理念", icon: "念", desc: "教育观学生观教师观", bg: "#e6f7f6" },
  { name: "职业道德", icon: "德", desc: "教师职业道德规范", bg: "#eaf2fd" },
  { name: "教育法律法规", icon: "法", desc: "教育法教师法未保法", bg: "#fff4e5" },
  { name: "文化素养", icon: "文", desc: "常识历史科技", bg: "#f3ecfd" },
  { name: "基本能力", icon: "能", desc: "阅读理解逻辑写作", bg: "#e8f8ee" },
];

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

function goAnswer() {
  Taro.navigateTo({ url: "/pages/answer/answer" });
}
</script>

<style>
.mod-grid {
  display: flex;
  flex-wrap: wrap;
  margin: 0 -12rpx;
}
.mod-card {
  width: calc(50% - 24rpx);
  margin: 0 12rpx 24rpx;
  border-radius: var(--r-lg);
  padding: 28rpx 24rpx;
  box-shadow: var(--shadow-card);
}
.mod-icon {
  width: 72rpx;
  height: 72rpx;
  line-height: 72rpx;
  text-align: center;
  border-radius: var(--r-md);
  background: rgba(255, 255, 255, 0.7);
  font-size: var(--fs-lg);
  font-weight: 600;
  margin-bottom: 16rpx;
}
.mod-name {
  display: block;
  font-size: var(--fs);
  font-weight: 600;
  color: var(--text-1);
}
.mod-desc {
  display: block;
  font-size: var(--fs-xs);
  color: var(--text-2);
  margin-top: 4rpx;
}
.session-tip {
  margin-top: 8rpx;
}
.go-btn {
  flex-shrink: 0;
  background: var(--brand);
  color: #fff;
  font-size: var(--fs-xs);
  padding: 12rpx 28rpx;
  border-radius: var(--r-pill);
}
</style>
