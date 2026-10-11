// 查看器：大图 + 信息面板（评分/标签/艺术家/专辑/评论/笔记/元数据）。
import { api, mediaUrl } from "./api.js";
import { escapeHtml, toast, can, state } from "./app.js";
import { openSlideshow } from "./slideshow.js";

const SIZE_LABEL = { fit: "适应", fill: "填满", original: "原图" };
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
      if (p && !detail) p.innerHTML = `<p class="muted">信息加载失败：${escapeHtml(e.message || e)}</p>`;
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
    const loaded = source.loadedAll ? `共 ${source.total}` : `已加载 ${source.items.length} / 共 ${source.total}`;
    backdrop.innerHTML = `
      <div class="viewer-top">
        <span class="title">${escapeHtml(source.title)}</span>
        <span class="muted meta-count">${current + 1} / ${source.items.length}（${loaded}）</span>
        <div class="viewer-actions">
          <button class="btn small" id="v-slideshow">幻灯片</button>
          <button class="btn small" id="v-zoom" title="切换显示尺寸 (z)">尺寸：${SIZE_LABEL[zoom]}</button>
          ${can("rate") ? `<button class="btn small" id="v-fav" title="收藏 (f)">${item.favorite ? "★ 已收藏" : "☆ 收藏"}</button>` : ""}
          <button class="btn small" id="v-panel" title="信息面板 (i)">信息</button>
          <a class="btn small" href="${mediaUrl("original", item.id)}?download=1">下载原图</a>
          <button class="btn small" id="v-close" title="关闭 (Esc)">关闭 ✕</button>
        </div>
      </div>
      <div class="viewer-body">
        <div class="viewer-stage size-${zoom}">
          <img src="${mediaUrl("preview", item.id)}" alt="${escapeHtml(item.filename)}">
          <button class="nav-btn prev" title="上一张 (←)">‹</button>
          <button class="nav-btn next" title="下一张 (→)">›</button>
        </div>
        <aside class="viewer-panel" ${panelOpen ? "" : "hidden"}>${panelHtml(item)}</aside>
      </div>`;
    bind(item);
  }

  function panelHtml(item) {
    if (!detail) return `<p class="muted">加载中…</p>`;
    const d = detail;
    return `
      <section>
        <h3>评分 <span class="muted">（平均 ${d.avg_rating ?? "—"} · ${d.rating_count} 人）</span></h3>
        ${can("rate") ? `<div class="rate-row">${Array.from({ length: 10 }, (_, i) => i + 1).map((v) =>
          `<button class="rate-btn ${d.my_rating === v ? "on" : ""}" data-rate="${v}" title="${v} 分">${v}</button>`).join("")}
          <button class="rate-btn" data-rate="0" style="width:auto;padding:0 10px" title="清除评分">清除</button></div>`
        : `<p class="muted">我的评分：${d.my_rating >= 0 ? d.my_rating : "未评"}</p>`}
      </section>
      <section>
        <h3>标签</h3>
        <div>${(d.tags || []).map((t) => `
          <span class="tag-chip pill">${escapeHtml(t.name)}${can("tag_edit") ? `<button data-untag="${escapeHtml(t.name)}" title="移除">✕</button>` : ""}</span>`).join("") || `<span class="muted">（无）</span>`}
        </div>
        ${can("tag_edit") ? `<div class="add-row"><input list="tag-dl" id="tag-input" placeholder="添加标签（如 色彩:红色）">
          <datalist id="tag-dl"></datalist><button class="btn small" id="tag-add">添加</button></div>` : ""}
      </section>
      <section>
        <h3>艺术家</h3>
        <div>${(d.artists || []).map((a) => `
          <span class="tag-chip pill">${escapeHtml(a.name)}${can("tag_edit") ? `<button data-unartist="${a.id}" title="取消关联">✕</button>` : ""}</span>`).join("") || `<span class="muted">（无）</span>`}
        </div>
        ${can("tag_edit") ? `<div class="add-row"><input list="artist-dl" id="artist-input" placeholder="关联艺术家">
          <datalist id="artist-dl"></datalist><button class="btn small" id="artist-add">关联</button></div>` : ""}
      </section>
      <section>
        <h3>专辑</h3>
        <div>${(d.albums || []).map((al) => `<span class="tag-chip pill">${escapeHtml(al.name)}</span>`).join("") || `<span class="muted">（无）</span>`}</div>
        ${can("tag_edit") ? `<div class="add-row"><select id="album-select"><option value="">加入手工专辑…</option></select>
          <button class="btn small" id="album-add">加入</button></div>` : ""}
      </section>
      <section>
        <h3>评论（${(d.comments || []).length}）</h3>
        <div id="comment-list">${(d.comments || []).map((c) => `
          <div class="comment"><div class="meta">${escapeHtml(c.username)} · ${c.created_at.replace("T", " ").slice(0, 16)}
          ${(c.user_id === state.user.id || state.user.is_admin) ? `<button class="icon-btn" data-del-comment="${c.id}" title="删除">✕</button>` : ""}</div>
          ${escapeHtml(c.body)}</div>`).join("") || `<p class="muted">（暂无评论）</p>`}</div>
        ${can("comment") ? `<div class="add-row"><textarea id="comment-input" rows="2" placeholder="写评论…" style="flex:1"></textarea>
          <button class="btn small" id="comment-add">发表</button></div>` : ""}
      </section>
      <section>
        <h3>笔记（${(d.notes || []).length}）</h3>
        ${(d.notes || []).map((n) => `<div class="comment"><div class="meta">${escapeHtml(n.username)}</div>${escapeHtml(n.body)}</div>`).join("")}
        ${can("note") ? `<div class="add-row"><input id="note-input" placeholder="写笔记…"><button class="btn small" id="note-add">添加</button></div>` : ""}
      </section>
      <section>
        <h3>信息</h3>
        <div class="kv">
          <div>文件名：<b>${escapeHtml(d.filename)}</b></div>
          <div>尺寸：<b>${d.width}×${d.height}</b> · 大小：<b>${(d.size_bytes / 1024).toFixed(1)} KB</b> · 格式：<b>${escapeHtml(d.ext)}</b></div>
          <div>库：<b>${escapeHtml(d.library?.name || "—")}</b></div>
          <div>路径：<b>${escapeHtml(d.rel_path)}</b></div>
          <div>导入时间：<b>${(d.imported_at || "").replace("T", " ").slice(0, 16)}</b></div>
          <div>指纹：<b title="${escapeHtml(d.sha256 || "")}">${(d.sha256 || "").slice(0, 12)}…</b> / pHash <b>${escapeHtml(d.phash || "—")}</b></div>
        </div>
      </section>`;
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
      toast(r.favorite ? "已收藏" : "已取消收藏", "ok");
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
        toast(v === 0 ? "已清除评分" : `评分 → ${v}`, "ok");
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
          toast(`已添加标签：${name}`, "ok");
          await refresh();
        } catch (e) { toast(e.message, "error"); }
      };
      q("#tag-add").addEventListener("click", addTag);
      tagInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } });
    }
    backdrop.querySelectorAll("[data-untag]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/images/${item.id}/tags/${encodeURIComponent(btn.dataset.untag)}`, { method: "DELETE" });
        toast("已移除标签", "ok");
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
          toast(`已关联艺术家：${name}`, "ok");
          await refresh();
        } catch (e) { toast(e.message, "error"); }
      };
      q("#artist-add").addEventListener("click", addArtist);
      artistInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addArtist(); } });
    }
    backdrop.querySelectorAll("[data-unartist]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/images/${item.id}/artists`, { method: "POST", body: { artist_id: Number(btn.dataset.unartist), on: false } });
        toast("已取消关联", "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    }));

    const albumSelect = q("#album-select");
    if (albumSelect) {
      (async () => {
        try {
          const albums = await api(`/albums?library_id=${item.library_id}&kind=manual`);
          albumSelect.innerHTML = `<option value="">加入手工专辑…</option>` +
            albums.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
        } catch { /* ignore */ }
      })();
      q("#album-add").addEventListener("click", async () => {
        const id = Number(albumSelect.value);
        if (!id) return;
        try {
          await api(`/albums/${id}/images`, { method: "POST", body: { image_ids: [item.id] } });
          toast("已加入专辑", "ok");
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
        toast("评论已发表", "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    });
    backdrop.querySelectorAll("[data-del-comment]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/comments/${btn.dataset.delComment}`, { method: "DELETE" });
        toast("评论已删除", "ok");
        await refresh();
      } catch (e) { toast(e.message, "error"); }
    }));

    const noteAdd = q("#note-add");
    if (noteAdd) noteAdd.addEventListener("click", async () => {
      const body = q("#note-input").value.trim();
      if (!body) return;
      try {
        await api(`/images/${item.id}/notes`, { method: "POST", body: { body } });
        toast("笔记已添加", "ok");
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
