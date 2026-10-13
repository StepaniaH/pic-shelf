import { t, getLocale } from "./i18n.js";
// API 客户端：统一请求、错误、媒体 URL。
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, message, code = "request_failed", nextStep = t("请稍后重试；仍失败时联系管理员。")) {
    super(message);
    this.status = status;
    this.code = code;
    this.nextStep = nextStep;
  }
}

let authFailHandler = () => {};
export function setAuthFailHandler(fn) { authFailHandler = fn; }

export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = { "Accept-Language": getLocale() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(BASE + path, {
      method, headers, signal,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, t("连接中断，无法读取服务器。"), "network_error", t("检查网络和服务状态，然后重试。"));
  }
  if (res.status === 401) {
    authFailHandler();
    throw new ApiError(401, t("未登录"));
  }
  if (!res.ok) {
    let msg = res.statusText, detail;
    try { const data = await res.json(); msg = data.detail || msg; detail = data.error; } catch { /* non-JSON errors */ }
    if (Array.isArray(msg)) msg = msg.map(error => `${(error.loc || []).slice(1).join(".")}: ${error.msg}`).join("; ");
    throw new ApiError(res.status, msg || t("请求失败"), detail?.code, detail?.next_step);
  }
  return res.status === 204 ? null : res.json();
}

export const mediaUrl = (kind, id, version) => `${BASE}/media/${kind}/${id}`
  + (version ? `?v=${encodeURIComponent(version.slice(0, 16))}` : "");

export function imageMediaUrl(kind, image) {
  if (image[`${kind}_url`]) return image[`${kind}_url`];
  const url = mediaUrl(kind, image.id, image.sha256);
  return kind !== "original" && image.sha256
    ? `${url}-d${image.derivative_version ?? 0}` : url;
}

// 构造 /images 查询串
export function imagesQuery({ q = "", sort = "newest", limit = 120, offset = 0, ...rest } = {}) {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  if (sort) p.set("sort", sort);
  p.set("limit", limit);
  p.set("offset", offset);
  for (const [k, v] of Object.entries(rest)) {
    if (v !== undefined && v !== null && v !== false && v !== "") p.set(k, v);
  }
  return p.toString();
}
