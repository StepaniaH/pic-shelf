// 查看器：大图 + 信息面板（评分/标签/艺术家/专辑/评论/笔记/元数据）。
import { api, mediaUrl, imageMediaUrl } from "./api.js";
import { escapeHtml, toast, can, state, uiConfirm, showModal } from "./app.js";
import { openSlideshow } from "./slideshow.js";
import { t } from "./i18n.js";
import {metadataHtml, workflowHtml, bindMetadata} from "./metadata.js";
import {formatTimestamp} from "./format.js";
import { activateDialog, isTopDialog } from "./dialog.js";
import {ViewerLoader, PreviewCache, decodeImage} from "./viewer-load.js";
import {pickEntity} from "./catalog-ui.js";

const SIZE_LABEL = { fit: t("适应"), fill: t("填满"), original: t("原图") };
const SIZE_CYCLE = ["fit", "fill", "original"];

export function openViewer(source, index) {
  let current = index;
  const preference = (key, fallback) => { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } };
  let zoom = preference("picshelf.zoom", "fit");
  if (!SIZE_LABEL[zoom]) zoom = "fit";
  let panelOpen = preference("picshelf.panel", matchMedia("(max-width: 900px)").matches ? "0" : "1") !== "0";
  const sections = new Set();
  let detail = null;
  let closed = false;
  let showVersion = 0;
  let frame = null, status = "", saving = false, closing = false;
  let renderedId = null, renderedWithDetails = false, transparent = false;
  let changed = false;
  const drafts = new Map();
  const previews = new PreviewCache();
  const loader = new ViewerLoader({
    read: (id, signal) => api(`/images/${id}`, {signal}),
    decode: (item, mode, signal) => {
      const url = imageMediaUrl(mode === "original" ? "original" : "preview", item);
      return mode === "original" ? decodeImage(url, signal) : previews.load(url, signal);
    },
  });

  const backdrop = document.createElement("div");
  backdrop.className = "viewer-backdrop";
  document.body.appendChild(backdrop);
  backdrop.setAttribute("aria-label", source.title);
  const releaseFocus = activateDialog(backdrop, () => {
    const menu = backdrop.querySelector(".viewer-more[open]");
    if (menu) { menu.open = false; menu.querySelector("summary").focus(); }
    else close();
  }, {dismiss: () => close({force:true})});
  backdrop.addEventListener("click", event => {
    const menu = backdrop.querySelector(".viewer-more");
    if (menu && !menu.contains(event.target)) menu.open = false;
  });

  const savePrefs = () => {
    try {
      localStorage.setItem("picshelf.zoom", zoom);
      localStorage.setItem("picshelf.panel", panelOpen ? "1" : "0");
    } catch { /* Preferences are optional. */ }
  };

  function captureDrafts() {
    if (closed || renderedId === null || !renderedWithDetails) return;
    for (const section of backdrop.querySelectorAll("details[data-section]")) {
      if (section.open) sections.add(section.dataset.section);
      else sections.delete(section.dataset.section);
    }
    const values = {};
    for (const input of backdrop.querySelectorAll(".viewer-panel input, .viewer-panel textarea, .viewer-panel select")) {
      const key = input.id ? `#${input.id}` : input.name ? `[name="${input.name}"]` : null;
      if (key && input.value) values[key] = input.value;
    }
    if (Object.keys(values).length) drafts.set(renderedId, values);
    else drafts.delete(renderedId);
  }

  function setStatus(message) {
    status = message;
    const el = backdrop.querySelector(".viewer-status");
    if (el) { el.textContent = message; el.hidden = !message; }
  }

  async function show(i) {
    if (closed) return;
    if (saving) { setStatus(t("正在保存…")); return; }
    captureDrafts();
    const version = ++showVersion;
    loader.cancel();
    try {
      if (i >= source.items.length && !source.loadedAll) await source.loadMore();
      if (closed || version !== showVersion || !source.items.length) return;
      current = Math.max(0, Math.min(i, source.items.length - 1));
      detail = null; frame = null; status = t("加载中…");
      render();
      const result = await loader.load(source.items[current].id, zoom);
      if (!result || closed || version !== showVersion) return;
      detail = result.detail; frame = result.image;
      Object.assign(source.items[current], {favorite:detail.favorite, my_rating:detail.my_rating});
      status = drafts.has(detail.id) ? t("尚未保存") : "";
      render();
      if (zoom !== "original" && !document.hidden && !navigator.connection?.saveData) {
        const adjacent = [source.items[current + 1], source.items[current - 1]].filter(item => item && !item.missing);
        previews.warm(adjacent.map(item => imageMediaUrl("preview", item)));
      }
    } catch (error) {
      if (closed || version !== showVersion) return;
      if ([401,403,404].includes(error.status)) previews.clear();
      detail = null; frame = null;
      status = error.status !== undefined ? `${error.message} ${error.nextStep || ""}` : t("图片无法显示，请重试或跳到下一张。");
      render();
      backdrop.querySelector("#v-retry").hidden = false;
    }
  }

  async function refresh(id = source.items[current]?.id) {
    if (closed || id !== source.items[current]?.id) return;
    const version = showVersion;
    try {
      const loaded = await api(`/images/${id}`);
      if (closed || version !== showVersion || id !== source.items[current]?.id) return;
      const kind = zoom === "original" ? "original" : "preview";
      if (!detail || imageMediaUrl(kind, detail) !== imageMediaUrl(kind, loaded)) {
        await show(current);
        return;
      }
      detail = loaded;
      Object.assign(source.items[current], {favorite:detail.favorite, my_rating:detail.my_rating});
      render();
    } catch (error) {
      if (closed || version !== showVersion) return;
      if ([401,403,404].includes(error.status)) { detail = null; frame = null; render(); }
      setStatus(t("详情刷新失败，请重试。") + " " + error.message);
      backdrop.querySelector("#v-retry").hidden = false;
    }
    source.onChange?.();
  }

  // ---------------------------------------------------------------- 渲染

  function render() {
    if (closed) return;
    captureDrafts();
    const active = backdrop.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = active?.id ? `#${CSS.escape(active.id)}` : active?.name ? `[name="${CSS.escape(active.name)}"]`
      : active?.dataset.rate ? `[data-rate="${active.dataset.rate}"]` : null;
    const item = source.items[current];
    if (!item) return;
    const panelScroll = renderedId === item.id ? backdrop.querySelector(".viewer-panel")?.scrollTop || 0 : 0;
    const loaded = source.sort === "random"
      ? t("本次抽样 {n} / 匹配 {total}", {n: source.items.length, total: source.total})
      : `${current + 1} / ${source.total ?? source.items.length}`;
    backdrop.innerHTML = `<div class="viewer-top">
      <span class="title">${escapeHtml(item.filename)}</span><span class="muted meta-count">${loaded}</span>
      <div class="viewer-actions">
        ${source.singleImage ? "" : `<button class="btn small" id="v-slideshow">${t("幻灯片")}</button>`}
        <button class="btn small" id="v-zoom">${t("尺寸：{value}", {value:SIZE_LABEL[zoom]})}</button>
        ${can("rate") ? `<button class="btn small" id="v-fav" aria-pressed="${!!item.favorite}" ${!detail ? "disabled" : ""}>${item.favorite ? t("★ 已收藏") : t("☆ 收藏")}</button>` : ""}
        <button class="btn small" id="v-panel" aria-expanded="${panelOpen}" aria-controls="viewer-details">${t("信息")}</button>
        <details class="viewer-more"><summary class="btn small" aria-label="${t("更多操作")}">•••</summary><div class="account-actions">
          <button class="btn small" id="v-bg" aria-pressed="${transparent}">${t("透明背景")}</button>
          ${detail ? `<a class="btn small" href="${mediaUrl("original", item.id)}?download=1">${t("下载原图")}</a>` : ""}
        </div></details>
      </div><button class="icon-btn" id="v-close" aria-label="${t("关闭")}" title="${t("关闭")}"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>
      <p class="viewer-status" role="status" ${status ? "" : "hidden"}>${escapeHtml(status)}</p>
      <button class="btn" id="v-retry" hidden>${t("重试")}</button>
      <div class="viewer-body"><div class="viewer-stage size-${zoom} ${transparent ? "checkerboard" : ""}">
        <button class="nav-btn prev" aria-label="${t("上一张")}" ${current === 0 ? "disabled" : ""}>‹</button>
        <button class="nav-btn next" aria-label="${t("下一张")}" ${source.loadedAll && current >= source.items.length - 1 ? "disabled" : ""}>›</button>
      </div><aside id="viewer-details" class="viewer-panel" ${panelOpen ? "" : "hidden"}>${detail ? panelHtml(item) : ""}</aside></div>`;
    if (frame) {
      frame.alt = detail.filename;
      backdrop.querySelector(".viewer-stage").prepend(frame);
    }
    renderedId = item.id;
    renderedWithDetails = !!detail;
    for (const [key, value] of Object.entries(drafts.get(item.id) || {})) {
      const input = backdrop.querySelector(key);
      if (input) input.value = value;
    }
    for (const section of backdrop.querySelectorAll("details[data-section]")) section.open = sections.has(section.dataset.section);
    bind(item);
    const panel = backdrop.querySelector(".viewer-panel");
    panel.scrollTop = panelScroll;
    if (focusKey && isTopDialog(backdrop)) backdrop.querySelector(focusKey)?.focus({preventScroll:true});
  }

  function workHtml(d) {
    if (!d.work) return "";
    const labels = {original: "原图版本", compressed: "压缩版", crop: "裁剪版", resize: "尺寸版本", revision: "修订版", other: "其他版本"};
    return `<section><h3>${t("作品文件")} · ${escapeHtml(d.work.name)}</h3><div class="add-row">${d.work.images.map(image =>
      `<button class="btn small" data-work-image="${image.id}" ${image.missing || image.id === d.id ? "disabled" : ""}>${escapeHtml(image.filename)} · ${t(labels[image.role])}${image.id === d.work.preferred_image_id ? ` · ${t("首选")}` : ""}</button>`
    ).join("")}</div></section>`;
  }

  function panelHtml(item) {
    const d = detail;
    if (!d) return "";
    const editable = d.metadata_editable;
    const chips = (items, render) => items.map(render).join("");
    return workflowHtml(d) + `
      <section><h3>${t("我的评分")} <span class="muted">${t("平均 {score}", {score:d.avg_rating ?? "—"})}</span></h3>
        ${can("rate") ? `<div class="rate-row">${Array.from({length:11}, (_,value) =>
          `<button class="rate-btn ${d.my_rating === value ? "on" : ""}" data-rate="${value}" aria-pressed="${d.my_rating === value}" title="${t("{n} 分", {n:value})}">${value}</button>`).join("")}
          <button class="rate-btn" data-rate="clear" title="${t("清除评分")}">×</button></div>` : `<p class="muted">${d.my_rating >= 0 ? d.my_rating : t("未评")}</p>`}
      </section>
      <section><h3>${t("标签")}</h3><div>${chips(d.tags || [], tag =>
        `<span class="tag-chip pill">${escapeHtml(tag.name)}${editable ? `<button data-untag="${escapeHtml(tag.name)}" title="${t("移除")}">×</button>` : ""}</span>`)}</div>
        ${editable ? `<div class="add-row"><input list="tag-dl" id="tag-input" aria-label="${t("添加标签")}" placeholder="${t("添加标签")}"><datalist id="tag-dl"></datalist><button class="btn small" id="tag-add">${t("添加")}</button></div>` : ""}
      </section>
      <section><h3>${t("艺术家")}</h3><div>${chips(d.artists || [], artist =>
        `<span class="tag-chip pill">${escapeHtml(artist.name)}${editable ? `<button data-unartist="${artist.id}" title="${t("取消关联")}">×</button>` : ""}</span>`)}</div>
        ${editable ? `<div class="add-row"><input list="artist-dl" id="artist-input" aria-label="${t("关联艺术家")}" placeholder="${t("关联艺术家")}"><datalist id="artist-dl"></datalist><button class="btn small" id="artist-add">${t("关联")}</button></div>` : ""}
      </section>
      <section><h3>${t("专辑")}</h3><div>${chips(d.albums || [], album => `<button class="tag-chip pill" data-album="${album.id}">${escapeHtml(album.name)}</button>`)}</div>
        ${editable ? `<div class="add-row"><button class="btn small" id="album-add">${t("加入手工专辑")}</button></div>` : ""}
      </section>
      ${workHtml(d)}
      <details data-section="comments"><summary>${t("评论")} · ${(d.comments || []).length}</summary>
        <div id="comment-list">${chips(d.comments || [], comment => `<div class="comment"><div class="meta">${escapeHtml(comment.username)} · ${formatTimestamp(comment.created_at)}
          ${comment.user_id === state.user.id || state.user.is_admin ? `<button class="icon-btn" data-del-comment="${comment.id}" title="${t("删除")}">×</button>` : ""}</div>${escapeHtml(comment.body)}</div>`)}</div>
        ${can("comment") ? `<div class="add-row"><textarea id="comment-input" rows="2" aria-label="${t("写评论…")}" placeholder="${t("写评论…")}"></textarea><button class="btn small" id="comment-add">${t("发表")}</button></div>` : ""}
      </details>
      <details data-section="notes"><summary>${t("笔记")} · ${(d.notes || []).length}</summary>
        ${chips(d.notes || [], note => `<div class="comment"><div class="meta">${escapeHtml(note.username)}</div>${escapeHtml(note.body)}</div>`)}
        ${can("note") ? `<div class="add-row"><input id="note-input" aria-label="${t("写笔记…")}" placeholder="${t("写笔记…")}"><button class="btn small" id="note-add">${t("添加")}</button></div>` : ""}
      </details>
      ${metadataHtml(d)}
      <details data-section="file"><summary>${t("文件信息")}</summary><div class="kv">
        <div><b>${escapeHtml(d.filename)}</b></div>
        <div>${d.width} × ${d.height} · ${(d.size_bytes / 1024).toFixed(1)} KB · ${escapeHtml(d.ext)}</div>
        <div>${t("库")}：<b>${escapeHtml(d.library?.name || "—")}</b></div>
        <div>${t("路径")}：<b>${escapeHtml(d.rel_path)}</b></div>
        <div>${t("首次入库时间")}：${formatTimestamp(d.imported_at)}</div>
        <div>SHA-256：<b>${escapeHtml(d.sha256 || "—")}</b></div>
        <div>pHash：<b>${escapeHtml(d.phash || "—")}</b></div>
      </div></details>`;
  }

  // ---------------------------------------------------------------- 事件

  function bind(item) {
    const q = (sel) => backdrop.querySelector(sel);
    q("#v-close").addEventListener("click", close);
    q("#v-retry").onclick = () => show(current);
    q("#v-bg").onclick = () => { transparent = !transparent; render(); };
    async function write(path, options, clear = []) {
      if (saving) throw new Error(t("正在保存…"));
      saving = true;
      setStatus(t("正在保存…"));
      const controls = [...backdrop.querySelectorAll("button, input, select, textarea")].map(el => [el, el.disabled]);
      controls.forEach(([el]) => { el.disabled = true; });
      try {
        const result = await api(path, options);
        changed = true;
        for (const selector of clear) backdrop.querySelectorAll(selector).forEach(el => { el.value = ""; });
        captureDrafts();
        setStatus(t("已保存"));
        return result;
      } catch (error) {
        if (closed) throw error;
        if (!error.status || error.status >= 500) changed = true;
        if ([401,403,404].includes(error.status)) { captureDrafts(); detail = null; frame = null; previews.clear(); render(); }
        setStatus((error.status && error.status < 500 ? t("保存失败。") : t("保存结果未确认，请刷新核对后再提交。")) + " " + error.message);
        const retry = q("#v-retry");
        retry.hidden = false;
        const previous = controls.find(([el]) => el === retry);
        if (previous) previous[1] = false;
        throw error;
      } finally {
        saving = false;
        controls.forEach(([el, disabled]) => { el.disabled = disabled; });
      }
    }
    q(".nav-btn.prev").addEventListener("click", () => show(current - 1));
    q(".nav-btn.next").addEventListener("click", () => show(current + 1));
    q("#v-slideshow")?.addEventListener("click", () => openSlideshow(source, {startImage:source.items[current]}));
    for (const button of backdrop.querySelectorAll("[data-work-image]")) button.addEventListener("click", () => {
      if (saving) { setStatus(t("正在保存…")); return; }
      const id = Number(button.dataset.workImage);
      const known = source.items.findIndex(image => image.id === id);
      if (known !== -1) { show(known); return; }
      const related = detail.work.images.filter(image => !image.missing);
      openViewer({title: detail.work.name, items: related, loadedAll: true, onChange: () => refresh(item.id)}, related.findIndex(image => image.id === id));
    });
    q("#v-zoom").addEventListener("click", () => {
      zoom = SIZE_CYCLE[(SIZE_CYCLE.indexOf(zoom) + 1) % SIZE_CYCLE.length];
      savePrefs();
      show(current);
    });
    const favBtn = q("#v-fav");
    if (favBtn) favBtn.addEventListener("click", async () => {
      try {
        const r = await write(`/images/${item.id}/favorite`, { method: "POST", body: {} });
        item.favorite = r.favorite ? 1 : 0;
        if (detail && source.items[current]?.id === item.id) detail.favorite = item.favorite;
        render();
        if (source.onChange) source.onChange();
      } catch (error) { toast(error.message, "error"); }
    });
    q("#v-panel").addEventListener("click", () => {
      panelOpen = !panelOpen;
      savePrefs();
      q(".viewer-panel").hidden = !panelOpen;
      q("#v-panel").setAttribute("aria-expanded", String(panelOpen));
    });

    if (!detail) return;
    q(".viewer-panel").addEventListener("input", () => setStatus(t("尚未保存")));
    bindMetadata(backdrop, detail, () => refresh(item.id), write);
    backdrop.querySelectorAll("[data-rate]").forEach((btn) => btn.addEventListener("click", async () => {
      const v = btn.dataset.rate === "clear" ? null : Number(btn.dataset.rate);
      try {
        const r = await write(`/images/${item.id}/rating`, { method: "POST", body: { value: v } });
        item.my_rating = r.my_rating;
        if (source.onChange) source.onChange();
        if (closed || source.items[current]?.id !== item.id) return;
        detail.my_rating = r.my_rating;
        detail.avg_rating = r.avg_rating;
        detail.rating_count = r.rating_count;
        render();
      } catch (e) { toast(e.message, "error"); }
    }));

    const tagInput = q("#tag-input");
    if (tagInput) {
      const dl = q("#tag-dl");
      let timer = null;
      tagInput.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try {
            const tags = await api("/tags?limit=12&q=" + encodeURIComponent(tagInput.value));
            dl.innerHTML = tags.map((tag) => `<option value="${escapeHtml(tag.name)}">`).join("");
          } catch { /* ignore */ }
        }, 200);
      });
      const addTag = async () => {
        const name = tagInput.value.trim();
        if (!name) return;
        try {
          await write(`/images/${item.id}/tags`, { method: "POST", body: { names: [name] } }, ["#tag-input"]);
          await refresh(item.id);
        } catch (e) { toast(e.message, "error"); }
      };
      q("#tag-add").addEventListener("click", addTag);
      tagInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addTag(); } });
    }
    backdrop.querySelectorAll("[data-untag]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await write(`/images/${item.id}/tags/${encodeURIComponent(btn.dataset.untag)}`, { method: "DELETE" });
        await refresh(item.id);
      } catch (e) { toast(e.message, "error"); }
    }));

    const artistInput = q("#artist-input");
    if (artistInput) {
      const dl = q("#artist-dl");
      let timer = null;
      artistInput.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try {
            const artists = await api("/artists?limit=12&q=" + encodeURIComponent(artistInput.value));
            dl.innerHTML = artists.map((a) => `<option value="${escapeHtml(a.name)}">`).join("");
          } catch { /* ignore */ }
        }, 200);
      });
      const addArtist = async () => {
        const name = artistInput.value.trim();
        if (!name) return;
        try {
          const found = await api("/artists?limit=1&q=" + encodeURIComponent(name));
          if (closed || source.items[current]?.id !== item.id || !artistInput.isConnected) return;
          let artistId = found[0] && found[0].name === name ? found[0].id : null;
          if (!artistId) {
            const created = await write("/artists", { method: "POST", body: { name } });
            artistId = created.id;
          }
          await write(`/images/${item.id}/artists`, { method: "POST", body: { artist_id: artistId, on: true } }, ["#artist-input"]);
          await refresh(item.id);
        } catch (e) { toast(e.message, "error"); }
      };
      q("#artist-add").addEventListener("click", addArtist);
      artistInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addArtist(); } });
    }
    backdrop.querySelectorAll("[data-unartist]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await write(`/images/${item.id}/artists`, { method: "POST", body: { artist_id: Number(btn.dataset.unartist), on: false } });
        await refresh(item.id);
      } catch (e) { toast(e.message, "error"); }
    }));

    const albumAdd = q("#album-add");
    if (albumAdd) albumAdd.onclick = async () => {
      albumAdd.disabled = true;
      try {
        const album = await pickEntity("albums", {libraryId:item.library_id, allowCreate:true});
        if (!album || closed || source.items[current]?.id !== item.id) return;
        await write(`/albums/${album.id}/images`, {method:"POST", body:{image_ids:[item.id]}});
        await refresh(item.id);
      } catch (error) { toast(error.message, "error"); } finally { albumAdd.disabled = false; }
    };
    backdrop.querySelectorAll("[data-album]").forEach(button => { button.onclick = async () => {
      button.disabled = true;
      try {
        const album = await api(`/albums/${button.dataset.album}`);
        if (closed || source.items[current]?.id !== item.id) return;
        showModal({title:album.name,
          bodyHtml:`<div class="catalog-form"><button class="btn" data-browse>${t("浏览专辑")}</button>
            ${album.can_edit ? `<button class="btn" data-cover>${t("设为封面")}</button>` : ""}
            ${album.can_edit && album.kind === "manual" ? `<button class="btn danger" data-remove>${t("移出专辑")}</button>` : ""}</div>
            <p class="error" role="status" hidden></p>`,
          onMount(el, dismiss) {
            el.querySelector("[data-browse]").onclick = async () => { dismiss(); await close(); if (closed) location.hash = `#/album/${album.id}`; };
            for (const [selector, path, options] of [
              ["[data-cover]", `/albums/${album.id}`, {method:"PATCH", body:{cover_image_id:item.id}}],
              ["[data-remove]", `/albums/${album.id}/images/${item.id}`, {method:"DELETE"}],
            ]) {
              const action = el.querySelector(selector);
              if (!action) continue;
              action.onclick = async () => {
                if (selector === "[data-remove]" && !await uiConfirm(t("移出专辑"), t("将这张图片移出专辑？原文件会保留。"))) return;
                action.disabled = true;
                try { await write(path, options); dismiss(); await refresh(item.id); toast(t("已保存")); }
                catch (error) { const message = el.querySelector(".error"); message.textContent = error.message; message.hidden = false; action.disabled = false; }
              };
            }
          },
        });
      } catch (error) { toast(error.message, "error"); } finally { button.disabled = false; }
    }; });

    const commentAdd = q("#comment-add");
    if (commentAdd) commentAdd.addEventListener("click", async () => {
      const body = q("#comment-input").value.trim();
      if (!body) return;
      try {
        await write(`/images/${item.id}/comments`, { method: "POST", body: { body } }, ["#comment-input"]);
        toast(t("评论已发表"), "ok");
        await refresh(item.id);
      } catch (e) { toast(e.message, "error"); }
    });
    backdrop.querySelectorAll("[data-del-comment]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!(await uiConfirm(t("删除评论"), t("确认删除这条评论？")))) return;
      try {
        await write(`/comments/${btn.dataset.delComment}`, { method: "DELETE" });
        toast(t("评论已删除"), "ok");
        await refresh(item.id);
      } catch (e) { toast(e.message, "error"); }
    }));

    const noteAdd = q("#note-add");
    if (noteAdd) noteAdd.addEventListener("click", async () => {
      const body = q("#note-input").value.trim();
      if (!body) return;
      try {
        await write(`/images/${item.id}/notes`, { method: "POST", body: { body } }, ["#note-input"]);
        toast(t("笔记已添加"), "ok");
        await refresh(item.id);
      } catch (e) { toast(e.message, "error"); }
    });
  }

  function onKey(e) {
    if (!isTopDialog(backdrop) || e.isComposing || e.keyCode === 229 || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " " && e.target.closest("button, a, summary")) return;
    if (e.target?.closest("input, textarea, select, [contenteditable=true]")) return;
    switch (e.key) {
      case "ArrowRight": case " ": e.preventDefault(); show(current + 1); break;
      case "ArrowLeft": e.preventDefault(); show(current - 1); break;
      case "Escape": close(); break;
      case "f": backdrop.querySelector("#v-fav")?.click(); break;
      case "i": q_panel_toggle(); break;
      case "z": backdrop.querySelector("#v-zoom")?.click(); break;
      default: break;
    }
  }
  function q_panel_toggle() { backdrop.querySelector("#v-panel")?.click(); }

  async function close({force = false} = {}) {
    if (closed || (closing && !force)) return;
    if (saving && !force) { setStatus(t("正在保存…")); return; }
    if (!force) captureDrafts();
    if (!force && drafts.size) {
      closing = true;
      const discard = await uiConfirm(t("关闭图片"), t("关闭会丢弃尚未保存的内容。"));
      closing = false;
      if (!discard || closed) return;
    }
    closed = true;
    showVersion++; loader.cancel(); previews.clear(); drafts.clear();
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    releaseFocus();
    detail = null; frame = null;
    if (force) return;
    try { if (changed) await source.refresh?.({afterWrite:true}); }
    catch (error) { toast(error.message,"error"); }
    source.onChange?.();
  }

  document.addEventListener("keydown", onKey);
  show(index);
}
