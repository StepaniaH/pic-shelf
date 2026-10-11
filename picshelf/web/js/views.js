// 视图：首页 / 专辑 / 艺术家 / 标签 / 收藏 / 搜索 / 库 / 深链
import { api, mediaUrl, imagesQuery } from "./api.js";
import { state, escapeHtml, navigate, toast, can, uiConfirm, refreshChrome } from "./app.js";
import { openViewer } from "./viewer.js";
import { openSlideshow } from "./slideshow.js";
import { t } from "./i18n.js";

export function fmtSize(n) {
  if (!n && n !== 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

export const SORTS = [
  ["newest", t("最新导入")], ["oldest", t("最早导入")], ["path", t("按文件名")],
  ["rating", t("按我的评分")], ["avg", t("按平均分")], ["random", t("随机")],
];

// ---------------------------------------------------------------- 数据源

export class Source {
  constructor(title, context = {}, { q = "", sort = "newest", filters = {}, emptyText = t("没有图片") } = {}) {
    this.title = title;
    this.context = context;
    this.q = q;
    this.sort = sort;
    this.filters = filters;
    this.emptyText = emptyText;
    this.items = [];
    this.total = 0;
    this.onChange = null;
  }
  async load(reset = true) {
    if (reset) this.items = [];
    const res = await api("/images?" + imagesQuery({
      q: this.q, sort: this.sort, limit: 120, offset: this.items.length, ...this.filters,
    }));
    this.total = res.total;
    this.items.push(...res.items);
    return res;
  }
  async loadMore() {
    if (this.items.length >= this.total) return false;
    await this.load(false);
    return true;
  }
  get loadedAll() { return this.items.length >= this.total; }
}

// ---------------------------------------------------------------- 网格

function cardHtml(item) {
  const rating = item.my_rating >= 0 ? `<span class="badge rated">${item.my_rating}</span>` : "";
  const fav = item.favorite ? `<span class="badge fav">★</span>` : "";
  return `<figure class="card" data-id="${item.id}" tabindex="0">
    <img src="${mediaUrl("thumb", item.id)}" alt="${escapeHtml(item.filename)}" decoding="async">
    <figcaption><span class="fname" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
    <span class="badges">${rating}${fav}</span></figcaption></figure>`;
}

export function renderGridInto(view, source, { headerExtraHtml = "" } = {}) {
  view.innerHTML = t("\n    <div class=\"page-head\">\n      <h1>{v0}</h1><span class=\"count\" id=\"count\">共 {v1} 张</span>\n      <div class=\"toolbar\">\n        {headerExtraHtml}\n        <label class=\"muted\">排序\n          <select id=\"sort-select\">{v2}\n          </select>\n        </label>\n        <button class=\"btn primary\" id=\"slideshow-btn\">幻灯片</button>\n      </div>\n    </div>\n    <div class=\"grid\" id=\"grid\"></div>\n    <div class=\"load-more\"><button class=\"btn\" id=\"more-btn\" hidden>加载更多</button></div>", {v0: escapeHtml(source.title), v1: source.total, headerExtraHtml: headerExtraHtml, v2: SORTS.map(([v, l]) =>
            `<option value="${v}" ${v === source.sort ? "selected" : ""}>${l}</option>`).join("")});

  const grid = view.querySelector("#grid");
  const moreBtn = view.querySelector("#more-btn");
  const paint = () => {
    grid.innerHTML = source.items.map(cardHtml).join("") || `<p class="empty">${escapeHtml(source.emptyText)}</p>`;
    view.querySelector("#count").textContent = t("共 {v0} 张", {v0: source.total});
    moreBtn.hidden = source.loadedAll;
  };
  paint();

  moreBtn.addEventListener("click", async () => { await source.loadMore(); paint(); });
  view.querySelector("#sort-select").addEventListener("change", async (e) => {
    source.sort = e.target.value;
    await source.load(true);
    paint();
  });
  view.querySelector("#slideshow-btn").addEventListener("click", () => openSlideshow(source));
  grid.addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (!card) return;
    const idx = source.items.findIndex((i) => String(i.id) === card.dataset.id);
    if (idx >= 0) openViewer(source, idx);
  });
  grid.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const card = e.target.closest(".card");
    if (!card) return;
    const idx = source.items.findIndex((i) => String(i.id) === card.dataset.id);
    if (idx >= 0) openViewer(source, idx);
  });

  source.onChange = () => paint(); // 查看器里改动后（评分/收藏）刷新徽标
  return paint;
}

