// 应用外壳：路由、用户态、通用 UI（toast / 弹窗）。
import { api, setAuthFailHandler } from "./api.js";
import * as views from "./views.js";
import { renderAdmin } from "./admin.js";
import { t, getLocale, setLocale } from "./i18n.js";
import { activateDialog } from "./dialog.js";

export const state = { user: null, route: null };

const viewEl = () => document.querySelector("#view");

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function toast(msg, kind = "info") {
  const root = document.querySelector("#toast-root");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => el.classList.add("out"), 2600);
  setTimeout(() => el.remove(), 3200);
}

export function can(cap) {
  return Boolean(state.user && state.user.caps.includes(cap));
}

export function navigate(hash) { location.hash = hash; }

// ---------------------------------------------------------------- 通用弹窗

export function showModal({ title, bodyHtml, onMount, onClose }) {
  const root = document.querySelector("#modal-root");
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = t("\n    <div class=\"modal\" role=\"dialog\" aria-modal=\"true\" aria-label=\"{v0}\">\n      <div class=\"modal-head\"><h2>{v1}</h2><button class=\"icon-btn\" data-close aria-label=\"关闭\">✕</button></div>\n      <div class=\"modal-body\"></div>\n    </div>", {v0: escapeHtml(title), v1: escapeHtml(title)});
  wrap.querySelector(".modal-body").innerHTML = bodyHtml;
  let closed = false;
  let releaseFocus;
  const close = () => {
    if (closed) return;
    closed = true;
    wrap.remove();
    releaseFocus?.();
    onClose?.();
  };
  wrap.querySelectorAll("[data-close]").forEach(el => el.addEventListener("click", close));
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  root.appendChild(wrap);
  wrap.setAttribute("aria-label", title);
  wrap.querySelector(".modal").removeAttribute("role");
  wrap.querySelector(".modal").removeAttribute("aria-modal");
  releaseFocus = activateDialog(wrap, close);
  if (onMount) onMount(wrap.querySelector(".modal"), close);
  return { el: wrap, close };
}

export function uiPrompt(title, { placeholder = "", value = "", type = "text" } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => { if (!settled) { settled = true; modal.close(); resolve(v); } };
    const modal = showModal({
      title,
      onClose: () => finish(null),
      bodyHtml: t("<form class=\"prompt-form\"><input type=\"{type}\" value=\"{v0}\" placeholder=\"{v1}\" autocomplete=\"off\"><div class=\"modal-actions\"><button type=\"submit\" class=\"btn primary\">确定</button><button type=\"button\" class=\"btn\" data-cancel>取消</button></div></form>", {type: type, v0: escapeHtml(value), v1: escapeHtml(placeholder)}),
      onMount(el) {
        const input = el.querySelector("input");
        input.focus();
        el.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); finish(input.value); });
        el.querySelector("[data-cancel]").addEventListener("click", () => finish(null));
      },
    });
    modal.el.addEventListener("keydown", (e) => { if (e.key === "Escape") finish(null); });
  });
}

export function uiConfirm(title, text) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => { if (!settled) { settled = true; modal.close(); resolve(v); } };
    const modal = showModal({
      title,
      onClose: () => finish(false),
      bodyHtml: t("<p>{v0}</p><div class=\"modal-actions\"><button class=\"btn primary\" data-yes>确定</button><button class=\"btn\" data-no>取消</button></div>", {v0: escapeHtml(text)}),
      onMount(el) {
        el.querySelector("[data-yes]").classList.replace("primary", "danger");
        el.querySelector("[data-yes]").addEventListener("click", () => finish(true));
        el.querySelector("[data-no]").addEventListener("click", () => finish(false));
        el.querySelector("[data-no]").focus();
      },
    });
  });
}

// ---------------------------------------------------------------- 用户

async function ensureUser() {
  if (state.user) return state.user;
  try {
    state.user = await api("/auth/me");
  } catch {
    state.user = null;
  }
  renderUserBox();
  return state.user;
}

