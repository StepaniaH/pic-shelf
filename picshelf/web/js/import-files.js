// Preserve relative folders for both native selection and dropped directories.
const imageExtension = /\.(jpe?g|jfif|png|gif|webp|bmp|tiff?|avif)$/i;

export function imageFiles(files) {
  const seen = new Set();
  return files.filter(item => imageExtension.test(item.path)).map(item => {
    let path = item.path, suffix = 2;
    const key = value => value.normalize("NFC").toLowerCase();
    // Equal names can come from separate dropped folders. Keep both contents;
    // the server deduplicates by hash after staging, never by filename alone.
    while (seen.has(key(path))) path = item.path.replace(/(\.[^/.]+)$/, `-${suffix++}$1`);
    seen.add(key(path));
    return {...item, path};
  });
}

export function selectedFiles(files) {
  return Array.from(files, file => ({file, path: file.webkitRelativePath || file.name}));
}

export async function droppedFiles(transfer) {
  // Capture entries synchronously: browsers clear the transfer after drop returns.
  const fallback = selectedFiles(transfer.files);
  const entries = Array.from(transfer.items || [], item => item.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return fallback;
  const files = [];
  async function walk(entry, prefix = "") {
    const path = prefix + entry.name;
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      files.push({file, path});
      if (files.length > 10000) throw new Error("file_limit");
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      // Chromium returns directory entries in batches, often of 100 items.
      while (true) {
        const children = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        if (!children.length) break;
        for (const child of children) await walk(child, path + "/");
      }
    }
  }
  for (const entry of entries) await walk(entry);
  return files;
}
