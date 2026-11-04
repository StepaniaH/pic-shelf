import {api, imagesQuery} from "./api.js";
import {t} from "./i18n.js";

// Pagination counts server rows, not the deduplicated cards on screen.
export class Source {
  constructor(title, context = {}, {q = "", sort = "newest", filters = {}, emptyText = t("没有图片")} = {}) {
    Object.assign(this, {title, context, q, sort, filters, emptyText});
    this.items = [];
    this.total = 0;
    this.errors = [];
    this.nextOffset = 0;
    this.loadedExtent = 0;
    this.exhausted = false;
    this.revision = 0;
    this.pending = null;
    this.controller = null;
    this.onChange = null;
  }

  async load(reset = true, {limit = 120} = {}) {
    if (reset) {
      this.controller?.abort();
      this.refreshSource?.cancel();
      this.revision++;
      this.pending = null;
    }
    const revision = this.revision;
    const offset = reset ? 0 : this.nextOffset;
    const controller = new AbortController();
    this.controller = controller;
    let result;
    try {
      result = await api("/images?" + imagesQuery({
        q: this.q, sort: this.sort, limit, offset, ...this.filters,
      }), {signal: controller.signal});
    } catch (error) {
      if (controller.signal.aborted || revision !== this.revision) return null;
      throw error;
    }
    if (controller.signal.aborted || revision !== this.revision) return null;
    this.total = this.ruleErrors?.length ? 0 : result.total;
    this.errors = [...new Set([...(this.ruleErrors || []), ...(result.errors || [])])];
    if (reset) this.items = [];
    const byId = new Map(this.items.map(item => [item.id, item]));
    if (!this.ruleErrors?.length) {
      for (const item of result.items) {
        if (byId.has(item.id)) Object.assign(byId.get(item.id), item);
        else { this.items.push(item); byId.set(item.id, item); }
      }
    }
    this.nextOffset = offset + result.items.length;
    this.loadedExtent = Math.max(reset ? 0 : this.loadedExtent, this.nextOffset);
    this.exhausted = result.items.length === 0 || this.errors.length > 0;
    return result;
  }

  async loadMore() {
    const revision = this.revision;
    if (this.refreshing) await this.refreshing;
    if (revision !== this.revision) return false;
    if (this.loadedAll) return false;
    if (!this.pending) {
      const pending = this.load(false).finally(() => {
        if (this.pending === pending) this.pending = null;
      });
      this.pending = pending;
    }
    return Boolean((await this.pending)?.items.length);
  }

  refresh({afterWrite = false} = {}) {
    if (this.refreshing && !afterWrite) return this.refreshing;
    this.refreshSource?.cancel();
    this.controller?.abort();
    this.pending = null;
    // Removing members must not shorten the range restored by a later undo.
    const extent = Math.max(this.nextOffset, this.loadedExtent);
    const revision = ++this.revision, count = extent || 120;
    const fresh = this.refreshSource = new Source(this.title, this.context, this);
    fresh.ruleErrors = this.ruleErrors;
    // Stage every page before replacing a long browsing range. A failed refresh
    // must leave the displayed cards and their backing data in agreement.
    const pending = (async () => {
      let result = await fresh.load(true, {limit:Math.min(10000, count)});
      while (result && revision === this.revision && !fresh.loadedAll && fresh.nextOffset < count)
        result = await fresh.load(false, {limit:Math.min(10000, count - fresh.nextOffset)});
      if (!result || revision !== this.revision) return null;
      for (const key of ["items", "total", "errors", "nextOffset", "exhausted"]) this[key] = fresh[key];
      this.loadedExtent = Math.max(extent, fresh.nextOffset);
      return result;
    })().finally(() => {
      if (this.refreshing === pending) { this.refreshing = null; this.refreshSource = null; }
    });
    this.refreshing = pending;
    return pending;
  }

  cancel() {
    this.revision++;
    this.controller?.abort();
    this.refreshSource?.cancel();
    this.pending = null;
  }

  get loadedAll() {
    return this.sort === "random" || this.exhausted || this.nextOffset >= this.total;
  }
}