function renderUserBox() {
  const box = document.querySelector("#userbox");
  const adminLink = document.querySelector("#nav-admin");
  if (state.user) {
    adminLink.hidden = !state.user.is_admin;
    box.innerHTML = t("<span class=\"username\">{v0}</span>\n      <button class=\"btn small\" id=\"logout-btn\">退出</button>", {v0: escapeHtml(state.user.display_name || state.user.username)});
    box.querySelector("#logout-btn").addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" });
      state.user = null;
      renderUserBox();
      navigate("#/login");
    });
  } else {
    adminLink.hidden = true;
    box.innerHTML = t("<a class=\"btn small\" href=\"#/login\">登录</a>");
  }
}

// 供其它模块在登录/登出后刷新顶栏
export function refreshChrome() {
  renderUserBox();
}

// 静态 UI（index.html 骨架里的字符串）与语言切换
function applyStaticUI() {
  document.documentElement.lang = getLocale() === "zh" ? "zh-CN" : "en";
  document.title = t("Pic Shelf — 图片库");
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) el.placeholder = t(el.dataset.i18nPlaceholder);
  for (const el of document.querySelectorAll("[data-i18n-label]")) el.setAttribute("aria-label", t(el.dataset.i18nLabel));
}

function renderLangBtn() {
  const btn = document.querySelector("#lang-btn");
  if (!btn) return;
  btn.textContent = getLocale() === "zh" ? "EN" : "中文";
  btn.addEventListener("click", () => { setLocale(getLocale() === "zh" ? "en" : "zh"); location.reload(); });
}

// ---------------------------------------------------------------- 路由

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const parts = path.split("/");
  return { name: parts[0] || "home", id: parts[1] ? decodeURIComponent(parts[1]) : null, params: new URLSearchParams(qs || "") };
}

let routeVersion = 0;
async function route() {
  const version = ++routeVersion;
  const r = parseHash();
  state.route = r;
  const target = document.createElement("div");
  viewEl().replaceChildren(target);
  target.innerHTML = `<p class="empty" role="status">${t("加载中…")}</p>`;
  document.querySelectorAll("#nav a").forEach(link => {
    const selected = link.hash === `#/${r.name}` || link.hash === `#/${r.name}s`;
    if (selected) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  try {
    if (r.name === "login") { views.renderLogin(target); return; }
    await ensureUser();
    if (version !== routeVersion) return;
    if (!state.user) { navigate("#/login"); return; }
    document.querySelector("#search-input").value = r.name === "search" ? (r.params.get("q") || "") : document.querySelector("#search-input").value;
    switch (r.name) {
      case "home": await views.renderHome(target); break;
      case "albums": await views.renderAlbums(target, r.params); break;
      case "album": await views.renderAlbum(target, Number(r.id)); break;
      case "artists": await views.renderArtists(target); break;
      case "artist": await views.renderArtist(target, Number(r.id)); break;
      case "tags": await views.renderTags(target); break;
      case "tag": await views.renderTag(target, r.id); break;
      case "favorites": await views.renderFavorites(target); break;
      case "search": await views.renderSearch(target, r.params.get("q") || ""); break;
      case "library": await views.renderLibrary(target, Number(r.id)); break;
      case "admin": await renderAdmin(target, r.params); break;
      case "view": await views.renderDeepLink(target, Number(r.id), r.params); break;
      default: await views.renderHome(target);
    }
  } catch (e) {
    if (version !== routeVersion || (e && e.status === 401)) return;
    console.error(e);
    toast(e.message || t("出错了"), "error");
    target.innerHTML = `<p class="empty error">${escapeHtml(e.message || t("出错了"))}</p>`;
  }
}

setAuthFailHandler(() => {
  state.user = null;
  renderUserBox();
  if (!location.hash.startsWith("#/login")) navigate("#/login");
});

document.querySelector("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = document.querySelector("#search-input").value.trim();
  navigate("#/search?q=" + encodeURIComponent(q));
});

window.addEventListener("hashchange", route);
document.querySelector(".skip-link").addEventListener("click", event => {
  event.preventDefault();
  viewEl().focus();
});
applyStaticUI();
renderLangBtn();
route();
