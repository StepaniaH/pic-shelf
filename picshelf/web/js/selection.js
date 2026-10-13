// Explicit IDs survive loading and sorting. A query becomes fixed at preview.
export class ImageSelection {
  constructor() { this.ids = new Set(); this.allMatching = false; }
  toggle(id, on) {
    this.allMatching = false;
    if (on) this.ids.add(id); else this.ids.delete(id);
  }
  loaded(items) {
    this.allMatching = false;
    for (const item of items) this.ids.add(item.id);
  }
  all() { this.ids.clear(); this.allMatching = true; }
  clear() { this.ids.clear(); this.allMatching = false; }
  payload(source) {
    if (!this.allMatching) return {image_ids: [...this.ids]};
    const query = {query: source.q, sort: source.sort, ...source.filters};
    if (query.favorites === "true") query.favorites = true;
    return {query};
  }
}