// ---------------------------------------------------------------- 登录

export function renderLogin(view) {
  view.innerHTML = t("\n    <div class=\"login-wrap\">\n      <h1>登录 Pic Shelf</h1>\n      <form id=\"login-form\">\n        <input type=\"text\" id=\"login-username\" placeholder=\"用户名\" autocomplete=\"username\" autofocus>\n        <input type=\"password\" id=\"login-password\" placeholder=\"密码\" autocomplete=\"current-password\">\n        <button class=\"btn primary\" type=\"submit\" style=\"width:100%\">登录</button>\n      </form>\n      <p class=\"hint\">没有账号？现有用户可在「管理 → 用户」中创建，或在服务器上运行 <code>picshelf users add 名字</code>。</p>\n    </div>");
  view.querySelector("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const data = await api("/auth/login", {
        method: "POST",
        body: {
          username: view.querySelector("#login-username").value.trim(),
          password: view.querySelector("#login-password").value,
        },
      });
      state.user = data.user;
      refreshChrome();
      navigate("#/home");
    } catch (err) {
      toast(err.message || t("登录失败"), "error");
    }
  });
}

// ---------------------------------------------------------------- 首页

export async function renderHome(view) {
  const [stats, libs] = await Promise.all([api("/stats"), api("/libraries")]);
  const tiles = [
    [t("图片"), stats.images, fmtSize(stats.size_bytes)],
    [t("库"), stats.libraries, ""], [t("专辑"), stats.albums, ""],
    [t("艺术家"), stats.artists, ""], [t("标签"), stats.tags, ""],
    [t("用户"), stats.users, ""],
  ];
  view.innerHTML = t("\n    <div class=\"page-head\"><h1>首页</h1></div>\n    <div class=\"tiles\">{v0}\n    </div>\n    <h2>库</h2>\n    <div class=\"lib-cards\">{v1}\n    </div>", {v0: tiles.map(([t, n, sub]) =>
      `<div class="tile"><b>${n}</b><span>${t}${sub ? " · " + sub : ""}</span></div>`).join(""), v1: libs.map((l) => `
      <div class="lib-card">
        <h3>${escapeHtml(l.name)}</h3>
        <p class="muted">${escapeHtml(l.root_path)}</p>
        <p class="muted">图片 ${l.image_count} · 专辑 ${l.album_count}</p>
        <div class="form-row">
          <a class="btn small" href="#/library/${l.id}">浏览</a>
          <a class="btn small" href="#/albums?library_id=${l.id}">专辑</a>
          ${state.user.is_admin ? `<button class="btn small" data-scan="${l.id}">扫描更新</button>` : ""}
        </div>
      </div>`).join("") || `<p class="empty">还没有库。管理员可在「管理 → 库」中注册目录。</p>`});
  view.querySelectorAll("[data-scan]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = t("扫描中…");
    try {
      const stats2 = await api(`/libraries/${btn.dataset.scan}/scan`, { method: "POST" });
      toast(t("扫描完成：新增 {v0}，更新 {v1}，移动 {v2}，缺失 {v3}", {v0: stats2.added, v1: stats2.updated, v2: stats2.moved, v3: stats2.missing}), "ok");
      await renderHome(view);
    } catch (e) {
      toast(e.message, "error");
      btn.disabled = false;
      btn.textContent = t("扫描更新");
    }
  }));
}

// ---------------------------------------------------------------- 专辑

