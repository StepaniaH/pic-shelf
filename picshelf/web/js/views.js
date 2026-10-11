// 视图：首页 / 专辑 / 艺术家 / 标签 / 收藏 / 搜索 / 库 / 深链
import { api, mediaUrl, imagesQuery } from "./api.js";
import { state, escapeHtml, navigate, toast, can, uiConfirm, refreshChrome } from "./app.js";
import { openViewer } from "./viewer.js";
import { openSlideshow } from "./slideshow.js";

export function fmtSize(n) {
  if (!n && n !== 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

export const SORTS = [
  ["newest", "最新导入"], ["oldest", "最早导入"], ["path", "按文件名"],
  ["rating", "按我的评分"], ["avg", "按平均分"], ["random", "随机"],
];

// ---------------------------------------------------------------- 数据源

export class Source {
  constructor(title, context = {}, { q = "", sort = "newest", filters = {}, emptyText = "没有图片" } = {}) {
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
  view.innerHTML = `
    <div class="page-head">
      <h1>${escapeHtml(source.title)}</h1><span class="count" id="count">共 ${source.total} 张</span>
      <div class="toolbar">
        ${headerExtraHtml}
        <label class="muted">排序
          <select id="sort-select">${SORTS.map(([v, l]) =>
            `<option value="${v}" ${v === source.sort ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </label>
        <button class="btn primary" id="slideshow-btn">幻灯片</button>
      </div>
    </div>
    <div class="grid" id="grid"></div>
    <div class="load-more"><button class="btn" id="more-btn" hidden>加载更多</button></div>`;

  const grid = view.querySelector("#grid");
  const moreBtn = view.querySelector("#more-btn");
  const paint = () => {
    grid.innerHTML = source.items.map(cardHtml).join("") || `<p class="empty">${escapeHtml(source.emptyText)}</p>`;
    view.querySelector("#count").textContent = `共 ${source.total} 张`;
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
  view.innerHTML = `
    <div class="login-wrap">
      <h1>登录 Pic Shelf</h1>
      <form id="login-form">
        <input type="text" id="login-username" placeholder="用户名" autocomplete="username" autofocus>
        <input type="password" id="login-password" placeholder="密码" autocomplete="current-password">
        <button class="btn primary" type="submit" style="width:100%">登录</button>
      </form>
      <p class="hint">没有账号？现有用户可在「管理 → 用户」中创建，或在服务器上运行 <code>picshelf users add 名字</code>。</p>
    </div>`;
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
      toast(err.message || "登录失败", "error");
    }
  });
}

// ---------------------------------------------------------------- 首页

export async function renderHome(view) {
  const [stats, libs] = await Promise.all([api("/stats"), api("/libraries")]);
  const tiles = [
    ["图片", stats.images, fmtSize(stats.size_bytes)],
    ["库", stats.libraries, ""], ["专辑", stats.albums, ""],
    ["艺术家", stats.artists, ""], ["标签", stats.tags, ""],
    ["用户", stats.users, ""],
  ];
  view.innerHTML = `
    <div class="page-head"><h1>首页</h1></div>
    <div class="tiles">${tiles.map(([t, n, sub]) =>
      `<div class="tile"><b>${n}</b><span>${t}${sub ? " · " + sub : ""}</span></div>`).join("")}
    </div>
    <h2>库</h2>
    <div class="lib-cards">${libs.map((l) => `
      <div class="lib-card">
        <h3>${escapeHtml(l.name)}</h3>
        <p class="muted">${escapeHtml(l.root_path)}</p>
        <p class="muted">图片 ${l.image_count} · 专辑 ${l.album_count}</p>
        <div class="form-row">
          <a class="btn small" href="#/library/${l.id}">浏览</a>
          <a class="btn small" href="#/albums?library_id=${l.id}">专辑</a>
          ${state.user.is_admin ? `<button class="btn small" data-scan="${l.id}">扫描更新</button>` : ""}
        </div>
      </div>`).join("") || `<p class="empty">还没有库。管理员可在「管理 → 库」中注册目录。</p>`}
    </div>`;
  view.querySelectorAll("[data-scan]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "扫描中…";
    try {
      const stats2 = await api(`/libraries/${btn.dataset.scan}/scan`, { method: "POST" });
      toast(`扫描完成：新增 ${stats2.added}，更新 ${stats2.updated}，移动 ${stats2.moved}，缺失 ${stats2.missing}`, "ok");
      await renderHome(view);
    } catch (e) {
      toast(e.message, "error");
      btn.disabled = false;
      btn.textContent = "扫描更新";
    }
  }));
}

// ---------------------------------------------------------------- 专辑

export async function renderAlbums(view, params) {
  const libraryId = params.get("library_id");
  const albums = await api("/albums" + (libraryId ? `?library_id=${libraryId}` : ""));
  const groups = {};
  for (const a of albums) {
    const key = a.library_name || "（无库）";
    (groups[key] = groups[key] || []).push(a);
  }
  view.innerHTML = `
    <div class="page-head"><h1>专辑</h1><span class="count">共 ${albums.length} 个</span></div>
    ${Object.entries(groups).map(([lib, list]) => `
      <h2>${escapeHtml(lib)}</h2>
      <div class="grid">${list.map((a) => `
        <figure class="card" data-href="#/album/${a.id}" tabindex="0">
          ${a.cover_image_id
            ? `<img loading="lazy" src="${mediaUrl("thumb", a.cover_image_id)}" alt="${escapeHtml(a.name)}">`
            : `<div style="aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;color:var(--muted)">空专辑</div>`}
          <figcaption><span class="fname">${escapeHtml(a.name)}</span>
            <span class="badge">${a.kind === "manual" ? "手工" : "文件夹"} ${a.image_count}</span></figcaption>
        </figure>`).join("")}</div>`).join("") || `<p class="empty">还没有专辑。</p>`}`;
  view.querySelectorAll("[data-href]").forEach((el) => {
    el.addEventListener("click", () => navigate(el.dataset.href));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(el.dataset.href); });
  });
}

export async function renderAlbum(view, albumId) {
  const album = await api(`/albums/${albumId}`);
  const source = new Source(
    `专辑：${album.name}`,
    { album_id: albumId, title: `专辑：${album.name}` },
    { sort: "path", filters: { album_id: albumId } },
  );
  await source.load(true);
  const extra = [
    album.category ? `<span class="pill">${escapeHtml(album.category)}</span>` : "",
    `<a class="btn small" href="#/albums">← 全部专辑</a>`,
  ].join(" ");
  renderGridInto(view, source, { headerExtraHtml: extra });
}

// ---------------------------------------------------------------- 艺术家

export async function renderArtists(view) {
  const artists = await api("/artists?limit=300");
  view.innerHTML = `
    <div class="page-head"><h1>艺术家</h1><span class="count">共 ${artists.length} 位</span></div>
    <div class="chips">${artists.map((a) => `
      <span class="chip" data-href="#/artist/${a.id}" tabindex="0">${escapeHtml(a.name)}
      <span class="n">${a.image_count}</span></span>`).join("") || `<p class="empty">还没有艺术家。可在查看器里给图片关联，或运行 <code>picshelf artist add 名字</code>。</p>`}
    </div>`;
  view.querySelectorAll("[data-href]").forEach((el) => {
    el.addEventListener("click", () => navigate(el.dataset.href));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(el.dataset.href); });
  });
}

export async function renderArtist(view, artistId) {
  const artist = await api(`/artists/${artistId}`);
  const source = new Source(
    `艺术家：${artist.name}`,
    { artist_id: artistId, title: `艺术家：${artist.name}` },
    { filters: { artist_id: artistId } },
  );
  await source.load(true);
  const links = (artist.links || []).map((u) =>
    `<a class="pill" href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u.replace(/^https?:\/\//, ""))}</a>`).join(" ");
  const extra = [artist.aliases ? `<span class="pill">别名：${escapeHtml(artist.aliases)}</span>` : "", links,
    `<a class="btn small" href="#/artists">← 全部艺术家</a>`].join(" ");
  renderGridInto(view, source, { headerExtraHtml: extra });
}

// ---------------------------------------------------------------- 标签

export async function renderTags(view) {
  const tags = await api("/tags?limit=500");
  view.innerHTML = `
    <div class="page-head"><h1>标签</h1><span class="count">共 ${tags.length} 个</span></div>
    <div class="chips">${tags.map((t) => `
      <span class="chip" data-tag="${escapeHtml(t.name)}" tabindex="0">${escapeHtml(t.name)}
      <span class="n">${t.count}</span></span>`).join("") || `<p class="empty">还没有标签。打开图片在其信息面板里添加。</p>`}
    </div>`;
  view.querySelectorAll("[data-tag]").forEach((el) => {
    el.addEventListener("click", () => navigate("#/tag/" + encodeURIComponent(el.dataset.tag)));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate("#/tag/" + encodeURIComponent(el.dataset.tag)); });
  });
}

export async function renderTag(view, name) {
  const source = new Source(`标签：${name}`, { tag: name, title: `标签：${name}` },
    { filters: { tag: name }, sort: "newest" });
  await source.load(true);
  renderGridInto(view, source, { headerExtraHtml: `<a class="btn small" href="#/tags">← 全部标签</a>` });
}

// ---------------------------------------------------------------- 收藏 / 搜索 / 库

export async function renderFavorites(view) {
  const source = new Source("我的收藏", { favorites: true, title: "我的收藏" },
    { filters: { favorites: "true" } });
  await source.load(true);
  renderGridInto(view, source, {});
}

export async function renderSearch(view, q) {
  const source = new Source(q ? `搜索：${q}` : "搜索（全部）", { title: `搜索：${q}`, q }, { q });
  await source.load(true);
  renderGridInto(view, source, { headerExtraHtml: `<a class="btn small" href="#/home">← 首页</a>` });
}

export async function renderLibrary(view, libraryId) {
  const lib = await api(`/libraries/${libraryId}`);
  const source = new Source(`库：${lib.name}`, { library_id: libraryId, title: `库：${lib.name}` },
    { filters: { library_id: libraryId } });
  await source.load(true);
  const canManage = state.user.is_admin || lib.access === "manage";
  const scanBtn = canManage ? `<button class="btn small" id="lib-scan">扫描更新</button>` : "";
  const last = lib.last_scan
    ? `<span class="muted">上次扫描：${escapeHtml((lib.last_scan.finished_at || lib.last_scan.started_at || "").replace("T", " ").slice(0, 16))}</span>`
    : "";
  renderGridInto(view, source, {
    headerExtraHtml: `<a class="btn small" href="#/albums?library_id=${libraryId}">专辑</a>${scanBtn}${last}`,
  });
  const scanEl = view.querySelector("#lib-scan");
  if (scanEl) scanEl.addEventListener("click", async () => {
    scanEl.disabled = true;
    scanEl.textContent = "扫描中…";
    try {
      const s = await api(`/libraries/${libraryId}/scan`, { method: "POST" });
      toast(`扫描完成：新增 ${s.added}，更新 ${s.updated}，移动 ${s.moved}，缺失 ${s.missing}`, "ok");
      await renderLibrary(view, libraryId);
    } catch (e) {
      toast(e.message, "error");
      scanEl.disabled = false;
      scanEl.textContent = "扫描更新";
    }
  });
}

// ---------------------------------------------------------------- 深链（直接打开某张图）

export async function renderDeepLink(view, imageId, params) {
  let source;
  if (params.get("album")) {
    const album = await api(`/albums/${params.get("album")}`);
    source = new Source(`专辑：${album.name}`, { album_id: album.id, title: `专辑：${album.name}` },
      { sort: "path", filters: { album_id: album.id } });
  } else if (params.get("artist")) {
    const artist = await api(`/artists/${params.get("artist")}`);
    source = new Source(`艺术家：${artist.name}`, { artist_id: artist.id, title: `艺术家：${artist.name}` },
      { filters: { artist_id: artist.id } });
  } else if (params.get("tag")) {
    source = new Source(`标签：${params.get("tag")}`, { tag: params.get("tag") },
      { filters: { tag: params.get("tag") } });
  } else {
    source = new Source("全部图片", {}, {});
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
