"""图片编码：文件哈希（SHA-256）、感知哈希（pHash）、缩略图/预览图。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

try:  # Pillow < 10 兼容；AVIF 读取需要较新版本
    Image.MAX_IMAGE_PIXELS = 500_000_000  # 5 亿像素上限，防解压炸弹误伤大图
except Exception:  # pragma: no cover
    pass

try:
    import imagehash
except ImportError:  # pragma: no cover
    imagehash = None  # type: ignore

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp",
    ".bmp", ".tif", ".tiff", ".avif",
}

THUMB_MAX = 400
PREVIEW_MAX = 1600


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _open_image(path: Path) -> Image.Image:
    img = Image.open(path)
    img.load()
    if getattr(img, "n_frames", 1) > 1:  # 动图：编码只取第一帧
        img.seek(0)
        img = img.copy()
    return img


def phash_file(path: Path) -> str | None:
    """感知哈希（16 进制）；解码失败返回 None。"""
    if imagehash is None:  # pragma: no cover
        return None
    try:
        with _open_image(path) as img:
            return str(imagehash.phash(img.convert("RGB"), hash_size=8))
    except Exception:
        return None


def hamming(a: str, b: str) -> int:
    """两个 16 进制 pHash 的汉明距离。"""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _shard_dir(base: Path, image_id: int) -> Path:
    d = base / f"{image_id % 256:02x}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_derivatives(
    src: Path,
    thumbs_dir: Path,
    previews_dir: Path,
    image_id: int,
) -> tuple[str | None, str | None, int | None, int | None]:
    """生成缩略图与预览图（WebP）。

    返回 (thumb_rel, preview_rel, width, height)；thumb/preview 相对 media_dir。
    无法解码时抛异常，由调用方记录为扫描错误。
    """
    with _open_image(src) as img:
        img = ImageOps.exif_transpose(img) or img
        width, height = img.size
        if img.mode in ("P", "LA") or (img.mode == "RGBA"):
            # 保留透明通道（PNG 抠图类）；其余统一 RGB
            base = img.convert("RGBA")
        else:
            base = img.convert("RGB")

        thumb = base.copy()
        thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
        tpath = _shard_dir(thumbs_dir, image_id) / f"{image_id}.webp"
        thumb.save(tpath, "WEBP", quality=82, method=4)

        preview = base.copy()
        preview.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)
        ppath = _shard_dir(previews_dir, image_id) / f"{image_id}.webp"
        preview.save(ppath, "WEBP", quality=85, method=4)

    thumb_rel = str(tpath.relative_to(thumbs_dir.parent))
    preview_rel = str(ppath.relative_to(previews_dir.parent))
    return thumb_rel, preview_rel, width, height
