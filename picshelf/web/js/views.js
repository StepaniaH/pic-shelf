// 视图：首页 / 专辑 / 艺术家 / 标签 / 收藏 / 搜索 / 库 / 深链
import { api, mediaUrl, imageMediaUrl } from "./api.js";
import { state, escapeHtml, navigate, toast, can, uiConfirm, refreshChrome } from "./app.js";
import { openViewer } from "./viewer.js";
import { openSlideshow } from "./slideshow.js";
import { t } from "./i18n.js";
import { jobKey, scanSummary, watchJob } from "./jobs.js";
import { externalUrl } from "./format.js";
import {attachBatchControls} from "./batches.js";
import {Source} from "./source.js";
import {ImageGrid} from "./image-grid.js";
import {editAlbum, editTag} from "./catalog-ui.js";
import {openImport} from "./importer.js";
import {openLibraryForm} from "./libraries.js";
export {Source} from "./source.js";

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

// ---------------------------------------------------------------- 网格
const gridDisposers = new WeakMap();
const sourceLoads = new WeakMap();

function cardHtml(item, {showLibrary = false} = {}) {
  const rating = item.my_rating >= 0 ? `<span class="badge rated">${item.my_rating}</span>` : "";
  const fav = item.favorite ? `<span class="badge fav">★</span>` : "";
  const library = showLibrary ? item.library_name : "";
  const path = library ? `${library} / ${item.rel_path}` : item.rel_path || item.filename;
  return `<figure class="card" data-id="${item.id}" tabindex="0" aria-label="${escapeHtml(library ? path : item.filename)}">
    <label class="card-selection"><input type="checkbox" data-select-image="${item.id}" aria-label="${escapeHtml(t("选择 {name}",{name:item.filename}))}"></label>
    ${item.missing?`<div class="missing-image">${t("文件缺失")}</div>`:`<img src="${imageMediaUrl("thumb", item)}" alt="" loading="lazy" decoding="async">`}
    <figcaption><span class="card-labels"><span class="fname" title="${escapeHtml(path)}">${escapeHtml(item.filename)}</span>
      ${library ? `<span class="library-name" title="${escapeHtml(library)}">${escapeHtml(library)}</span>` : ""}</span>
    <span class="badges">${rating}${fav}</span></figcaption></figure>`;
}

export async function loadSource(view, source) {
  if (!view.isConnected) return false;
  const previous = gridDisposers.get(view);
  sourceLoads.get(view)?.source.cancel();
  const pending = {source};
  sourceLoads.set(view, pending);
  state.dispose = () => { previous?.(); source.cancel(); };
  try {
    const result = await source.load(true);
    return Boolean(result) && view.isConnected && sourceLoads.get(view) === pending;
  } finally {
    if (sourceLoads.get(view) === pending) sourceLoads.delete(view);
  }
}

async function refreshAfterImport(view, source) {
  if (!view.isConnected) return;
  try {
    const result = await (source.nextOffset < 120 ? source.load(true) : source.refresh({afterWrite:true}));
    if (result && view.isConnected) source.onChange?.();
  } catch (error) { toast(error.message, 'error'); }
}

