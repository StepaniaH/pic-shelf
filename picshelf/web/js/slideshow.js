// 幻灯片：设置弹窗 + 全屏播放（服务端队列驱动）。
import { api, mediaUrl } from "./api.js";
import { escapeHtml, toast, showModal } from "./app.js";

const ORDER_LABEL = { sequential: "顺序", shuffle: "乱序", random: "随机" };

function describeSource(source) {
  return source.title || "当前列表";
}

function sourceSpec(source) {
  const c = source.context || {};
  const spec = {};
  for (const k of ["album_id", "artist_id", "library_id"]) if (c[k]) spec[k] = c[k];
  if (c.tag) spec.tag = c.tag;
  if (c.favorites) spec.favorites = true;
  if (c.rating_min !== undefined && c.rating_min !== null) spec.rating_min = c.rating_min;
  return spec;
}

export function openSlideshow(source, startIndex = 0) {
  const saved = {
    interval: 5, order: "sequential", size: "fit", captions: true, loop: true,
    ...JSON.parse(localStorage.getItem("picshelf.slideshow") || "{}"),
  };
  showModal({
    title: "幻灯片设置",
    bodyHtml: `
      <div class="form-row"><label class="muted">来源</label><span>${escapeHtml(describeSource(source))}</span></div>
      <div class="form-row">
        <label>间隔 <select id="ss-interval">${[2, 3, 5, 8, 10, 15, 30, 60]
          .map((v) => `<option value="${v}" ${Number(saved.interval) === v ? "selected" : ""}>${v}</option>`).join("")}
        </select> 秒</label>
        <label>顺序 <select id="ss-order">${Object.entries(ORDER_LABEL)
          .map(([v, l]) => `<option value="${v}" ${saved.order === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      </div>
      <div class="form-row">
        <label>数量 <input id="ss-count" type="number" min="0" value="0" style="width:90px"> <span class="muted">0 = 全部</span></label>
      </div>
      <div class="form-row checkbox-row">
        <label><input type="checkbox" id="ss-loop" ${saved.loop ? "checked" : ""}> 循环（轮播）</label>
        <label><input type="checkbox" id="ss-captions" ${saved.captions ? "checked" : ""}> 显示字幕（文件名/标签）</label>
      </div>
      <div class="form-row">
        <label>尺寸 <select id="ss-size">${[["fit", "适应"], ["fill", "填满"], ["original", "原图"]]
          .map(([v, l]) => `<option value="${v}" ${saved.size === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      </div>
      <p class="hint">播放中：空格 暂停/继续 · ←/→ 上一张/下一张 · Esc 退出</p>
      <div class="modal-actions">
        <button class="btn primary" id="ss-start">开始播放</button>
        <button class="btn" data-close>取消</button>
      </div>`,
    onMount(el, close) {
      el.querySelector("#ss-start").addEventListener("click", () => {
        const opts = {
          interval: Number(el.querySelector("#ss-interval").value),
          order: el.querySelector("#ss-order").value,
          count: Number(el.querySelector("#ss-count").value),
          loop: el.querySelector("#ss-loop").checked,
          captions: el.querySelector("#ss-captions").checked,
          size: el.querySelector("#ss-size").value,
        };
        localStorage.setItem("picshelf.slideshow", JSON.stringify(opts));
        close();
        startSlideshow(source, opts).catch((e) => toast(e.message || "播放失败", "error"));
      });
    },
  });
}

async function startSlideshow(source, opts) {
  const session = await api("/play/sessions", {
    method: "POST",
    body: {
      source: sourceSpec(source),
      query: source.q || "",
      order: opts.order,
      interval: opts.interval,
      limit: opts.count || 0,
      loop: opts.loop,
    },
  });
  if (!session.total) { toast("来源为空，没有可播放的图片", "error"); return; }

  const backdrop = document.createElement("div");
  backdrop.className = "slideshow-backdrop";
  backdrop.innerHTML = `
    <div class="slideshow-stage size-${opts.size}">
      <img id="ss-img" alt="">
      <div class="slideshow-controls">
        <button class="btn small" id="ss-prev">‹ 上一张</button>
        <button class="btn small" id="ss-pause">⏸ 暂停</button>
        <button class="btn small" id="ss-next">下一张 ›</button>
        <button class="btn small" id="ss-settings">设置</button>
        <button class="btn small" id="ss-exit">退出 (Esc)</button>
      </div>
    </div>
    ${opts.captions ? `<div class="slideshow-caption" id="ss-caption" aria-live="polite"></div>` : ""}`;
  document.body.appendChild(backdrop);

  let closed = false;
  let paused = false;
  let timer = null;
  let position = 0;
  let lastImage = null;
  const imgEl = backdrop.querySelector("#ss-img");

  function renderCaption() {
    const cap = backdrop.querySelector("#ss-caption");
    if (!cap || !lastImage) return;
    const tags = (lastImage.tags || []).map((t) => t.name).slice(0, 8).join("、");
    cap.innerHTML = `<b>${position} / ${session.total}</b>
      <span class="name">${escapeHtml(lastImage.filename)}</span>
      <span class="tags">${escapeHtml(tags)}</span>`;
  }

  function schedule() {
    clearTimeout(timer);
    if (closed || paused) return;
    timer = setTimeout(() => step(1).catch(() => {}), opts.interval * 1000);
  }

  async function step(dir = 1) {
    if (closed) return;
    const ep = dir > 0 ? "next" : "prev";
    const r = await api(`/play/sessions/${session.token}/${ep}`, { method: "POST", body: {} });
    if (closed) return;
    if (r.image) {
      lastImage = r.image;
      position = r.position;
      const preload = new Image();
      preload.src = mediaUrl("preview", r.image.id);
      imgEl.src = mediaUrl("preview", r.image.id);
      imgEl.alt = r.image.filename;
      imgEl.classList.remove("fade-in");
      void imgEl.offsetWidth;
      imgEl.classList.add("fade-in");
      renderCaption();
    }
    if (r.done && dir > 0) {
      stopTimer();
      toast("播放完成", "ok");
      return;
    }
    schedule();
  }

  function stopTimer() { clearTimeout(timer); timer = null; }

  function setPaused(p) {
    paused = p;
    backdrop.querySelector("#ss-pause").textContent = paused ? "▶ 继续" : "⏸ 暂停";
    if (paused) stopTimer(); else schedule();
  }

  async function close() {
    if (closed) return;
    closed = true;
    stopTimer();
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    try { await api(`/play/sessions/${session.token}`, { method: "DELETE" }); } catch { /* ignore */ }
  }

  function onKey(e) {
    switch (e.key) {
      case "Escape": close(); break;
      case " ": e.preventDefault(); setPaused(!paused); break;
      case "ArrowRight": step(1).catch(() => {}); break;
      case "ArrowLeft": step(-1).catch(() => {}); break;
      default: break;
    }
  }

  backdrop.querySelector("#ss-next").addEventListener("click", () => step(1).catch(() => {}));
  backdrop.querySelector("#ss-prev").addEventListener("click", () => step(-1).catch(() => {}));
  backdrop.querySelector("#ss-pause").addEventListener("click", () => setPaused(!paused));
  backdrop.querySelector("#ss-exit").addEventListener("click", close);
  backdrop.querySelector("#ss-settings").addEventListener("click", async () => {
    await close();
    openSlideshow(source);
  });
  document.addEventListener("keydown", onKey);

  await step(1); // 取第一张（cursor -1 → 0）
}
