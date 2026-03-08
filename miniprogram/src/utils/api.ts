import Taro from "@tarojs/taro";

const BASE = process.env.TARO_APP_API_BASE || "http://localhost:8000";

function getUnionid(): string | null {
  return Taro.getStorageSync("unionid") || null;
}

export async function api(path: string, opts: { method?: string; data?: any; header?: Record<string, string> } = {}) {
  const header: Record<string, string> = {
    "content-type": "application/json",
    ...(opts.header || {}),
  };
  const uid = getUnionid();
  if (uid) header["X-Unionid"] = uid;
  const res = await Taro.request({
    url: BASE + path,
    method: (opts.method as any) || "GET",
    data: opts.data,
    header,
  });
  if (res.statusCode >= 400) {
    throw new Error((res.data && (res.data as any).detail) || "request failed");
  }
  return res.data;
}

/** 游客先玩：本地无 unionid 时向后端取游客身份并缓存（票 02）。*/
export async function ensureIdentity(): Promise<string> {
  let uid = getUnionid();
  if (!uid) {
    const data = await api("/api/identity/guest", { method: "POST" });
    uid = (data as any).unionid;
    Taro.setStorageSync("unionid", uid);
  }
  return uid;
}
