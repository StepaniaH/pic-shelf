// 应用外壳：路由、用户态、通用 UI（toast / 弹窗）。
import { api, setAuthFailHandler } from "./api.js";
import * as views from "./views.js";
import { renderAdmin } from "./admin.js";

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

export function showModal({ title, bodyHtml, onMount }) {
  const root = document.querySelector("#modal-root");
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
      <div class="modal-head"><h2>${escapeHtml(title)}</h2><button class="icon-btn" data-close aria-label="关闭">✕</button></div>
      <div class="modal-body"></div>
    </div>`;
  wrap.querySelector(".modal-body").innerHTML = bodyHtml;
  const close = () => wrap.remove();
  wrap.querySelector("[data-close]").addEventListener("click", close);
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  root.appendChild(wrap);
  if (onMount) onMount(wrap.querySelector(".modal"), close);
  return { el: wrap, close };
}

export function uiPrompt(title, { placeholder = "", value = "", type = "text" } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => { if (!settled) { settled = true; modal.close(); resolve(v); } };
    const modal = showModal({
      title,
      bodyHtml: `<form class="prompt-form"><input type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off"><div class="modal-actions"><button type="submit" class="btn primary">确定</button><button type="button" class="btn" data-cancel>取消</button></div></form>`,
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
      bodyHtml: `<p>${escapeHtml(text)}</p><div class="modal-actions"><button class="btn primary" data-yes>确定</button><button class="btn" data-no>取消</button></div>`,
      onMount(el) {
        el.querySelector("[data-yes]").addEventListener("click", () => finish(true));
        el.querySelector("[data-no]").addEventListener("click", () => finish(false));
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
    box.innerHTML = `<span class="username">${escapeHtml(state.user.display_name || state.user.username)}</span>
      <button class="btn small" id="logout-btn">退出</button>`;
    box.querySelector("#logout-btn").addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" });
      state.user = null;
      renderUserBox();
      navigate("#/login");
    });
  } else {
    adminLink.hidden = true;
    box.innerHTML = `<a class="btn small" href="#/login">登录</a>`;
  }
}

// 供其它模块在登录/登出后刷新顶栏
export function refreshChrome() {
  renderUserBox();
}

// ---------------------------------------------------------------- 路由

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const parts = path.split("/");
  return { name: parts[0] || "home", id: parts[1] ? decodeURIComponent(parts[1]) : null, params: new URLSearchParams(qs || "") };
}

async function route() {
  const r = parseHash();
  state.route = r;
  try {
    if (r.name === "login") { views.renderLogin(viewEl()); return; }
    await ensureUser();
    if (!state.user) { navigate("#/login"); return; }
    document.querySelector("#search-input").value = r.name === "search" ? (r.params.get("q") || "") : document.querySelector("#search-input").value;
    switch (r.name) {
      case "home": await views.renderHome(viewEl()); break;
      case "albums": await views.renderAlbums(viewEl(), r.params); break;
      case "album": await views.renderAlbum(viewEl(), Number(r.id)); break;
      case "artists": await views.renderArtists(viewEl()); break;
      case "artist": await views.renderArtist(viewEl(), Number(r.id)); break;
      case "tags": await views.renderTags(viewEl()); break;
      case "tag": await views.renderTag(viewEl(), r.id); break;
      case "favorites": await views.renderFavorites(viewEl()); break;
      case "search": await views.renderSearch(viewEl(), r.params.get("q") || ""); break;
      case "library": await views.renderLibrary(viewEl(), Number(r.id)); break;
      case "admin": await renderAdmin(viewEl()); break;
      case "view": await views.renderDeepLink(viewEl(), Number(r.id), r.params); break;
      default: await views.renderHome(viewEl());
    }
  } catch (e) {
    if (e && e.status === 401) return;
    console.error(e);
    toast(e.message || "出错了", "error");
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
route();