export function renderGridInto(view, source, { headerExtraHtml = "" } = {}) {
  if (!view.isConnected) { source.cancel(); return () => {}; }
  gridDisposers.get(view)?.();
  view.innerHTML = `
    <div class="page-head">
      <h1>${escapeHtml(source.title)}</h1><span class="count" id="count"></span>
      <div class="toolbar">${headerExtraHtml}<button class="btn primary" id="slideshow-btn">▷ ${t("幻灯片")}</button></div>
    </div>
    <div class="collection-toolbar">
      <label class="muted">${t("排序")} <select id="sort-select" aria-label="${t("排序")}">${SORTS.map(([value, label]) =>
        `<option value="${value}" ${value === source.sort ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="muted">${t("图片密度")} <select id="density-select" aria-label="${t("图片密度")}"><option value="comfortable">${t("宽松")}</option><option value="compact">${t("紧凑")}</option></select></label>
      <button class="btn small" data-selection-toggle aria-expanded="false">${t("选择")}</button>
      <button class="btn small" data-refresh>${t("刷新")}</button>
    </div>
    <p class="error" data-grid-errors role="status" hidden></p>
    <div class="grid" id="grid" role="list" aria-label="${escapeHtml(source.title)}"></div>
    <div class="load-more"><button class="btn" id="more-btn" hidden>${t("加载更多")}</button></div>`;

  const grid = view.querySelector("#grid");
  const density = view.querySelector("#density-select");
  let savedDensity;
  try { savedDensity = localStorage.getItem("picshelf.grid-density"); } catch { /* Optional preference. */ }
  density.value = (source.display?.density || savedDensity) === "compact" ? "compact" : "comfortable";
  grid.classList.toggle("compact", density.value === "compact");
  density.onchange = () => {
    grid.classList.toggle("compact", density.value === "compact");
    renderer.dirty = true; renderer.render();
    try { localStorage.setItem("picshelf.grid-density", density.value); } catch { /* Optional preference. */ }
  };
  const moreBtn = view.querySelector("#more-btn");
  const warnings = view.querySelector("[data-grid-errors]");
  const refresh = view.querySelector("[data-refresh]");
  let updateSelection = () => {};
  let showLibraries = false;
  const renderer = new ImageGrid(grid, item => cardHtml(item, {showLibrary:showLibraries}), () => updateSelection());
  let disposed = false, loading = false, autoFailed = false;
  const paint = () => {
    if (disposed || !view.isConnected) return;
    showLibraries = source.items.some(item => item.library_id !== source.items[0]?.library_id);
    renderer.update(source.items, source.total);
    if (!source.items.length && !source.errors.length) grid.innerHTML = `<p class="empty">${escapeHtml(source.emptyText)}</p>`;
    view.querySelector("#count").textContent = t("共 {v0} 张", {v0: source.total});
    moreBtn.hidden = source.loadedAll || source.sort === "random";
    warnings.textContent = source.errors.join(" ");
    warnings.hidden = !source.errors.length;
    view.querySelector("#slideshow-btn").disabled = source.total === 0;
    updateSelection();
  };
  updateSelection = attachBatchControls(view,source,grid,async()=>{await source.refresh({afterWrite:true});paint();});
  paint();
  refresh.onclick=async()=>{refresh.disabled=true;try{await source.refresh();paint();}catch(e){toast(e.message,"error");}finally{refresh.disabled=false;}};

  async function more(manual = false) {
    if (disposed || loading || source.loadedAll || (!manual && (autoFailed || document.body.classList.contains("dialog-open")))) return;
    loading = true; moreBtn.disabled = true;
    moreBtn.textContent = t("加载中…");
    try { await source.loadMore(); autoFailed = false; if (!disposed) paint(); }
    catch (error) { autoFailed = true; if (!disposed) toast(error.message, "error"); }
    finally {
      loading = false; moreBtn.disabled = false;
      moreBtn.textContent = t(autoFailed ? "重试" : "加载更多");
    }
  }
  moreBtn.addEventListener("click", () => more(true));
  const pages = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting)) more();
  }, {rootMargin:"600px"});
  pages.observe(moreBtn.parentElement);
  view.querySelector("#sort-select").addEventListener("change", async (e) => {
    const previous = source.sort;
    source.sort = e.target.value;
    e.target.disabled = true;
    try {
      if (!await loadSource(view, source)) return; autoFailed = false; paint();
      if (view.isConnected) {
        const [path, query] = location.hash.split("?");
        const params = new URLSearchParams(query); params.set("sort", source.sort);
        history.replaceState(null, "", `${path}?${params}`);
      }
    }
    catch (error) { source.sort = previous; e.target.value = previous; toast(error.message, "error"); }
    finally { e.target.disabled = false; }
  });
  view.querySelector("#slideshow-btn").addEventListener("click", () => openSlideshow(source));
  grid.addEventListener("click", (e) => {
    if(e.target.closest("input,label,button"))return;
    const card = e.target.closest(".card");
    if (!card) return;
    const idx = source.items.findIndex((i) => String(i.id) === card.dataset.id);
    card.focus();
    if (source.items[idx]?.missing) {toast(t("文件已缺失，请恢复原文件并重新扫描"),"error");return;}
    if (idx >= 0) openViewer(source, idx);
  });
  grid.addEventListener("keydown", (e) => {
    if(e.isComposing || e.keyCode === 229 || e.ctrlKey || e.altKey || e.metaKey)return;
    if(e.target.closest("input,label,button"))return;
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    const card = e.target.closest(".card");
    if (!card) return;
    const idx = source.items.findIndex((i) => String(i.id) === card.dataset.id);
    if (source.items[idx]?.missing) {toast(t("文件已缺失，请恢复原文件并重新扫描"),"error");return;}
    if (idx >= 0) openViewer(source, idx);
  });

  source.onChange = paint;
  const dispose = () => { disposed = true; source.cancel(); pages.disconnect(); renderer.dispose(); };
  gridDisposers.set(view, dispose);
  if (view.isConnected) state.dispose = dispose;
  return paint;
}

// ---------------------------------------------------------------- 登录

export function renderLogin(view) {
  document.body.classList.add("signed-out");
  view.innerHTML = `<div class="login-wrap"><h1>${t("登录 Pic Shelf")}</h1>
    <form id="login-form">
      <label>${t("用户名")}<input type="text" id="login-username" autocomplete="username" required></label>
      <label>${t("密码")}<input type="password" id="login-password" autocomplete="current-password" required></label>
      <p class="error" id="login-error" role="alert" hidden></p>
      <button class="btn primary" type="submit">${t("登录")}</button>
    </form><p class="hint">${t("需要账号？请联系管理员。")}</p></div>`;
  const form = view.querySelector("#login-form");
  const button = form.querySelector("button");
  const error = view.querySelector("#login-error");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    error.hidden = true;
    try {
      const result = await api("/auth/login", {method:"POST", body:{
        username: form.querySelector("#login-username").value.trim(),
        password: form.querySelector("#login-password").value,
      }});
      state.user = result.user;
      refreshChrome();
      navigate("#/home");
    } catch (failure) {
      error.textContent = failure.message || t("登录失败");
      error.hidden = false;
    } finally { button.disabled = false; }
  });
}

// ---------------------------------------------------------------- 首页

export async function renderHome(view) {
  const [libs, recent] = await Promise.all([api("/libraries"), api("/images?limit=24&sort=newest")]);
  const source = new Source(t("最近添加"));
  source.items = recent.items;
  source.total = recent.total;
  source.nextOffset = recent.items.length;
  const count = t("共 {v0} 张", {v0: recent.total});
  view.innerHTML = `<div class="page-head"><h1>${t("图库")}</h1><span class="count">${count}</span>
    ${state.user.is_admin ? `<div class="toolbar"><button class="btn" data-new-library>${t("添加图库")}</button>${libs.length ? `<button class="btn" data-import>${t("导入图片")}</button>` : ''}</div>` : ""}</div>
    ${!libs.length ? `<section class="empty-state"><h2>${t("还没有图库")}</h2>
      ${state.user.is_admin ? "" : `<p>${t("还没有可访问的图库，请联系管理员。")}</p>`}
      ${state.user.is_admin ? `<button class="btn primary" data-new-library>${t("添加图库")}</button>` : ""}</section>` : `
    <div class="lib-cards home-libraries">${libs.map(lib => `<article class="lib-card">
      <a class="library-title" href="#/library/${lib.id}">
        <span class="library-cover">${lib.cover_image_id ? `<img src="${mediaUrl("thumb", lib.cover_image_id)}" alt="" loading="lazy">` : ""}</span>
        <h3>${escapeHtml(lib.name)}</h3></a>
      <p class="library-count">${t("共 {v0} 张", {v0: lib.image_count})}</p>
    </article>`).join("")}</div>
    <div class="section-head"><h2>${t("最近添加")}</h2><a href="#/search">${t("查看全部")} →</a></div>
    <div class="grid" id="grid">${recent.items.map(item => cardHtml(item, {showLibrary:libs.length > 1})).join("") || `<p class="empty">${t("暂无图片")}</p>`}</div>`}`;
  const grid = view.querySelector("#grid");
  const refresh = () => {
    const opener = document.activeElement?.matches('[data-new-library]') ? '[data-new-library]' : '[data-import]';
    if (view.isConnected) renderHome(view).then(() => view.querySelector(opener)?.focus({preventScroll:true})).catch(error => toast(error.message, 'error'));
  };
  view.querySelectorAll('[data-new-library]').forEach(button => { button.onclick = () => openLibraryForm('managed', refresh); });
  view.querySelector('[data-import]')?.addEventListener('click', () => openImport({onClose:refresh}));
  if (!grid) return;
  grid.querySelectorAll(".card-selection").forEach(label => label.remove());
  function open(event) {
    const card = event.target.closest(".card");
    if (!card) return;
    card.focus();
    openViewer(source, source.items.findIndex(item => String(item.id) === card.dataset.id));
  }
  grid.onclick = open;
  grid.onkeydown = event => {
    if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey || !["Enter", " "].includes(event.key)) return;
    event.preventDefault(); open(event);
  };
  source.onChange = () => {
    grid.querySelectorAll(".card").forEach(card => {
      const item = source.items.find(item => String(item.id) === card.dataset.id);
      if (!item) return;
      card.querySelector(".badges").innerHTML = `${item.my_rating >= 0 ? `<span class="badge rated">${item.my_rating}</span>` : ""}${item.favorite ? '<span class="badge fav">★</span>' : ""}`;
    });
  };
  if (view.isConnected) state.dispose = () => source.cancel();
}

// ---------------------------------------------------------------- 专辑

export async function renderAlbums(view, params) {
  view.classList.add("quiet-collection-page", "albums-page");
  const libraryId = params.get("library_id");
  view.innerHTML = `<div class="page-head"><h1>${t("专辑")}</h1><span class="count" data-list-count></span>
    ${can("tag_edit") ? `<div class="toolbar"><button class="btn primary" data-new-album>${t("新建专辑")}</button></div>` : ""}</div>
    <form class="collection-toolbar" role="search"><input type="search" maxlength="300" aria-label="${t("搜索名称")}" placeholder="${t("搜索名称")}">
      <select aria-label="${t("专辑类型")}"><option value="">${t("全部专辑")}</option><option value="manual">${t("手工专辑")}</option><option value="folder">${t("文件夹")}</option></select></form>
    <p class="error" data-list-error role="status" hidden></p><button class="btn" data-retry hidden>${t("重试")}</button>
    <div class="grid album-grid"></div><div class="load-more"><button class="btn" data-more hidden>${t("加载更多")}</button></div>`;
  const input = view.querySelector("input"), kind = view.querySelector("select"), grid = view.querySelector(".grid");
  const more = view.querySelector("[data-more]"), error = view.querySelector("[data-list-error]"), retry = view.querySelector("[data-retry]");
  let offset = 0, revision = 0, controller, timer, lastReset = true;
  async function load(reset = false) {
    lastReset = reset;
    if (reset) { controller?.abort(); revision++; }
    const requested = revision;
    controller = new AbortController(); more.disabled = true; error.hidden = true; retry.hidden = true;
    try {
      const query = new URLSearchParams({q:input.value.trim(), limit:101, offset:reset ? 0 : offset});
      if (kind.value) query.set("kind", kind.value);
      if (libraryId) query.set("library_id", libraryId);
      const values = await api(`/albums?${query}`, {signal:controller.signal});
      if (requested !== revision || !view.isConnected) return;
      if (reset) { grid.replaceChildren(); offset = 0; }
      grid.insertAdjacentHTML("beforeend", values.slice(0, 100).map(album => {
        const count = album.image_count === 1 ? t("共 1 张") : t("共 {v0} 张", {v0:album.image_count});
        const summary = `${count} · ${t(album.kind === "manual" ? "手工" : "文件夹")}`;
        const label = [album.name, album.library_name !== album.name ? album.library_name : "", summary].filter(Boolean).join(" · ");
        return `<a class="card" href="#/album/${album.id}" aria-label="${escapeHtml(label)}" title="${escapeHtml(album.library_name || album.name)}">
          ${album.preview_image_id ? `<img loading="lazy" decoding="async" src="${mediaUrl("thumb", album.preview_image_id)}" alt="">` : `<div class="empty-cover">${t("空专辑")}</div>`}
          <div class="album-caption"><strong title="${escapeHtml(album.name)}">${escapeHtml(album.name)}</strong><span class="muted">${escapeHtml(summary)}</span></div></a>`;
      }).join(""));
      offset += Math.min(100, values.length); more.hidden = values.length <= 100;
      view.querySelector("[data-list-count]").textContent = t("已显示 {n} 项", {n:offset});
      if (!offset) grid.innerHTML = `<p class="empty">${t(input.value || kind.value ? "没有匹配的结果" : "还没有专辑。")}</p>`;
    } catch (failure) {
      if (failure.name === "AbortError" || requested !== revision) return;
      error.textContent = failure.message; error.hidden = false; retry.hidden = false;
    } finally { if (requested === revision) more.disabled = false; }
  }
  input.oninput = () => { clearTimeout(timer); controller?.abort(); revision++; more.disabled = true; timer = setTimeout(() => load(true), 200); };
  kind.onchange = () => { clearTimeout(timer); load(true); };
  view.querySelector("form").onsubmit = event => { event.preventDefault(); clearTimeout(timer); load(true); };
  more.onclick = () => load(); retry.onclick = () => load(lastReset);
  const create = view.querySelector("[data-new-album]");
  if (create) create.onclick = async () => {
    create.disabled = true;
    try { const album = await editAlbum(null, libraryId); if (album && view.isConnected) await load(true); }
    catch (failure) { toast(failure.message, "error"); } finally { create.disabled = false; }
  };
  state.dispose = () => { clearTimeout(timer); controller?.abort(); revision++; };
  await load(true);
}

export async function renderAlbum(view, albumId, sort = "path") {
  const album = await api(`/albums/${albumId}`);
  const library = album.library_id ? await api(`/libraries/${album.library_id}`) : null;
  const canImport = album.kind === 'manual' && album.can_edit && library && library.mode !== 'indexed' && (state.user.is_admin || library.access === 'manage');
  const source = new Source(
    t("专辑：{v0}", {v0: album.name}),
    { album_id: albumId, title: t("专辑：{v0}", {v0: album.name}) },
    { sort, filters: { album_id: albumId } },
  );
  if (!await loadSource(view, source)) return;
  const extra = [
    album.category ? `<span class="pill">${escapeHtml(album.category)}</span>` : "",
    t("<a class=\"btn small\" href=\"#/albums\">← 全部专辑</a>"),
    album.can_edit ? `<button class="btn small" data-edit-album>${t("编辑专辑")}</button>` : "",
    canImport ? `<button class="btn small" data-import>${t("导入图片")}</button>` : '',
  ].join(" ");
  renderGridInto(view, source, { headerExtraHtml: extra });
  view.querySelector('[data-import]')?.addEventListener('click', () => openImport({libraryId:album.library_id, albumId:album.id, targetName:album.name,
    onClose:() => refreshAfterImport(view, source)}));
  if (album.description) {
    const description = document.createElement("p"); description.className = "album-description"; description.textContent = album.description;
    view.querySelector(".page-head").after(description);
  }
  const edit = view.querySelector("[data-edit-album]");
  if (edit) edit.onclick = async () => {
    try { if (await editAlbum(album) && view.isConnected) { source.cancel(); await renderAlbum(view, albumId, source.sort); } }
    catch (failure) { toast(failure.message, "error"); }
  };
  if (album.kind === "manual" && album.can_edit && !source.total) {
    const empty = view.querySelector(".empty");
    if (empty) empty.innerHTML = `<p>${t("到图库选择图片，再用批量整理加入此专辑。")}</p><a class="btn" href="${album.library_id ? `#/library/${album.library_id}` : "#/search"}">${t("浏览图片")}</a>`;
  }
}

