<template>
  <view class="mistakes">
    <nut-empty v-if="!loading && groups.length === 0" description="暂无错题，继续保持" />
    <nut-cell-group v-else title="错题本（按考点聚合）">
      <nut-cell
        v-for="g in groups"
        :key="g.knowledge_point"
        :title="g.knowledge_point"
        :desc="`错次 ${g.wrong_count} · ${g.question_count} 题 · ${g.module || ''}`"
        @click="repractice(g)"
      >
        <template v-slot:link>
          <nut-button size="small" type="warning" @click.stop="repractice(g)">重练此考点</nut-button>
        </template>
      </nut-cell>
    </nut-cell-group>
  </view>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Cell as NutCell, CellGroup as NutCellGroup, Button as NutButton, Empty as NutEmpty } from "@nutui/nutui-taro";
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
.mistakes {
  padding: 24rpx;
}
</style>
