<template>
  <view class="history">
    <nut-empty v-if="!loading && items.length === 0" description="还没有完成的闯关局" />
    <nut-cell-group v-else title="历史闯关局">
      <nut-cell
        v-for="s in items"
        :key="s.session_id"
        :title="`#${s.session_id} ${s.module || '综合'}${s.knowledge_point ? ' · ' + s.knowledge_point : ''}`"
        :desc="`提交于 ${fmt(s.submitted_at)} · 答对 ${s.correct_count}/${s.question_count}`"
        @click="openReview(s)"
      />
    </nut-cell-group>

    <nut-dialog v-if="review" :title="`复盘报告 #${review.session_id}`" :visible="showReview" @close="showReview = false">
      <view class="review">
        <text class="line">五维掌握度：{{ fmtMastery(review.mastery) }}</text>
        <text class="line">薄弱考点：{{ review.weak_points.join("、") }}</text>
        <text class="line">{{ review.next_step }}</text>
        <text class="line dim">{{ review.paragraph }}</text>
        <text class="line dim">AI 生成内容仅供参考</text>
      </view>
    </nut-dialog>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Cell as NutCell, CellGroup as NutCellGroup, Empty as NutEmpty, Dialog as NutDialog } from "@nutui/nutui-taro";
import { api, ensureIdentity } from "@/utils/api";

const items = ref<any[]>([]);
const loading = ref(true);
const review = ref<any>(null);
const showReview = ref(false);

async function load() {
  await ensureIdentity();
  items.value = (await api("/api/sessions/history")) as any[];
  loading.value = false;
}
load();

/** 回看任意一局完整复盘报告：复用票 08 接口（验收 2）。 */
async function openReview(s: any) {
  review.value = await api(`/api/review/${s.session_id}`);
  showReview.value = true;
}

function fmt(iso: string | null) {
  return iso ? iso.slice(0, 16).replace("T", " ") : "-";
}

function fmtMastery(m: Record<string, number>) {
  return Object.entries(m)
    .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`)
    .join("，");
}
</script>

<style>
.history {
  padding: 24rpx;
}
.review {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.line {
  font-size: 26rpx;
}
.dim {
  color: #999;
}
</style>