export async function renderAlbums(view, params) {
  const libraryId = params.get("library_id");
  const albums = await api("/albums" + (libraryId ? `?library_id=${libraryId}` : ""));
  const groups = {};
  for (const a of albums) {
    const key = a.library_name || t("（无库）");
    (groups[key] = groups[key] || []).push(a);
  }
  view.innerHTML = t("\n    <div class=\"page-head\"><h1>专辑</h1><span class=\"count\">共 {v0} 个</span></div>\n    {v1}", {v0: albums.length, v1: Object.entries(groups).map(([lib, list]) => `
      <h2>${escapeHtml(lib)}</h2>
      <div class="grid">${list.map((a) => `
        <figure class="card" data-href="#/album/${a.id}" tabindex="0">
          ${a.cover_image_id
            ? `<img loading="lazy" src="${mediaUrl("thumb", a.cover_image_id)}" alt="${escapeHtml(a.name)}">`
            : `<div style="aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;color:var(--muted)">空专辑</div>`}
          <figcaption><span class="fname">${escapeHtml(a.name)}</span>
            <span class="badge">${a.kind === "manual" ? "手工" : "文件夹"} ${a.image_count}</span></figcaption>
        </figure>`).join("")}</div>`).join("") || `<p class="empty">还没有专辑。</p>`});
  view.querySelectorAll("[data-href]").forEach((el) => {
    el.addEventListener("click", () => navigate(el.dataset.href));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(el.dataset.href); });
  });
}

export async function renderAlbum(view, albumId) {
  const album = await api(`/albums/${albumId}`);
  const source = new Source(
    t("专辑：{v0}", {v0: album.name}),
    { album_id: albumId, title: t("专辑：{v0}", {v0: album.name}) },
    { sort: "path", filters: { album_id: albumId } },
  );
  await source.load(true);
  const extra = [
    album.category ? `<span class="pill">${escapeHtml(album.category)}</span>` : "",
    t("<a class=\"btn small\" href=\"#/albums\">← 全部专辑</a>"),
  ].join(" ");
  renderGridInto(view, source, { headerExtraHtml: extra });
}

// ---------------------------------------------------------------- 艺术家

export async function renderArtists(view) {
  const artists = await api("/artists?limit=300");
  view.innerHTML = t("\n    <div class=\"page-head\"><h1>艺术家</h1><span class=\"count\">共 {v0} 位</span></div>\n    <div class=\"chips\">{v1}\n    </div>", {v0: artists.length, v1: artists.map((a) => `
      <span class="chip" data-href="#/artist/${a.id}" tabindex="0">${escapeHtml(a.name)}
      <span class="n">${a.image_count}</span></span>`).join("") || `<p class="empty">还没有艺术家。可在查看器里给图片关联，或运行 <code>picshelf artist add 名字</code>。</p>`});
  view.querySelectorAll("[data-href]").forEach((el) => {
    el.addEventListener("click", () => navigate(el.dataset.href));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(el.dataset.href); });
  });
}

export async function renderArtist(view, artistId) {
  const artist = await api(`/artists/${artistId}`);
  const source = new Source(
    t("艺术家：{v0}", {v0: artist.name}),
    { artist_id: artistId, title: t("艺术家：{v0}", {v0: artist.name}) },
    { filters: { artist_id: artistId } },
  );
  await source.load(true);
  const links = (artist.links || []).map((u) =>
    `<a class="pill" href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u.replace(/^https?:\/\//, ""))}</a>`).join(" ");
  const extra = [artist.aliases ? t("<span class=\"pill\">别名：{v0}</span>", {v0: escapeHtml(artist.aliases)}) : "", links,
    t("<a class=\"btn small\" href=\"#/artists\">← 全部艺术家</a>")].join(" ");
  renderGridInto(view, source, { headerExtraHtml: extra });
}

// ---------------------------------------------------------------- 标签

export async function renderTags(view) {
  const tags = await api("/tags?limit=500");
  view.innerHTML = t("\n    <div class=\"page-head\"><h1>标签</h1><span class=\"count\">共 {v0} 个</span></div>\n    <div class=\"chips\">{v1}\n    </div>", {v0: tags.length, v1: tags.map((t) => `
      <span class="chip" data-tag="${escapeHtml(t.name)}" tabindex="0">${escapeHtml(t.name)}
      <span class="n">${t.count}</span></span>`).join("") || `<p class="empty">还没有标签。打开图片在其信息面板里添加。</p>`});
  view.querySelectorAll("[data-tag]").forEach((el) => {
    el.addEventListener("click", () => navigate("#/tag/" + encodeURIComponent(el.dataset.tag)));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate("#/tag/" + encodeURIComponent(el.dataset.tag)); });
  });
}

