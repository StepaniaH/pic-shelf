import { t, getLocale } from "./i18n.js";
// API 客户端：统一请求、错误、媒体 URL。
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let authFailHandler = () => {};
export function setAuthFailHandler(fn) { authFailHandler = fn; }

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Accept-Language": getLocale() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401) {
    authFailHandler();
    throw new ApiError(401, t("未登录"));
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
    throw new ApiError(res.status, msg || t("请求失败"));
  }
  return res.status === 204 ? null : res.json();
}

export const mediaUrl = (kind, id) => `${BASE}/media/${kind}/${id}`;

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
