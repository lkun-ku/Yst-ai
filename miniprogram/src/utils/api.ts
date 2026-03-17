import Taro from "@tarojs/taro";
import { ref } from "vue";
import { ApiRequestError } from "./errClassify";

const BASE = "http://localhost:8000";

/** R4 / O-07：全局请求中计数，供页面订阅显示 loading。 */
export const requesting = ref(0);

function getUnionid(): string | null {
  return Taro.getStorageSync("unionid") || null;
}

/** 游客身份：首次请求前静默获取并缓存（身份延续）。 */
export async function ensureIdentity(): Promise<void> {
  if (getUnionid()) return;
  const res = await Taro.request({ url: `${BASE}/api/identity/guest`, method: "POST" });
  const uid = res.data?.unionid;
  if (uid) Taro.setStorageSync("unionid", uid);
}

/** 在途请求去重锁：相同 key 复用同一 Promise（防手抖/幂等，R4 / O-02 / G-09）。 */
const inflight = new Map<string, Promise<any>>();

export interface ApiOptions {
  method?: "GET" | "POST" | "DELETE" | "PUT";
  data?: any;
  /** 默认 true：相同请求在途时复用，防止重复点击与幂等冲突 */
  lock?: boolean;
  /** 默认 true：计入全局 loading 态（O-07） */
  loading?: boolean;
}

export async function api(path: string, options: ApiOptions = {}): Promise<any> {
  const method = options.method || "GET";
  const useLock = options.lock !== false;
  const useLoading = options.loading !== false;
  const key = `${method} ${path}${options.data ? " " + stableStringify(options.data) : ""}`;

  if (useLock && inflight.has(key)) return inflight.get(key)!;

  const task = (async () => {
    await ensureIdentity();
    const unionid = getUnionid();
    if (useLoading && requesting.value === 0) Taro.showNavigationBarLoading();
    requesting.value++;
    try {
      const res = await Taro.request({
        url: `${BASE}${path}`,
        method: method as any,
        data: options.data,
        header: { "X-Unionid": unionid || "" },
      });
      if (res.statusCode >= 200 && res.statusCode < 300) return res.data;
      let detail = "";
      try {
        detail = res.data?.detail || JSON.stringify(res.data);
      } catch {
        detail = "";
      }
      throw new ApiRequestError(res.statusCode, detail || `请求失败(${res.statusCode})`);
    } finally {
      requesting.value = Math.max(0, requesting.value - 1);
      if (useLoading && requesting.value === 0) Taro.hideNavigationBarLoading();
    }
  })();

  if (useLock) {
    inflight.set(key, task);
    task.finally(() => inflight.delete(key));
  }
  return task;
}

function stableStringify(v: any): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/**
 * R4 / O-03：统一错误兜底。
 * 展示后端 detail 或分类后的友好文案，并暴露 kind/action 供页面决定跳转。
 */
export function toastApiError(e: any): void {
  const msg = e instanceof ApiRequestError ? e.message : e?.message || "请求失败，请稍后重试";
  Taro.showToast({ title: msg.length > 30 ? msg.slice(0, 30) + "…" : msg, icon: "none" });
}

/**
 * R11：统一页面跳转，防止 navigateTo 静默失败（曾导致"点了没反应又无报错"）。
 * 失败时 toast 提示 errMsg，便于定位（页面未注册/页面栈超限等）。
 */
export function navTo(url: string): Promise<void> {
  return new Promise((resolve) => {
    Taro.navigateTo({
      url,
      success: () => resolve(),
      fail: (r) => {
        const reason = r?.errMsg || "跳转失败";
        Taro.showToast({ title: reason.length > 30 ? reason.slice(0, 30) + "…" : reason, icon: "none" });
        resolve();
      },
    });
  });
}

/** R11：redirectTo 同款兜底（复盘页→再来一局/去错题本，避免页面栈过深）。 */
export function redirectNav(url: string): Promise<void> {
  return new Promise((resolve) => {
    Taro.redirectTo({
      url,
      success: () => resolve(),
      fail: (r) => {
        const reason = r?.errMsg || "跳转失败";
        Taro.showToast({ title: reason.length > 30 ? reason.slice(0, 30) + "…" : reason, icon: "none" });
        resolve();
      },
    });
  });
}
