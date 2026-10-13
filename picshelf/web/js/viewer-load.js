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
