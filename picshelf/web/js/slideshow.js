// 幻灯片：设置弹窗 + 全屏播放（服务端队列驱动）。
import { api, mediaUrl } from "./api.js";
import { escapeHtml, toast, showModal } from "./app.js";
import { t } from "./i18n.js";

const ORDER_LABEL = { sequential: t("顺序"), shuffle: t("乱序"), random: t("随机") };

function describeSource(source) {
  return source.title || t("当前列表");
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
    title: t("幻灯片设置"),
    bodyHtml: t("\n      <div class=\"form-row\"><label class=\"muted\">来源</label><span>{v0}</span></div>\n      <div class=\"form-row\">\n        <label>间隔 <select id=\"ss-interval\">{v1}\n        </select> 秒</label>\n        <label>顺序 <select id=\"ss-order\">{v2}\n        </select></label>\n      </div>\n      <div class=\"form-row\">\n        <label>数量 <input id=\"ss-count\" type=\"number\" min=\"0\" value=\"0\" style=\"width:90px\"> <span class=\"muted\">0 = 全部</span></label>\n      </div>\n      <div class=\"form-row checkbox-row\">\n        <label><input type=\"checkbox\" id=\"ss-loop\" {v3}> 循环（轮播）</label>\n        <label><input type=\"checkbox\" id=\"ss-captions\" {v4}> 显示字幕（文件名/标签）</label>\n      </div>\n      <div class=\"form-row\">\n        <label>尺寸 <select id=\"ss-size\">{v5}\n        </select></label>\n      </div>\n      <p class=\"hint\">播放中：空格 暂停/继续 · ←/→ 上一张/下一张 · Esc 退出</p>\n      <div class=\"modal-actions\">\n        <button class=\"btn primary\" id=\"ss-start\">开始播放</button>\n        <button class=\"btn\" data-close>取消</button>\n      </div>", {v0: escapeHtml(describeSource(source)), v1: [2, 3, 5, 8, 10, 15, 30, 60]
          .map((v) => `<option value="${v}" ${Number(saved.interval) === v ? "selected" : ""}>${v}</option>`).join(""), v2: Object.entries(ORDER_LABEL)
          .map(([v, l]) => `<option value="${v}" ${saved.order === v ? "selected" : ""}>${l}</option>`).join(""), v3: saved.loop ? "checked" : "", v4: saved.captions ? "checked" : "", v5: [["fit", "适应"], ["fill", "填满"], ["original", "原图"]]
          .map(([v, l]) => `<option value="${v}" ${saved.size === v ? "selected" : ""}>${l}</option>`).join("")}),
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
        startSlideshow(source, opts).catch((e) => toast(e.message || t("播放失败"), "error"));
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
  if (!session.total) { toast(t("来源为空，没有可播放的图片"), "error"); return; }

  const backdrop = document.createElement("div");
  backdrop.className = "slideshow-backdrop";
  backdrop.innerHTML = t("\n    <div class=\"slideshow-stage size-{v0}\">\n      <img id=\"ss-img\" alt=\"\">\n      <div class=\"slideshow-controls\">\n        <button class=\"btn small\" id=\"ss-prev\">‹ 上一张</button>\n        <button class=\"btn small\" id=\"ss-pause\">⏸ 暂停</button>\n        <button class=\"btn small\" id=\"ss-next\">下一张 ›</button>\n        <button class=\"btn small\" id=\"ss-settings\">设置</button>\n        <button class=\"btn small\" id=\"ss-exit\">退出 (Esc)</button>\n      </div>\n    </div>\n    {v1}", {v0: opts.size, v1: opts.captions ? `<div class="slideshow-caption" id="ss-caption" aria-live="polite"></div>` : ""});
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
      toast(t("播放完成"), "ok");
      return;
    }
    schedule();
  }

  function stopTimer() { clearTimeout(timer); timer = null; }

  function setPaused(p) {
    paused = p;
    backdrop.querySelector("#ss-pause").textContent = paused ? t("▶ 继续") : t("⏸ 暂停");
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
