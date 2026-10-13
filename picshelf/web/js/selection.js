// Explicit IDs survive loading and sorting. A query becomes fixed at preview.
export class ImageSelection {
  constructor() { this.ids = new Set(); this.allMatching = false; }
  toggle(id, on) {
    this.allMatching = false;
    if (on) this.ids.add(id); else this.ids.delete(id);
  }
  range(id, on, items, extend = false) {
    const start = items.findIndex(item => item.id === this.anchor);
    const end = items.findIndex(item => item.id === id);
    if (extend && start >= 0 && end >= 0) {
      for (const item of items.slice(Math.min(start, end), Math.max(start, end) + 1)) this.toggle(item.id, on);
    } else this.toggle(id, on);
    this.anchor = id;
  }
  loaded(items) {
    this.allMatching = false;
    for (const item of items) this.ids.add(item.id);
  }
  all() { this.ids.clear(); this.allMatching = true; }
  clear() { this.ids.clear(); this.allMatching = false; this.anchor = null; }
  payload(source) {
    if (!this.allMatching) return {image_ids: [...this.ids]};
    const query = {query: source.q, sort: source.sort, ...source.filters};
    if (query.favorites === "true") query.favorites = true;
    return {query};
  }
}
