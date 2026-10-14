"""图片编码：文件哈希（SHA-256）、感知哈希（pHash）、缩略图/预览图。"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .errors import ServiceError
from .i18n import t

PHASH_VERSION = 3
DERIVATIVE_VERSION = 3
# Admission budgets bound decoded dimensions and input size before allocation.
# They are not an operating-system RSS limit for native codecs.
MAX_WORKING_BYTES = 512 * 1024 * 1024
MAX_PIXELS = MAX_WORKING_BYTES // 16
MAX_SOURCE_BYTES = 256 * 1024 * 1024
_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
Image.init()
READ_FORMATS = [name for name in ("JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF", "AVIF") if name in Image.OPEN]

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
    # 将像素与文件句柄分离，动图/多页文件也在离开时关闭。
    def too_large():
        return ServiceError("image_too_large", t("图片超过解码资源预算，已跳过"),
                            next_step=t("请在独立副本中缩小图片后重试；原文件未更改"))
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise too_large()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=READ_FORMATS) as img:
                img.seek(0)
                if img.width * img.height > MAX_PIXELS:
                    raise too_large()
                return ImageOps.exif_transpose(img)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise too_large() from exc
    except UnidentifiedImageError as exc:
        raise ServiceError("image_decode_error", t("无法解码图片，请检查文件完整性及已安装的格式支持"),
                           next_step=t("请先用图像查看器打开独立副本，确认可读取后重扫")) from exc


def _normalized_color(img: Image.Image) -> Image.Image:
    alpha = None
    if img.mode == "P" or "A" in img.getbands() or "transparency" in img.info:
        with img.convert("RGBA") as rgba:
            alpha = rgba.getchannel("A")
    profile = img.info.get("icc_profile")
    try:
        if profile:
            try:
                source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                color_input = img if img.mode in ("RGB", "CMYK", "L", "LAB") else img.convert("RGB")
                base = ImageCms.profileToProfile(color_input, source_profile, _SRGB, outputMode="RGB",
                                                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
            except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                raise ServiceError("invalid_color_profile", t("无法读取图片的颜色配置，已保留原文件"),
                                   next_step=t("请用图像编辑器检查独立副本的颜色配置，再重新扫描")) from exc
        else:
            base = img.convert("RGB")
        if alpha is not None:
            base.putalpha(alpha)
        return base
    finally:
        if alpha is not None:
            alpha.close()


def phash_file(path: Path) -> str | None:
    """感知哈希（16 进制）；解码失败返回 None。"""
    if imagehash is None:  # pragma: no cover
        return None
    try:
        with _open_image(path) as img:
            with _normalized_color(img) as base:
                return _phash_pixels(base)
    except Exception:
        return None


def _phash_pixels(base):
    if imagehash is None:  # pragma: no cover
        return None
    try:
        # Normalize hidden transparent color onto white at hash size.
        small = base.resize((32, 32), Image.Resampling.LANCZOS)
        if small.mode == 'RGBA':
            with Image.new('RGBA', small.size, 'white') as background:
                flattened = Image.alpha_composite(background, small).convert('RGB')
            small.close()
            small = flattened
        with small:
            return str(imagehash.phash(small, hash_size=8))
    except Exception:
        return None


def hamming(a: str, b: str) -> int:
    """两个 16 进制 pHash 的汉明距离。"""
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _shard_dir(base: Path, image_id: int) -> Path:
    d = base / f"{image_id % 256:02x}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_webp(img: Image.Image, path: Path, quality: int) -> None:
    """同目录原子替换，读取者不会拿到半张图片。"""
    fd, name = tempfile.mkstemp(prefix=".thumb-", suffix=".webp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            img.save(output, "WEBP", quality=quality, method=4, icc_profile=_SRGB.tobytes())
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass(frozen=True)
class EncodedImage:
    sha256: str
    phash: str | None
    thumb_rel: str
    preview_rel: str
    width: int
    height: int
    phash_version: int
    derivative_version: int


def _source_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _changed():
    return ServiceError('image_changed', t('扫描期间图片内容变化，请保存文件后重新扫描'), retryable=True,
                        next_step=t('请等待文件保存完成后重新扫描；原文件未更改'))


def encode_image(
    src: Path,
    thumbs_dir: Path,
    previews_dir: Path,
    image_id: int,
    *, expected_sha256: str | None = None, source_stat=None,
) -> EncodedImage:
    """Decode once; publish derivatives only after the source is verified again."""
    before = src.stat()
    if source_stat is not None and _source_identity(before) != _source_identity(source_stat):
        raise _changed()
    digest = sha256_file(src)
    if expected_sha256 is not None and digest != expected_sha256:
        raise _changed()
    staged = []
    try:
        with _open_image(src) as img, _normalized_color(img) as base:
            width, height = img.size
            phash = _phash_pixels(base)
            name = f'{image_id}-v{DERIVATIVE_VERSION}-{digest}.webp'
            tpath = _shard_dir(thumbs_dir, image_id) / name
            ppath = _shard_dir(previews_dir, image_id) / name
            for target, bound, quality in ((tpath, THUMB_MAX, 82), (ppath, PREVIEW_MAX, 85)):
                fd, temporary = tempfile.mkstemp(prefix='.encode-', suffix='.webp', dir=target.parent)
                os.close(fd)
                temporary = Path(temporary)
                staged.append((temporary, target))
                with base.copy() as derivative:
                    derivative.thumbnail((bound, bound), Image.Resampling.LANCZOS)
                    _save_webp(derivative, temporary, quality)
        verified_digest = sha256_file(src)
        if _source_identity(src.stat()) != _source_identity(before) or verified_digest != digest:
            raise _changed()
        for temporary, target in staged:
            os.replace(temporary, target)
        return EncodedImage(digest, phash, str(tpath.relative_to(thumbs_dir.parent)),
                            str(ppath.relative_to(previews_dir.parent)), width, height,
                            PHASH_VERSION if phash is not None else 0, DERIVATIVE_VERSION)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def make_derivatives(src: Path, thumbs_dir: Path, previews_dir: Path, image_id: int):
    """Compatibility entry returning paths relative to media_dir and dimensions."""
    result = encode_image(src, thumbs_dir, previews_dir, image_id)
    return result.thumb_rel, result.preview_rel, result.width, result.height
