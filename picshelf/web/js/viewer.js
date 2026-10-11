// 查看器：大图 + 信息面板（评分/标签/艺术家/专辑/评论/笔记/元数据）。
import { api, mediaUrl } from "./api.js";
import { escapeHtml, toast, can, state } from "./app.js";
import { openSlideshow } from "./slideshow.js";
import { t } from "./i18n.js";

const SIZE_LABEL = { fit: t("适应"), fill: t("填满"), original: t("原图") };
const SIZE_CYCLE = ["fit", "fill", "original"];

export function openViewer(source, index) {
  let current = index;
  let zoom = localStorage.getItem("picshelf.zoom") || "fit";
  if (!SIZE_LABEL[zoom]) zoom = "fit";
  let panelOpen = localStorage.getItem("picshelf.panel") !== "0";
  let detail = null;
  let closed = false;

  const backdrop = document.createElement("div");
  backdrop.className = "viewer-backdrop";
  document.body.appendChild(backdrop);

  const savePrefs = () => {
    localStorage.setItem("picshelf.zoom", zoom);
    localStorage.setItem("picshelf.panel", panelOpen ? "1" : "0");
  };

  async function show(i) {
    if (closed) return;
    if (i < 0) { if (!source.loadedAll) await source.loadMore(); i = Math.max(0, Math.min(i, source.items.length - 1)); }
    if (i >= source.items.length) {
      if (!source.loadedAll) await source.loadMore();
      if (i >= source.items.length) i = source.items.length - 1;
    }
    current = Math.max(0, Math.min(i, source.items.length - 1));
    detail = null;
    render();
    const item = source.items[current];
    try {
      detail = await api(`/images/${item.id}`);
      if (!closed) render();
    } catch (e) {
      const p = backdrop.querySelector(".viewer-panel");
      if (p && !detail) p.innerHTML = t("<p class=\"muted\">信息加载失败：{v0}</p>", {v0: escapeHtml(e.message || e)});
    }
    // 预取相邻两张
    for (const n of [current + 1, current - 1]) {
      const it = source.items[n];
      if (it) { const img = new Image(); img.src = mediaUrl("preview", it.id); }
    }
  }

  async function refresh() {
    const item = source.items[current];
    try {
      detail = await api(`/images/${item.id}`);
      render();
    } catch { /* ignore */ }
    if (source.onChange) source.onChange();
  }

  // ---------------------------------------------------------------- 渲染

  function render() {
    const item = source.items[current];
    const loaded = source.loadedAll ? t("共 {v0}", {v0: source.total}) : t("已加载 {v0} / 共 {v1}", {v0: source.items.length, v1: source.total});
    backdrop.innerHTML = t("\n      <div class=\"viewer-top\">\n        <span class=\"title\">{v0}</span>\n        <span class=\"muted meta-count\">{v1} / {v2}（{loaded}）</span>\n        <div class=\"viewer-actions\">\n          <button class=\"btn small\" id=\"v-slideshow\">幻灯片</button>\n          <button class=\"btn small\" id=\"v-zoom\" title=\"切换显示尺寸 (z)\">尺寸：{v3}</button>\n          {v4}\n          <button class=\"btn small\" id=\"v-panel\" title=\"信息面板 (i)\">信息</button>\n          <a class=\"btn small\" href=\"{v5}?download=1\">下载原图</a>\n          <button class=\"btn small\" id=\"v-close\" title=\"关闭 (Esc)\">关闭 ✕</button>\n        </div>\n      </div>\n      <div class=\"viewer-body\">\n        <div class=\"viewer-stage size-{zoom}\">\n          <img src=\"{v6}\" alt=\"{v7}\">\n          <button class=\"nav-btn prev\" title=\"上一张 (←)\">‹</button>\n          <button class=\"nav-btn next\" title=\"下一张 (→)\">›</button>\n        </div>\n        <aside class=\"viewer-panel\" {v8}>{v9}</aside>\n      </div>", {v0: escapeHtml(source.title), v1: current + 1, v2: source.items.length, loaded: loaded, v3: SIZE_LABEL[zoom], v4: can("rate") ? `<button class="btn small" id="v-fav" title="收藏 (f)">${item.favorite ? "★ 已收藏" : "☆ 收藏"}</button>` : "", v5: mediaUrl("original", item.id), zoom: zoom, v6: mediaUrl("preview", item.id), v7: escapeHtml(item.filename), v8: panelOpen ? "" : "hidden", v9: panelHtml(item)});
    bind(item);
  }

  function panelHtml(item) {
    if (!detail) return t("<p class=\"muted\">加载中…</p>");
    const d = detail;
    return t("\n      <section>\n        <h3>评分 <span class=\"muted\">（平均 {v0} · {v1} 人）</span></h3>\n        {v2}\n      </section>\n      <section>\n        <h3>标签</h3>\n        <div>{v3}\n        </div>\n        {v4}\n      </section>\n      <section>\n        <h3>艺术家</h3>\n        <div>{v5}\n        </div>\n        {v6}\n      </section>\n      <section>\n        <h3>专辑</h3>\n        <div>{v7}</div>\n        {v8}\n      </section>\n      <section>\n        <h3>评论（{v9}）</h3>\n        <div id=\"comment-list\">{v10}</div>\n        {v11}\n      </section>\n      <section>\n        <h3>笔记（{v12}）</h3>\n        {v13}\n        {v14}\n      </section>\n      <section>\n        <h3>信息</h3>\n        <div class=\"kv\">\n          <div>文件名：<b>{v15}</b></div>\n          <div>尺寸：<b>{v16}×{v17}</b> · 大小：<b>{v18} KB</b> · 格式：<b>{v19}</b></div>\n          <div>库：<b>{v20}</b></div>\n          <div>路径：<b>{v21}</b></div>\n          <div>导入时间：<b>{v22}</b></div>\n          <div>指纹：<b title=\"{v23}\">{v24}…</b> / pHash <b>{v25}</b></div>\n        </div>\n      </section>", {v0: d.avg_rating ?? "—", v1: d.rating_count, v2: can("rate") ? `<div class="rate-row">${Array.from({ length: 10 }, (_, i) => i + 1).map((v) =>
          `<button class="rate-btn ${d.my_rating === v ? "on" : ""}" data-rate="${v}" title="${v} 分">${v}</button>`).join("")}
          <button class="rate-btn" data-rate="0" style="width:auto;padding:0 10px" title="清除评分">清除</button></div>`
        : `<p class="muted">我的评分：${d.my_rating >= 0 ? d.my_rating : "未评"}</p>`, v3: (d.tags || []).map((t) => `
          <span class="tag-chip pill">${escapeHtml(t.name)}${can("tag_edit") ? `<button data-untag="${escapeHtml(t.name)}" title="移除">✕</button>` : ""}</span>`).join("") || `<span class="muted">（无）</span>`, v4: can("tag_edit") ? `<div class="add-row"><input list="tag-dl" id="tag-input" placeholder="添加标签（如 色彩:红色）">
          <datalist id="tag-dl"></datalist><button class="btn small" id="tag-add">添加</button></div>` : "", v5: (d.artists || []).map((a) => `
          <span class="tag-chip pill">${escapeHtml(a.name)}${can("tag_edit") ? `<button data-unartist="${a.id}" title="取消关联">✕</button>` : ""}</span>`).join("") || `<span class="muted">（无）</span>`, v6: can("tag_edit") ? `<div class="add-row"><input list="artist-dl" id="artist-input" placeholder="关联艺术家">
          <datalist id="artist-dl"></datalist><button class="btn small" id="artist-add">关联</button></div>` : "", v7: (d.albums || []).map((al) => `<span class="tag-chip pill">${escapeHtml(al.name)}</span>`).join("") || `<span class="muted">（无）</span>`, v8: can("tag_edit") ? `<div class="add-row"><select id="album-select"><option value="">加入手工专辑…</option></select>
          <button class="btn small" id="album-add">加入</button></div>` : "", v9: (d.comments || []).length, v10: (d.comments || []).map((c) => `
          <div class="comment"><div class="meta">${escapeHtml(c.username)} · ${c.created_at.replace("T", " ").slice(0, 16)}
          ${(c.user_id === state.user.id || state.user.is_admin) ? `<button class="icon-btn" data-del-comment="${c.id}" title="删除">✕</button>` : ""}</div>
          ${escapeHtml(c.body)}</div>`).join("") || `<p class="muted">（暂无评论）</p>`, v11: can("comment") ? `<div class="add-row"><textarea id="comment-input" rows="2" placeholder="写评论…" style="flex:1"></textarea>
          <button class="btn small" id="comment-add">发表</button></div>` : "", v12: (d.notes || []).length, v13: (d.notes || []).map((n) => `<div class="comment"><div class="meta">${escapeHtml(n.username)}</div>${escapeHtml(n.body)}</div>`).join(""), v14: can("note") ? `<div class="add-row"><input id="note-input" placeholder="写笔记…"><button class="btn small" id="note-add">添加</button></div>` : "", v15: escapeHtml(d.filename), v16: d.width, v17: d.height, v18: (d.size_bytes / 1024).toFixed(1), v19: escapeHtml(d.ext), v20: escapeHtml(d.library?.name || "—"), v21: escapeHtml(d.rel_path), v22: (d.imported_at || "").replace("T", " ").slice(0, 16), v23: escapeHtml(d.sha256 || ""), v24: (d.sha256 || "").slice(0, 12), v25: escapeHtml(d.phash || "—")});
  }

  // ---------------------------------------------------------------- 事件

  function bind(item) {
    const q = (sel) => backdrop.querySelector(sel);
    q("#v-close").addEventListener("click", close);
    q(".nav-btn.prev").addEventListener("click", () => show(current - 1));
    q(".nav-btn.next").addEventListener("click", () => show(current + 1));
    q("#v-slideshow").addEventListener("click", () => openSlideshow(source, current));
    q("#v-zoom").addEventListener("click", () => {
      zoom = SIZE_CYCLE[(SIZE_CYCLE.indexOf(zoom) + 1) % SIZE_CYCLE.length];
      savePrefs();
      render();
    });
    const favBtn = q("#v-fav");
    if (favBtn) favBtn.addEventListener("click", async () => {
      const r = await api(`/images/${item.id}/favorite`, { method: "POST", body: {} });
      item.favorite = r.favorite ? 1 : 0;
      if (detail) detail.favorite = item.favorite;
      toast(r.favorite ? t("已收藏") : t("已取消收藏"), "ok");
      render();
      if (source.onChange) source.onChange();
    });
    q("#v-panel").addEventListener("click", () => {
      panelOpen = !panelOpen;
      savePrefs();
      q(".viewer-panel").hidden = !panelOpen;
    });

    if (!detail) return;
    backdrop.querySelectorAll("[data-rate]").forEach((btn) => btn.addEventListener("click", async () => {
      const v = Number(btn.dataset.rate);
      try {
        const r = await api(`/images/${item.id}/rating`, { method: "POST", body: { value: v === 0 ? null : v } });
        detail.my_rating = r.my_rating;
        detail.avg_rating = r.avg_rating;
        detail.rating_count = r.rating_count;
        item.my_rating = r.my_rating;
        toast(v === 0 ? t("已清除评分") : t("评分 → {v}", {v: v}), "ok");
        render();
        if (source.onChange) source.onChange();
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
            dl.innerHTML = tags.map((t) => `<option value="${escapeHtml(t.name)}">`).join("");
          } catch { /* ignore */ }
        }, 200);
      });
      const addTag = async () => {
        const name = tagInput.value.trim();
        if (!name) return;
        try {
          await api(`/images/${item.id}/tags`, { method: "POST", body: { names: [name] } });
          toast(t("已添加标签：{name}", {name: name}), "ok");
          await refresh();
        } catch (e) { toast(e.message, "error"); }
      };
      q("#tag-add").addEventListener("click", addTag);
      tagInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } });
    }
    backdrop.querySelectorAll("[data-untag]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/images/${item.id}/tags/${encodeURIComponent(btn.dataset.untag)}`, { method: "DELETE" });
        toast(t("已移除标签"), "ok");
        await refresh();
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
          let artistId = found[0] && found[0].name === name ? found[0].id : null;
          if (!artistId) {
            const created = await api("/artists", { method: "POST", body: { name } });
            artistId = created.id;
          }
          await api(`/images/${item.id}/artists`, { method: "POST", body: { artist_id: artistId, on: true } });
          toast(t("已关联艺术家：{name}", {name: name}), "ok");
          await refresh();
        } catch (e) { toast(e.message, "error"); }
      };
      q("#artist-add").addEventListener("click", addArtist);
      artistInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addArtist(); } });
    }
    backdrop.querySelectorAll("[data-unartist]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/images/${item.id}/artists`, { method: "POST", body: { artist_id: Number(btn.dataset.unartist), on: false } });
        toast(t("已取消关联"), "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    }));

    const albumSelect = q("#album-select");
    if (albumSelect) {
      (async () => {
        try {
          const albums = await api(`/albums?library_id=${item.library_id}&kind=manual`);
          albumSelect.innerHTML = t("<option value=\"\">加入手工专辑…</option>") +
            albums.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
        } catch { /* ignore */ }
      })();
      q("#album-add").addEventListener("click", async () => {
        const id = Number(albumSelect.value);
        if (!id) return;
        try {
          await api(`/albums/${id}/images`, { method: "POST", body: { image_ids: [item.id] } });
          toast(t("已加入专辑"), "ok");
          await refresh();
        } catch (e) { toast(e.message, "error"); }
      });
    }

    const commentAdd = q("#comment-add");
    if (commentAdd) commentAdd.addEventListener("click", async () => {
      const body = q("#comment-input").value.trim();
      if (!body) return;
      try {
        await api(`/images/${item.id}/comments`, { method: "POST", body: { body } });
        toast(t("评论已发表"), "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    });
    backdrop.querySelectorAll("[data-del-comment]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/comments/${btn.dataset.delComment}`, { method: "DELETE" });
        toast(t("评论已删除"), "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    }));

    const noteAdd = q("#note-add");
    if (noteAdd) noteAdd.addEventListener("click", async () => {
      const body = q("#note-input").value.trim();
      if (!body) return;
      try {
        await api(`/images/${item.id}/notes`, { method: "POST", body: { body } });
        toast(t("笔记已添加"), "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    });
  }

  function onKey(e) {
    if (e.target && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
    switch (e.key) {
      case "ArrowRight": case " ": e.preventDefault(); show(current + 1); break;
      case "ArrowLeft": show(current - 1); break;
      case "Escape": close(); break;
      case "f": backdrop.querySelector("#v-fav")?.click(); break;
      case "i": q_panel_toggle(); break;
      case "z": backdrop.querySelector("#v-zoom")?.click(); break;
      default: break;
    }
  }
  function q_panel_toggle() { backdrop.querySelector("#v-panel")?.click(); }

  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    if (source.onChange) source.onChange();
  }

  document.addEventListener("keydown", onKey);
  show(index);
}
