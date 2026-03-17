/**
 * 请求层：wx.request 封装（原生重写版）。
 *
 * 必须复刻原 Taro 版 `miniprogram/src/utils/api.ts` 的四大机制（决策三）：
 *   1. 局域网 BASE 地址（真机预览用，D7：抽为可覆盖常量）
 *   2. 在途请求去重锁（防手抖 / 幂等，R4 / O-02 / G-09）
 *   3. 计数式全局 loading（O-07）
 *   4. 游客身份注入（ensureIdentity + X-Unionid 头）
 *
 * 相对原版的修正：
 *   - D8：原 `stableStringify` 实为 JSON.stringify，键序敏感导致去重锁可能漏判；
 *     此处实现真正的递归排序序列化。
 *   - loading 计数从 Vue ref 改为模块级计数 + 轻量订阅（原生无响应式）。
 */

import { ApiRequestError } from "./errClassify.js";

/** 手机「预览/真机调试」时 localhost 指向手机自己，必须用 dev 机局域网 IP。换 WiFi 后需同步改这里。 */
export let BASE = "http://172.20.10.2:8000";

/** 允许运行时覆盖（如按环境变量/构建期注入）。 */
export function setBase(url) {
  if (url) BASE = url;
}

/* ---------------- 机制 3：计数式全局 loading ---------------- */

let _requesting = 0;
const _subs = new Set();

export function getRequesting() {
  return _requesting;
}

/** 订阅请求计数变化，返回取消订阅函数。answer 页据此显示全屏遮罩。 */
export function onRequesting(cb) {
  _subs.add(cb);
  cb(_requesting);
  return () => _subs.delete(cb);
}

function _setRequesting(n) {
  _requesting = n < 0 ? 0 : n;
  // 同步镜像到 globalData，便于页面直接读取
  try {
    const app = getApp();
    if (app && app.globalData) app.globalData.requesting = _requesting;
  } catch (e) {
    /* getApp 在 App 实例化前不可用，忽略 */
  }
  _subs.forEach((cb) => {
    try {
      cb(_requesting);
    } catch (e) {
      /* 单个订阅者异常不影响其他订阅者 */
    }
  });
}

/* ---------------- 机制 4：游客身份 ---------------- */

/** 供 wx.uploadFile 等原生 API 手动组装请求头使用。 */
export function getUnionid() {
  try {
    return wx.getStorageSync("unionid") || null;
  } catch (e) {
    return null;
  }
}

let _identityPromise = null;

/** 游客身份：首次请求前静默获取并缓存（身份延续）。并发时复用同一 Promise。 */
export function ensureIdentity() {
  if (getUnionid()) return Promise.resolve();
  if (_identityPromise) return _identityPromise;

  _identityPromise = new Promise((resolve) => {
    wx.request({
      url: `${BASE}/api/identity/guest`,
      method: "POST",
      success: (res) => {
        const uid = res && res.data && res.data.unionid;
        if (uid) {
          try {
            wx.setStorageSync("unionid", uid);
          } catch (e) {
            /* 存储失败不阻塞 */
          }
        }
        resolve();
      },
      fail: () => resolve(), // 身份失败静默，由后续 401 兜底
    });
  });

  return _identityPromise.finally(() => {
    _identityPromise = null;
  });
}

/* ---------------- 机制 2：在途请求去重锁 ---------------- */

const inflight = new Map();

/** D8：真正的 stable stringify —— 递归排序对象键，避免键序不同导致去重锁失效。 */
export function stableStringify(v) {
  if (v === null || v === undefined) return "null";
  if (typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(stableStringify).join(",") + "]";
  const keys = Object.keys(v).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + stableStringify(v[k])).join(",") + "}";
}

/**
 * 发起请求。
 * @param {string} path 以 / 开头，如 "/api/sessions/start"
 * @param {{method?:string, data?:any, lock?:boolean, loading?:boolean}} options
 */
export function request(path, options = {}) {
  const method = options.method || "GET";
  const useLock = options.lock !== false;
  const useLoading = options.loading !== false;
  const key = `${method} ${path}${options.data !== undefined ? " " + stableStringify(options.data) : ""}`;

  if (useLock && inflight.has(key)) return inflight.get(key);

  const task = (async () => {
    await ensureIdentity();
    const unionid = getUnionid();
    if (useLoading && _requesting === 0) wx.showNavigationBarLoading();
    _setRequesting(_requesting + 1);
    try {
      const res = await new Promise((resolve, reject) => {
        wx.request({
          url: `${BASE}${path}`,
          method: method,
          data: options.data,
          header: { "X-Unionid": unionid || "" },
          success: resolve,
          fail: (e) => {
            const err = new Error((e && e.errMsg) || "网络异常");
            err.statusCode = 0;
            reject(err);
          },
        });
      });
      const status = res.statusCode || 0;
      if (status >= 200 && status < 300) return res.data;
      let detail = "";
      try {
        detail = (res.data && res.data.detail) || JSON.stringify(res.data);
      } catch (e) {
        detail = "";
      }
      throw new ApiRequestError(status, detail || `请求失败(${status})`);
    } finally {
      _setRequesting(_requesting - 1);
      if (useLoading && _requesting === 0) wx.hideNavigationBarLoading();
    }
  })();

  if (useLock) {
    inflight.set(key, task);
    task.finally(() => inflight.delete(key));
  }
  return task;
}

/** 兼容原 Taro 版命名。 */
export const api = request;

/* ---------------- R4 / O-03：统一错误兜底 ---------------- */

export function toastApiError(e) {
  const msg = (e && e.message) || "请求失败，请稍后重试";
  wx.showToast({ title: msg.length > 30 ? msg.slice(0, 30) + "…" : msg, icon: "none" });
}

/* ---------------- R11：跳转兜底（失败必须 toast，不得静默） ---------------- */

function _toastReason(r) {
  const reason = (r && r.errMsg) || "跳转失败";
  wx.showToast({ title: reason.length > 30 ? reason.slice(0, 30) + "…" : reason, icon: "none" });
}

export function navTo(url) {
  return new Promise((resolve) => {
    wx.navigateTo({ url, success: () => resolve(), fail: (r) => { _toastReason(r); resolve(); } });
  });
}

export function redirectNav(url) {
  return new Promise((resolve) => {
    wx.redirectTo({ url, success: () => resolve(), fail: (r) => { _toastReason(r); resolve(); } });
  });
}

export function reLaunchNav(url) {
  return new Promise((resolve) => {
    wx.reLaunch({ url, success: () => resolve(), fail: (r) => { _toastReason(r); resolve(); } });
  });
}
