import {t} from "./i18n.js";

// Keep nearby rows mounted and retain a small set of recently viewed thumbnails.
export class ImageGrid {
  constructor(root, renderCard, onRender) {
    Object.assign(this, {root, renderCard, onRender});
    this.items = [];
    this.indices = new Map();
    this.cards = new Map();
    root.tabIndex = -1;
    this.dirty = true;
    this.schedule = () => {
      if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = null; this.render(); });
    };
    this.resize = new ResizeObserver(this.schedule);
    this.resize.observe(root);
    this.resize.observe(root.parentElement);
    this.dialogs = new MutationObserver(this.schedule);
    this.dialogs.observe(document.body, {attributes:true, attributeFilter:["class"]});
    window.addEventListener("scroll", this.schedule, {passive:true});
    window.addEventListener("resize", this.schedule);
    this.onKey = event => this.keydown(event);
    root.addEventListener("keydown", this.onKey);
    this.onImage = event => {
      const image = event.target;
      if (!image.matches?.(".card > img")) return;
      const card = image.closest(".card");
      if (event.type === "load") { card.querySelector("[data-thumb-retry]")?.remove(); return; }
      if (card.querySelector("[data-thumb-retry]")) return;
      const retry = document.createElement("button");
      retry.type = "button"; retry.className = "thumb-retry btn small"; retry.dataset.thumbRetry = "";
      retry.textContent = t("重载缩略图");
      retry.onclick = event => {
        event.stopPropagation();
        if (document.activeElement === retry) card.focus({preventScroll:true});
        retry.remove();
        const url = image.src; image.removeAttribute("src"); image.src = url;
      };
      card.append(retry);
    };
    root.addEventListener("error", this.onImage, true);
    root.addEventListener("load", this.onImage, true);
  }

  update(items, total = items.length) {
    this.items = items;
    this.total = total;
    this.indices = new Map(items.map((item, index) => [item.id, index]));
    this.dirty = true;
    this.render();
  }

  render(focusIndex = null) {
    const root = this.root;
    if (!root.isConnected || document.body.classList.contains("dialog-open")) return;
    const style = getComputedStyle(root);
    const widths = style.gridTemplateColumns.split(" ").map(Number.parseFloat);
    const columns = widths.length;
    if (!widths[0]) return;
    const gap = Number.parseFloat(style.rowGap) || 0;
    // Matches the 4:3 thumbnail, 2px border and fixed caption in app.css.
    const height = (widths[0] - 2) * .75 + Number.parseFloat(getComputedStyle(document.documentElement).fontSize) * 2.75 + 2;
    const stride = height + gap;
    const rows = Math.ceil(this.items.length / columns);
    const windowed = this.items.length > 240;
    const focused = document.activeElement?.closest(".card");
    const pinned = root.contains(focused) ? this.indices.get(Number(focused.dataset.id)) : undefined;
    let restoreFocus = this.dirty && root.contains(focused)
      ? {id:Number(focused.dataset.id), checkbox:document.activeElement.matches("input")} : null;
    if (root.contains(focused) && pinned === undefined) {
      focusIndex = Math.max(0, Math.min(this.items.length - 1, Number(focused.getAttribute("aria-posinset")) - 1));
      restoreFocus = {id:this.items[focusIndex]?.id};
    }
    const layoutChanged = this.geometry && (this.geometry.columns !== columns || this.geometry.stride !== stride);
    const keepFocus = focusIndex === null && layoutChanged && pinned >= this.start && pinned < this.end;
    let top = Math.max(0, -root.getBoundingClientRect().top), scrollDelta = 0;
    if (windowed && focusIndex === null && top > 0 && layoutChanged) {
      const anchor = Math.floor(top / this.geometry.stride) * this.geometry.columns;
      const adjusted = Math.floor(anchor / columns) * stride + top % this.geometry.stride / this.geometry.stride * stride;
      scrollDelta = adjusted - top; top = adjusted;
    }
    this.geometry = {columns, stride};
    const scrollTarget = scrollY + scrollDelta;
    const overscan = Math.max(4, Math.ceil(innerHeight / stride));
    const center = focusIndex === null ? Math.floor(top / stride) : Math.floor(focusIndex / columns);
    const start = windowed ? Math.max(0, Math.min(rows - 1, center) - overscan) * columns : 0;
    const end = windowed ? Math.min(this.items.length, (center + Math.ceil(innerHeight / stride) + overscan + 1) * columns) : this.items.length;
    const outside = pinned !== undefined && (pinned < start || pinned >= end);
    const key = [start, end, columns, height, outside ? pinned : -1].join(":");
    if (!this.dirty && this.key === key) return;
    this.key = key; this.dirty = false;
    this.start = start; this.end = end; this.columns = columns;
    root.classList.toggle("windowed", windowed);
    root.style.paddingTop = windowed ? `${start / columns * stride}px` : "";
    root.style.paddingBottom = windowed ? `${Math.max(0, rows - Math.ceil(end / columns)) * stride}px` : "";
    root.style.gridAutoRows = windowed ? `${height}px` : "";
    const visible = this.items.slice(start, end).map((item, offset) => [item, start + offset]);
    if (outside) visible.push([this.items[pinned], pinned]);
    visible.sort((a, b) => a[1] - b[1]);
    const keep = new Set(visible.map(([item]) => item.id));
    for (const card of [...root.children]) if (!keep.has(Number(card.dataset.id))) card.remove();
    let previous = null;
    for (const [item, index] of visible) {
      const html = this.renderCard(item);
      let record = this.cards.get(item.id);
      if (!record || record.html !== html) {
        const template = document.createElement("template");
        template.innerHTML = html;
        let card = template.content.firstElementChild;
        if (record) {
          const active = document.activeElement;
          if (record.card.contains(active) && active !== record.card)
            restoreFocus = {id:item.id, checkbox:active.matches("input")};
          const oldImage = record.card.querySelector("img"), newImage = card.querySelector("img");
          if (oldImage && newImage?.getAttribute("src") === oldImage.getAttribute("src")) newImage.replaceWith(oldImage);
          record.card.setAttribute("aria-label", card.getAttribute("aria-label"));
          record.card.replaceChildren(...card.childNodes);
          card = record.card;
        }
        record = {html, card};
      }
      this.cards.delete(item.id); this.cards.set(item.id, record);
      const card = record.card;
      card.setAttribute("role", "listitem");
      card.setAttribute("aria-posinset", index + 1);
      card.setAttribute("aria-setsize", this.total);
      card.style.cssText = index < start || index >= end
        ? `position:absolute;top:${Math.floor(index / columns) * stride}px;left:${index % columns * (widths[0] + gap)}px;width:${widths[0]}px;height:${height}px` : "";
      const next = previous ? previous.nextElementSibling : root.firstElementChild;
      if (card !== next) root.insertBefore(card, next);
      const image = card.querySelector("img");
      if (image?.complete && !image.naturalWidth) this.onImage({target:image, type:"error"});
      previous = card;
    }
    for (const [id, record] of this.cards) {
      if (!this.indices.has(id) || (this.cards.size > 240 && !root.contains(record.card))) this.cards.delete(id);
    }
    if (scrollDelta) window.scrollTo(0, scrollTarget);
    if (keepFocus) focused?.scrollIntoView({block:"nearest"});
    this.onRender();
    if (restoreFocus) {
      const card = this.cards.get(restoreFocus.id)?.card;
      (restoreFocus.checkbox ? card?.querySelector("input") : card || root)?.focus({preventScroll:true});
    }
  }

  focus(index, checkbox = false) {
    index = Math.max(0, Math.min(this.items.length - 1, index));
    this.render(index);
    const card = this.cards.get(this.items[index]?.id)?.card;
    (checkbox ? card?.querySelector("input") : card)?.focus({preventScroll:true});
    card?.scrollIntoView({block:"nearest"});
  }

  keydown(event) {
    if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
    const card = event.target.closest(".card");
    const index = this.indices.get(Number(card?.dataset.id));
    if (index === undefined) return;
    let target;
    if (event.target === card) {
      target = {ArrowRight:index + 1, ArrowLeft:index - 1, ArrowDown:index + this.columns,
        ArrowUp:index - this.columns, Home:0, End:this.items.length - 1}[event.key];
    }
    if (event.key === "Tab" && !event.shiftKey && event.target.matches("input") && index === this.end - 1 && index + 1 < this.items.length) target = index + 1;
    if (event.key === "Tab" && event.shiftKey && event.target === card && index === this.start && index > 0) target = index - 1;
    if (target === undefined) return;
    event.preventDefault();
    this.focus(target, event.key === "Tab" && event.shiftKey);
  }

  dispose() {
    cancelAnimationFrame(this.frame);
    this.resize.disconnect(); this.dialogs.disconnect();
    window.removeEventListener("scroll", this.schedule);
    window.removeEventListener("resize", this.schedule);
    this.root.removeEventListener("keydown", this.onKey);
    this.root.removeEventListener("error", this.onImage, true);
    this.root.removeEventListener("load", this.onImage, true);
    this.cards.clear();
  }
}
