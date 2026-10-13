// 查看器：大图 + 信息面板（评分/标签/艺术家/专辑/评论/笔记/元数据）。
import { api, mediaUrl, imageMediaUrl } from "./api.js";
import { escapeHtml, toast, can, state, uiConfirm } from "./app.js";
import { openSlideshow } from "./slideshow.js";
import { t } from "./i18n.js";
import {metadataHtml, bindMetadata} from "./metadata.js";
import {formatTimestamp} from "./format.js";
import { activateDialog, isTopDialog } from "./dialog.js";
import {ViewerLoader, decodeImage} from "./viewer-load.js";

const SIZE_LABEL = { fit: t("适应"), fill: t("填满"), original: t("原图") };
const SIZE_CYCLE = ["fit", "fill", "original"];

export function openViewer(source, index) {
  let current = index;
  let zoom = localStorage.getItem("picshelf.zoom") || "fit";
  if (!SIZE_LABEL[zoom]) zoom = "fit";
  let panelOpen = (localStorage.getItem("picshelf.panel") ?? (matchMedia("(max-width: 900px)").matches ? "0" : "1")) !== "0";
  let detail = null;
  let closed = false;
  let showVersion = 0;
  let frame = null, status = "", saving = false, closing = false;
  let renderedId = null, renderedWithDetails = false, transparent = false;
  const drafts = new Map();
  const previews = new Map();
  let prefetch = new AbortController();
  const loader = new ViewerLoader({
    read: (id, signal) => api(`/images/${id}`, {signal}),
    decode: (item, mode, signal) => {
      const url = imageMediaUrl(mode === "original" ? "original" : "preview", item);
      const cached = previews.get(url);
      previews.delete(url);
      return cached || decodeImage(url, signal);
    },
  });

  const backdrop = document.createElement("div");
  backdrop.className = "viewer-backdrop";
  document.body.appendChild(backdrop);
  backdrop.setAttribute("aria-label", source.title);
  const releaseFocus = activateDialog(backdrop, close);

  const savePrefs = () => {
    localStorage.setItem("picshelf.zoom", zoom);
    localStorage.setItem("picshelf.panel", panelOpen ? "1" : "0");
  };

  function captureDrafts() {
    if (renderedId === null || !renderedWithDetails) return;
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
    if (saving) { setStatus(t("正在保存，请稍候。")); return; }
    captureDrafts();
    const version = ++showVersion;
    loader.cancel(); prefetch.abort();
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
      status = drafts.has(detail.id) ? t("输入尚未保存。") : "";
      render();
      if (!navigator.connection?.saveData) {
        prefetch = new AbortController();
        const signal = prefetch.signal;
        const adjacent = [source.items[current + 1], source.items[current - 1]].filter(item => item && !item.missing);
        const urls = adjacent.map(item => imageMediaUrl("preview", item));
        for (const url of previews.keys()) if (!urls.includes(url)) previews.delete(url);
        for (const url of urls) {
          if (!previews.has(url)) decodeImage(url, signal).then(image => {
            if (!closed && !signal.aborted) previews.set(url, image);
          }).catch(() => {});
        }
      }
    } catch (error) {
      if (closed || version !== showVersion) return;
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
      setStatus(t("详情尚未刷新。已确认的保存仍有效，请刷新后核对。") + " " + error.message);
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
    const loaded = source.loadedAll ? t("共 {v0}", {v0: source.total}) : t("已加载 {v0} / 共 {v1}", {v0: source.items.length, v1: source.total});
    backdrop.innerHTML = `<div class="viewer-top">
      <span class="title">${escapeHtml(item.filename)}</span><span class="muted meta-count">${current + 1} / ${source.items.length} (${loaded})</span>
      <div class="viewer-actions">
        <button class="btn small" id="v-slideshow">${t("幻灯片")}</button>
        <button class="btn small" id="v-zoom">${t("尺寸：{value}", {value:SIZE_LABEL[zoom]})}</button>
        <button class="btn small" id="v-bg" aria-pressed="${transparent}">${t("透明背景")}</button>
        ${can("rate") ? `<button class="btn small" id="v-fav" aria-pressed="${!!item.favorite}" ${!detail ? "disabled" : ""}>${item.favorite ? t("★ 已收藏") : t("☆ 收藏")}</button>` : ""}
        <button class="btn small" id="v-panel" aria-expanded="${panelOpen}" aria-controls="viewer-details">${t("信息")}</button>
        ${detail ? `<a class="btn small" href="${mediaUrl("original", item.id)}?download=1">${t("下载原图")}</a>` : ""}
        <button class="btn small" id="v-close">${t("关闭")} ✕</button>
      </div></div>
      <p class="viewer-status" role="status" ${status ? "" : "hidden"}>${escapeHtml(status)}</p>
      <button class="btn" id="v-retry" hidden>${t("重试")}</button>
      <div class="viewer-body"><div class="viewer-stage size-${zoom} ${transparent ? "checkerboard" : ""}">
        <button class="nav-btn prev" aria-label="${t("上一张")}" ${current === 0 ? "disabled" : ""}>‹</button>
        <button class="nav-btn next" aria-label="${t("下一张")}" ${current >= source.total - 1 ? "disabled" : ""}>›</button>
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
    if (!detail) return t("<p class=\"muted\">加载中…</p>");
    const d = detail;
    return metadataHtml(d) + workHtml(d) + t("\n      <section>\n        <h3>评分 <span class=\"muted\">（平均 {v0} · {v1} 人）</span></h3>\n        {v2}\n      </section>\n      <section>\n        <h3>标签</h3>\n        <div>{v3}\n        </div>\n        {v4}\n      </section>\n      <section>\n        <h3>艺术家</h3>\n        <div>{v5}\n        </div>\n        {v6}\n      </section>\n      <section>\n        <h3>专辑</h3>\n        <div>{v7}</div>\n        {v8}\n      </section>\n      <section>\n        <h3>评论（{v9}）</h3>\n        <div id=\"comment-list\">{v10}</div>\n        {v11}\n      </section>\n      <section>\n        <h3>笔记（{v12}）</h3>\n        {v13}\n        {v14}\n      </section>\n      <section>\n        <h3>信息</h3>\n        <div class=\"kv\">\n          <div>文件名：<b>{v15}</b></div>\n          <div>尺寸：<b>{v16}×{v17}</b> · 大小：<b>{v18} KB</b> · 格式：<b>{v19}</b></div>\n          <div>库：<b>{v20}</b></div>\n          <div>路径：<b>{v21}</b></div>\n          <div>首次入库时间：<b>{v22}</b></div>\n          <div>指纹：<b title=\"{v23}\">{v24}…</b> / pHash <b>{v25}</b></div>\n        </div>\n      </section>", {v0: d.avg_rating ?? "—", v1: d.rating_count, v2: can("rate") ? `<div class="rate-row">${Array.from({ length: 11 }, (_, i) => i).map((v) =>
          `<button class="rate-btn ${d.my_rating === v ? "on" : ""}" data-rate="${v}" title="${t("{n} 分", {n: v})}">${v}</button>`).join("")}
          <button class="rate-btn" data-rate="clear" style="width:auto;padding:0 10px" title="${t("清除评分")}">${t("清除")}</button></div>`
        : `<p class="muted">${t("我的评分：{v0}", {v0: d.my_rating >= 0 ? d.my_rating : t("未评")})}</p>`, v3: (d.tags || []).map((tag) => `
          <span class="tag-chip pill">${escapeHtml(tag.name)}${d.metadata_editable ? `<button data-untag="${escapeHtml(tag.name)}" title="${t("移除")}">✕</button>` : ""}</span>`).join("") || `<span class="muted">${t("（无）")}</span>`, v4: d.metadata_editable ? `<div class="add-row"><input list="tag-dl" id="tag-input" placeholder="${t("添加标签（如 色彩:红色）")}">
          <datalist id="tag-dl"></datalist><button class="btn small" id="tag-add">${t("添加")}</button></div>` : "", v5: (d.artists || []).map((a) => `
          <span class="tag-chip pill">${escapeHtml(a.name)}${d.metadata_editable ? `<button data-unartist="${a.id}" title="${t("取消关联")}">✕</button>` : ""}</span>`).join("") || `<span class="muted">${t("（无）")}</span>`, v6: d.metadata_editable ? `<div class="add-row"><input list="artist-dl" id="artist-input" placeholder="${t("关联艺术家")}">
          <datalist id="artist-dl"></datalist><button class="btn small" id="artist-add">${t("关联")}</button></div>` : "", v7: (d.albums || []).map((al) => `<span class="tag-chip pill">${escapeHtml(al.name)}</span>`).join("") || `<span class="muted">${t("（无）")}</span>`, v8: d.metadata_editable ? `<div class="add-row"><select id="album-select"><option value="">${t("加入手工专辑…")}</option></select>
          <button class="btn small" id="album-add">${t("加入")}</button></div>` : "", v9: (d.comments || []).length, v10: (d.comments || []).map((c) => `
          <div class="comment"><div class="meta">${escapeHtml(c.username)} · ${formatTimestamp(c.created_at)}
          ${(c.user_id === state.user.id || state.user.is_admin) ? `<button class="icon-btn" data-del-comment="${c.id}" title="${t("删除")}">✕</button>` : ""}</div>
          ${escapeHtml(c.body)}</div>`).join("") || `<p class="muted">${t("（暂无评论）")}</p>`, v11: can("comment") ? `<div class="add-row"><textarea id="comment-input" rows="2" placeholder="${t("写评论…")}" style="flex:1"></textarea>
          <button class="btn small" id="comment-add">${t("发表")}</button></div>` : "", v12: (d.notes || []).length, v13: (d.notes || []).map((n) => `<div class="comment"><div class="meta">${escapeHtml(n.username)}</div>${escapeHtml(n.body)}</div>`).join(""), v14: can("note") ? `<div class="add-row"><input id="note-input" placeholder="${t("写笔记…")}"><button class="btn small" id="note-add">${t("添加")}</button></div>` : "", v15: escapeHtml(d.filename), v16: d.width, v17: d.height, v18: (d.size_bytes / 1024).toFixed(1), v19: escapeHtml(d.ext), v20: escapeHtml(d.library?.name || "—"), v21: escapeHtml(d.rel_path), v22: formatTimestamp(d.imported_at), v23: escapeHtml(d.sha256 || ""), v24: (d.sha256 || "").slice(0, 12), v25: escapeHtml(d.phash || "—")});
  }

  // ---------------------------------------------------------------- 事件

  function bind(item) {
    const q = (sel) => backdrop.querySelector(sel);
    q("#v-close").addEventListener("click", close);
    q("#v-retry").onclick = () => show(current);
    q("#v-bg").onclick = () => { transparent = !transparent; render(); };
    async function write(path, options, clear = []) {
      if (saving) throw new Error(t("正在保存，请稍候。"));
      saving = true;
      setStatus(t("正在保存，请稍候。"));
      const controls = [...backdrop.querySelectorAll("button, input, select, textarea")].map(el => [el, el.disabled]);
      controls.forEach(([el]) => { el.disabled = true; });
      try {
        const result = await api(path, options);
        for (const selector of clear) backdrop.querySelectorAll(selector).forEach(el => { el.value = ""; });
        captureDrafts();
        setStatus(t("已保存"));
        return result;
      } catch (error) {
        if ([401,403,404].includes(error.status)) { captureDrafts(); detail = null; frame = null; previews.clear(); render(); }
        setStatus((error.status && error.status < 500 ? t("未保存，输入已保留。") : t("保存结果尚未确认，请先刷新详情核对，避免重复提交。")) + " " + error.message);
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
    q("#v-slideshow").addEventListener("click", () => openSlideshow(source, current));
    for (const button of backdrop.querySelectorAll("[data-work-image]")) button.addEventListener("click", () => {
      if (saving) { setStatus(t("正在保存，请稍候。")); return; }
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
        toast(r.favorite ? t("已收藏") : t("已取消收藏"), "ok");
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
    q(".viewer-panel").addEventListener("input", () => setStatus(t("输入尚未保存。")));
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
        toast(v === null ? t("已清除评分") : t("评分 → {v}", {v: v}), "ok");
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
          toast(t("已添加标签：{name}", {name: name}), "ok");
          await refresh(item.id);
        } catch (e) { toast(e.message, "error"); }
      };
      q("#tag-add").addEventListener("click", addTag);
      tagInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addTag(); } });
    }
    backdrop.querySelectorAll("[data-untag]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await write(`/images/${item.id}/tags/${encodeURIComponent(btn.dataset.untag)}`, { method: "DELETE" });
        toast(t("已移除标签"), "ok");
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
          toast(t("已关联艺术家：{name}", {name: name}), "ok");
          await refresh(item.id);
        } catch (e) { toast(e.message, "error"); }
      };
      q("#artist-add").addEventListener("click", addArtist);
      artistInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addArtist(); } });
    }
    backdrop.querySelectorAll("[data-unartist]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await write(`/images/${item.id}/artists`, { method: "POST", body: { artist_id: Number(btn.dataset.unartist), on: false } });
        toast(t("已取消关联"), "ok");
        await refresh(item.id);
      } catch (e) { toast(e.message, "error"); }
    }));

    const albumSelect = q("#album-select");
    if (albumSelect) {
      (async () => {
        try {
          const albums = await api(`/albums?library_id=${item.library_id}&kind=manual`);
          if (!albumSelect.isConnected) return;
          albumSelect.innerHTML = t("<option value=\"\">加入手工专辑…</option>") +
            albums.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
          const selected = drafts.get(item.id)?.["#album-select"];
          if (selected) albumSelect.value = selected;
        } catch { /* ignore */ }
      })();
      q("#album-add").addEventListener("click", async () => {
        const id = Number(albumSelect.value);
        if (!id) return;
        try {
          await write(`/albums/${id}/images`, { method: "POST", body: { image_ids: [item.id] } }, ["#album-select"]);
          toast(t("已加入专辑"), "ok");
          await refresh(item.id);
        } catch (e) { toast(e.message, "error"); }
      });
    }

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
    if (e.key === " " && e.target.closest("button, a")) return;
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

  async function close() {
    if (closed || closing) return;
    if (saving) { setStatus(t("正在保存，请稍候。")); return; }
    captureDrafts();
    if (drafts.size) {
      closing = true;
      const discard = await uiConfirm(t("离开查看器"), t("存在未保存输入。离开会丢弃这些输入，取消可继续编辑。"));
      closing = false;
      if (!discard) return;
    }
    closed = true;
    showVersion++; loader.cancel(); prefetch.abort(); previews.clear(); drafts.clear();
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    if (source.onChange) source.onChange();
    releaseFocus();
  }

  document.addEventListener("keydown", onKey);
  show(index);
}