export async function renderTag(view, name) {
  const source = new Source(t("标签：{name}", {name: name}), { tag: name, title: t("标签：{name}", {name: name}) },
    { filters: { tag: name }, sort: "newest" });
  await source.load(true);
  renderGridInto(view, source, { headerExtraHtml: t("<a class=\"btn small\" href=\"#/tags\">← 全部标签</a>") });
}

// ---------------------------------------------------------------- 收藏 / 搜索 / 库

export async function renderFavorites(view) {
  const source = new Source(t("我的收藏"), { favorites: true, title: t("我的收藏") },
    { filters: { favorites: "true" } });
  await source.load(true);
  renderGridInto(view, source, {});
}

export async function renderSearch(view, q) {
  const source = new Source(q ? t("搜索：{q}", {q: q}) : t("搜索（全部）"), { title: t("搜索：{q}", {q: q}), q }, { q });
  await source.load(true);
  renderGridInto(view, source, { headerExtraHtml: t("<a class=\"btn small\" href=\"#/home\">← 首页</a>") });
}

export async function renderLibrary(view, libraryId) {
  const lib = await api(`/libraries/${libraryId}`);
  const source = new Source(t("库：{v0}", {v0: lib.name}), { library_id: libraryId, title: t("库：{v0}", {v0: lib.name}) },
    { filters: { library_id: libraryId } });
  await source.load(true);
  const canManage = state.user.is_admin || lib.access === "manage";
  const scanBtn = canManage ? t("<button class=\"btn small\" id=\"lib-scan\">扫描更新</button>") : "";
  const last = lib.last_scan
    ? t("<span class=\"muted\">上次扫描：{v0}</span>", {v0: escapeHtml((lib.last_scan.finished_at || lib.last_scan.started_at || "").replace("T", " ").slice(0, 16))})
    : "";
  renderGridInto(view, source, {
    headerExtraHtml: t("<a class=\"btn small\" href=\"#/albums?library_id={libraryId}\">专辑</a>{scanBtn}{last}", {libraryId: libraryId, scanBtn: scanBtn, last: last}),
  });
  const scanEl = view.querySelector("#lib-scan");
  if (scanEl) scanEl.addEventListener("click", async () => {
    scanEl.disabled = true;
    scanEl.textContent = t("扫描中…");
    try {
      const s = await api(`/libraries/${libraryId}/scan`, { method: "POST" });
      toast(t("扫描完成：新增 {v0}，更新 {v1}，移动 {v2}，缺失 {v3}", {v0: s.added, v1: s.updated, v2: s.moved, v3: s.missing}), "ok");
      await renderLibrary(view, libraryId);
    } catch (e) {
      toast(e.message, "error");
      scanEl.disabled = false;
      scanEl.textContent = t("扫描更新");
    }
  });
}

// ---------------------------------------------------------------- 深链（直接打开某张图）

export async function renderDeepLink(view, imageId, params) {
  let source;
  if (params.get("album")) {
    const album = await api(`/albums/${params.get("album")}`);
    source = new Source(t("专辑：{v0}", {v0: album.name}), { album_id: album.id, title: t("专辑：{v0}", {v0: album.name}) },
      { sort: "path", filters: { album_id: album.id } });
  } else if (params.get("artist")) {
    const artist = await api(`/artists/${params.get("artist")}`);
    source = new Source(t("艺术家：{v0}", {v0: artist.name}), { artist_id: artist.id, title: t("艺术家：{v0}", {v0: artist.name}) },
      { filters: { artist_id: artist.id } });
  } else if (params.get("tag")) {
    source = new Source(t("标签：{v0}", {v0: params.get("tag")}), { tag: params.get("tag") },
      { filters: { tag: params.get("tag") } });
  } else {
    source = new Source(t("全部图片"), {}, {});
  }
  await source.load(true);
  for (let i = 0; i < 5; i++) {
    if (source.items.some((x) => String(x.id) === String(imageId))) break;
    if (!(await source.loadMore())) break;
  }
  renderGridInto(view, source, {});
  const idx = source.items.findIndex((x) => String(x.id) === String(imageId));
  if (idx >= 0) openViewer(source, idx);
}
