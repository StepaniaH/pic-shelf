// Publish metadata and decoded pixels as one frame. Superseded work has no result.
export class ViewerLoader {
  constructor({read, decode}) { this.read = read; this.decode = decode; this.version = 0; }
  cancel() { this.version++; this.controller?.abort(); }
  async load(id, mode) {
    this.cancel();
    const version = this.version;
    const controller = this.controller = new AbortController();
    try {
      const detail = await this.read(id, controller.signal);
      if (version !== this.version) return null;
      const image = await this.decode(detail, mode, controller.signal);
      return version === this.version ? {detail, image} : null;
    } catch (error) {
      if (version !== this.version) return null;
      throw error;
    }
  }
}

// Share in-flight decodes as well as finished images. The cache belongs to one
// open viewer; authorization is still checked before publishing each frame.
export class PreviewCache {
  constructor({limit = 3, decode = decodeImage} = {}) {
    this.limit = limit;
    this.decode = decode;
    this.entries = new Map();
  }
  load(url, signal) {
    if (signal?.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
    let entry = this.entries.get(url);
    this.entries.delete(url);
    if (!entry) {
      const controller = new AbortController();
      entry = {controller};
      entry.promise = this.decode(url, controller.signal).catch(error => {
        if (this.entries.get(url) === entry) this.entries.delete(url);
        throw error;
      });
    }
    this.entries.set(url, entry);
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value;
      this.entries.get(oldest).controller.abort();
      this.entries.delete(oldest);
    }
    if (!signal) return entry.promise;
    return new Promise((resolve, reject) => {
      const abort = () => reject(new DOMException("Aborted", "AbortError"));
      signal.addEventListener("abort", abort, {once:true});
      entry.promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
    });
  }
  warm(urls) {
    for (const url of new Set(urls)) this.load(url).catch(() => {});
  }
  clear() {
    for (const entry of this.entries.values()) entry.controller.abort();
    this.entries.clear();
  }
}

export function decodeImage(url, signal, timeout = 20000) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    let finished = false;
    const finish = (error) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      image.onload = image.onerror = null;
      if (error) { image.removeAttribute("src"); reject(error); }
      else resolve(image);
    };
    const abort = () => finish(new DOMException("Aborted", "AbortError"));
    const timer = setTimeout(() => finish(new Error("image_timeout")), timeout);
    image.onload = async () => {
      try { await image.decode(); finish(); }
      catch { finish(new Error("image_decode_failed")); }
    };
    image.onerror = () => finish(new Error("image_load_failed"));
    signal?.addEventListener("abort", abort, {once:true});
    if (signal?.aborted) { abort(); return; }
    image.decoding = "async";
    image.src = url;
  });
}