// ---------------------------------------------------------------- 艺术家

async function renderCatalogList(view, kind) {
  const artists = kind === "artists";
  view.innerHTML = `<div class="page-head"><h1>${t(artists ? "艺术家" : "标签")}</h1><span class="count" data-list-count></span></div>
    <form class="entity-search" role="search"><input type="search" maxlength="300" aria-label="${t("搜索名称")}" placeholder="${t("搜索名称")}"></form>
    <p class="error" data-list-error role="status" hidden></p><button class="btn small" data-list-retry hidden>${t("重试")}</button><div class="entity-list"></div>
    <div class="load-more"><button class="btn" data-list-more hidden>${t("加载更多")}</button></div>`;
  const input = view.querySelector("input");
  const list = view.querySelector(".entity-list");
  const more = view.querySelector("[data-list-more]");
  const error = view.querySelector("[data-list-error]");
  const retry = view.querySelector("[data-list-retry]");
  let offset = 0, revision = 0, controller, timer, lastReset = true;
  async function load(reset = false) {
    lastReset = reset;
    if (reset) { controller?.abort(); revision++; }
    const requested = revision;
    controller = new AbortController();
    more.disabled = true;
    error.hidden = true;
    retry.hidden = true;
    try {
      const values = await api(`/${kind}?` + new URLSearchParams({q:input.value.trim(), limit:101, offset:reset ? 0 : offset}), {signal:controller.signal});
      if (requested !== revision || !view.isConnected) return;
      if (reset) { list.replaceChildren(); offset = 0; }
      const rows = values.slice(0, 100);
      list.insertAdjacentHTML("beforeend", rows.map(item => `<div class="entity-row"><a class="entity-link" href="${artists ? `#/artist/${item.id}` : `#/tag/${encodeURIComponent(item.name)}`}">
        <span class="entity-symbol" aria-hidden="true">${artists ? escapeHtml(item.name.slice(0, 1)) : "#"}</span>
        <span class="entity-name">${escapeHtml(item.name)}</span><span class="muted">${t("共 {v0} 张", {v0:artists ? item.image_count : item.count})}</span></a>
        ${!artists && can("admin") ? `<button class="btn small" data-edit-tag="${item.id}" aria-label="${escapeHtml(t("管理标签：{name}", {name:item.name}))}">${t("管理标签")}</button>` : ""}</div>`).join(""));
      for (const item of rows) {
        const edit = list.querySelector(`[data-edit-tag="${item.id}"]`);
        if (edit) edit.onclick = async () => { if (await editTag(item) && view.isConnected) await load(true); };
      }
      offset += rows.length;
      view.querySelector("[data-list-count]").textContent = t("已显示 {n} 项", {n:offset});
      more.hidden = values.length <= 100;
      if (!offset) list.innerHTML = `<p class="empty">${t(input.value ? "没有匹配的结果" : artists ? "在图片详情中关联艺术家。" : "在图片详情中添加标签。")}</p>`;
    } catch (failure) {
      if (failure.name === "AbortError" || requested !== revision) return;
      error.textContent = failure.message;
      error.hidden = false;
      retry.hidden = false;
    } finally { if (requested === revision) more.disabled = false; }
  }
  more.onclick = () => load();
  retry.onclick = () => load(lastReset);
  input.oninput = () => { clearTimeout(timer); controller?.abort(); revision++; more.disabled = true; retry.hidden = true; timer = setTimeout(() => load(true), 200); };
  view.querySelector("form").onsubmit = event => { event.preventDefault(); clearTimeout(timer); load(true); };
  state.dispose = () => { clearTimeout(timer); controller?.abort(); revision++; };
  await load(true);
}

export async function renderArtists(view) { await renderCatalogList(view, "artists"); }

export async function renderArtist(view, artistId, sort = "newest") {
  const artist = await api(`/artists/${artistId}`);
  const source = new Source(
    t("艺术家：{v0}", {v0: artist.name}),
    { artist_id: artistId, title: t("艺术家：{v0}", {v0: artist.name}) },
    { sort, filters: { artist_id: artistId } },
  );
  if (!await loadSource(view, source)) return;
  const links = (artist.links || []).map(externalUrl).filter(Boolean).map((u) =>
    `<a class="pill" href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer">${escapeHtml(u.replace(/^https?:\/\//, ""))}</a>`).join(" ");
  const extra = [artist.aliases ? t("<span class=\"pill\">别名：{v0}</span>", {v0: escapeHtml(artist.aliases)}) : "", links,
    t("<a class=\"btn small\" href=\"#/artists\">← 全部艺术家</a>")].join(" ");
  renderGridInto(view, source, { headerExtraHtml: extra });
}

// ---------------------------------------------------------------- 标签

export async function renderTags(view) { await renderCatalogList(view, "tags"); }

export async function renderTag(view, name, sort = "newest") {
  const source = new Source(t("标签：{name}", {name: name}), { tag: name, title: t("标签：{name}", {name: name}) },
    { filters: { tag: name }, sort });
  if (!await loadSource(view, source)) return;
  renderGridInto(view, source, { headerExtraHtml: t("<a class=\"btn small\" href=\"#/tags\">← 全部标签</a>") });
}

// ---------------------------------------------------------------- 收藏 / 搜索 / 库

export async function renderFavorites(view, sort = "newest") {
  view.classList.add("quiet-collection-page");
  const title = t("收藏");
  const source = new Source(title, { favorites: true, title },
    { sort, filters: { favorites: "true" }, emptyText: t("还没有收藏的图片") });
  if (!await loadSource(view, source)) return;
  renderGridInto(view, source, {});
}

export async function renderInbox(view, sort = "newest") {
  view.classList.add("quiet-collection-page");
  const source=new Source(t("待整理"),{title:t("待整理")},{sort,filters:{workflow:"inbox"}});
  if (!await loadSource(view, source)) return;
  renderGridInto(view,source);
}

export async function renderSearch(view, q, sort="newest") {
  view.classList.add("images-page", "quiet-collection-page");
  const title = t(q ? "搜索结果" : "图片");
  const source = new Source(title, {title, q}, {q, sort});
  if (!await loadSource(view, source)) return;
  renderGridInto(view, source);
  const {attachSearchTools}=await import("./queries.js");
  await attachSearchTools(view,source);
}

export async function renderLibrary(view, libraryId, jobId = null, sort = "newest") {
  const lib = await api(`/libraries/${libraryId}`);
  const source = new Source(t("库：{v0}", {v0: lib.name}), { library_id: libraryId, title: t("库：{v0}", {v0: lib.name}) },
    { sort, filters: { library_id: libraryId } });
  if (!await loadSource(view, source)) return;
  const canManage = state.user.is_admin || lib.access === "manage";
  const scanBtn = canManage ? t("<button class=\"btn small\" id=\"lib-scan\">扫描更新</button>") : "";
  const last = lib.last_scan
    ? t("<span class=\"muted\">上次扫描：{v0}</span>", {v0: escapeHtml((lib.last_scan.finished_at || lib.last_scan.started_at || "").replace("T", " ").slice(0, 16))})
    : "";
  renderGridInto(view, source, {
    headerExtraHtml: `<a class="btn small" href="#/albums?library_id=${libraryId}">${t("专辑")}</a>
      ${canManage && lib.mode !== "indexed" ? `<button class="btn small" data-import>${t("导入图片")}</button>` : ""}${scanBtn}`,
  });
  view.querySelector('[data-import]')?.addEventListener('click', () => openImport({libraryId,
    onClose:() => refreshAfterImport(view, source)}));
  if (last && canManage) {
    const caption = document.createElement("p");
    caption.className = "collection-caption";
    caption.innerHTML = last;
    view.querySelector(".page-head").after(caption);
  }
  const scanEl = view.querySelector("#lib-scan");
  let scanTarget, scanRequestKey;
  async function scan(existingJob = null) {
    scanEl.disabled = true;
    scanEl.textContent = t("扫描中…");
    try {
      const queued = existingJob ? {id:existingJob} : await api(`/libraries/${libraryId}/scan-jobs`, { method: "POST", body: {request_key: scanRequestKey ||= jobKey()} });
      scanRequestKey = null;
      if (!scanEl.isConnected) return;
      history.replaceState(null, "", `#/library/${libraryId}?sort=${source.sort}&job=${encodeURIComponent(queued.id)}`);
      if (!scanTarget) {
        scanTarget = document.createElement("div");
        view.querySelector(".page-head").after(scanTarget);
      }
      const status = await watchJob(queued.id, scanTarget);
      if (!status || !scanEl.isConnected) return;
      if (status.state === "succeeded") {
        toast(status.result ? scanSummary(status.result) : t("扫描完成"), "ok");
        history.replaceState(null, "", `#/library/${libraryId}?sort=${source.sort}`);
        await renderLibrary(view, libraryId, null, source.sort);
      }
    } catch (e) {
      if (scanEl.isConnected) toast(e.message, "error");
    } finally {
      scanEl.disabled = false;
      scanEl.textContent = t("扫描更新");
    }
  }
  if (scanEl) scanEl.addEventListener("click", () => scan());
  if (scanEl && jobId && view.isConnected) await scan(jobId);
}

// ---------------------------------------------------------------- 深链（直接打开某张图）

export async function renderDeepLink(view, imageId, params, sort) {
  let source;
  if (params.get("album")) {
    const album = await api(`/albums/${params.get("album")}`);
    source = new Source(t("专辑：{v0}", {v0: album.name}), { album_id: album.id, title: t("专辑：{v0}", {v0: album.name}) },
      { sort:sort || "path", filters: { album_id: album.id } });
  } else if (params.get("artist")) {
    const artist = await api(`/artists/${params.get("artist")}`);
    source = new Source(t("艺术家：{v0}", {v0: artist.name}), { artist_id: artist.id, title: t("艺术家：{v0}", {v0: artist.name}) },
      { sort:sort || "newest", filters: { artist_id: artist.id } });
  } else if (params.get("tag")) {
    source = new Source(t("标签：{v0}", {v0: params.get("tag")}), { tag: params.get("tag") },
      { sort:sort || "newest", filters: { tag: params.get("tag") } });
  } else {
    source = new Source(t("图片"), {}, {sort:sort || "newest"});
  }
  const image = await api(`/images/${imageId}`);
  if (!await loadSource(view, source)) return;
  const index = source.items.findIndex(item => item.id === imageId);
  if (!view.isConnected) return;
  renderGridInto(view, source);
  if (index < 0) {
    // Keep the collection behind a direct link, without paging through it to find one image.
    const direct = new Source(image.filename);
    Object.assign(direct, {items:[image], total:1, nextOffset:1, singleImage:true});
    openViewer(direct, 0);
  } else openViewer(source, index);
}
