<template>
  <view class="benefits">
    <nut-cell-group title="我的额度">
      <nut-cell title="今日已刷" :desc="`已用 ${q.used_today} / ${q.free_daily_limit} 题，剩余 ${q.remaining} 题`" />
      <nut-cell title="重置规则" :desc="q.reset_rule" />
    </nut-cell-group>

    <!-- VIP 与免费区别（验收 4） -->
    <nut-cell-group title="免费版 vs VIP">
      <nut-cell title="免费版" desc="每日 20 题额度 · 全部模块/考点可刷 · 复盘与错题本齐全" />
      <nut-cell title="VIP" desc="不限量刷题 · 权益与免费版一致的内容范围，仅题量不限" />
    </nut-cell-group>

    <nut-button v-if="!q.is_vip" type="primary" block @click="activate">开通 VIP（内测免费体验）</nut-button>
    <nut-cell v-else title="当前状态" desc="已是 VIP，不限量刷题中" />
    <text class="tip">内测阶段不涉及真实支付（个人主体限制）；正式版将接入合规支付。</text>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Cell as NutCell, CellGroup as NutCellGroup, Button as NutButton } from "@nutui/nutui-taro";
import Taro from "@tarojs/taro";
import { api, ensureIdentity } from "@/utils/api";

const q = ref<any>({ is_vip: false, free_daily_limit: 20, used_today: 0, remaining: 20, reset_rule: "" });

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
.benefits {
  padding: 24rpx;
}
.tip {
  display: block;
  color: #999;
  font-size: 22rpx;
  padding: 16rpx;
}
</style>
