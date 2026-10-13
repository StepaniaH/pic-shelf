"""Generated inputs and independent observations; no application imports."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import random
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw

SEED = 20261013
CATALOG = json.loads((Path(__file__).parent / "fixtures/catalog.json").read_text())


def draw_image(path: Path, number: int, size=(96, 64)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, ((number * 37) % 256, 110, 180))
    ImageDraw.Draw(image).rectangle((number % 40, 4, 45, 30), fill=(10, number, 30))
    image.save(path)


def catalog_files(root: Path) -> dict:
    for number, item in enumerate(CATALOG["images"], 1):
        draw_image(root / item["library"] / item["path"], number)
    return file_inventory(root)


def file_inventory(root: Path) -> dict:
    """Observe bytes and file identity without using the product's hash code."""
    return {
        path.relative_to(root).as_posix(): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "device": path.stat().st_dev,
            "inode": path.stat().st_ino,
        }
        for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
    }


def boundary_files(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    names = ["中文.png", "space name.png", "quote'name.png", "comma,name.png",
             "100%.png", "under_score.png", "e\u0301.png", "x" * 180 + ".png"]
    for number, name in enumerate(names, 1):
        draw_image(root / name, number)
    (root / "broken.png").write_bytes(b"not an image\x00")
    draw_image(root / "Case.png", 20)
    capabilities = {"case_sensitive": not (root / "case.png").exists(),
                    "unicode_distinct": not (root / "é.png").exists()}
    (root / "duplicate.png").write_bytes((root / names[0]).read_bytes())
    for kind in ("hardlink", "symlink"):
        try:
            if kind == "hardlink": os.link(root / names[0], root / "hardlink.png")
            else: (root / "symlink.png").symlink_to(names[0])
            capabilities[kind] = True
        except OSError as exc:
            capabilities[kind] = {"unavailable_errno": exc.errno}
    locked = root / "permissions"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        capabilities["readonly_enforced"] = not os.access(locked, os.W_OK)
    finally:
        locked.chmod(0o700)
    return {"source": "generated", "seed": SEED, "capabilities": capabilities,
            "files": file_inventory(root)}


def image_samples(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    rgb = Image.new("RGB", (80, 40), "red")
    ImageDraw.Draw(rgb).rectangle((40, 0, 79, 39), fill="blue")
    exif = Image.Exif(); exif[274] = 6
    rgb.save(root / "orientation.jpg", exif=exif)
    rgba = Image.new("RGBA", (40, 80), (0, 255, 0, 0))
    ImageDraw.Draw(rgba).rectangle((10, 20, 29, 59), fill=(0, 255, 0, 255))
    rgba.save(root / "alpha.png")
    rgb.convert("L").save(root / "gray.png")
    rgb.convert("CMYK").save(root / "cmyk.jpg")
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    rgb.save(root / "srgb.png", icc_profile=profile)
    blue = Image.new("RGB", rgb.size, "blue")
    for name in ("animation.gif", "pages.tiff"):
        rgb.save(root / name, save_all=True, append_images=[blue], duration=100, loop=0)
    return {"source": "generated geometry; synthetic ICC, not a color reference",
            "seed": SEED, "files": file_inventory(root),
            "expected": {"orientation.jpg": {"size": [40, 80], "top": "red", "bottom": "blue"},
                         "alpha.png": {"size": [40, 80], "corner_alpha": 0, "center_alpha": 255},
                         "animation.gif": {"frames": 2}, "pages.tiff": {"frames": 2}},
            "limits": ["No photographic color reference", "Large-pixel stress is opt-in"]}


def metadata_rows(count: int, library_id: int):
    """Metadata scale only: these entries do not claim to have decoded images."""
    rng = random.Random(SEED)
    for number in range(count):
        filename = f"image-{number:06}.png"
        yield {"library_id": library_id, "rel_path": filename, "filename": filename,
               "ext": "png", "size_bytes": 1024, "width": 96, "height": 64,
               "mtime": 1700000000, "sha256": hashlib.sha256(filename.encode()).hexdigest(),
               "phash": f"{rng.getrandbits(64):016x}", "imported_at": "2026-10-01T00:00:00+00:00"}


FAULTS = {
    "disk_full": {"errno": errno.ENOSPC, "invariant": "source bytes remain intact"},
    "cross_device": {"errno": errno.EXDEV, "invariant": "copy fallback preserves bytes"},
    "permission": {"errno": errno.EACCES, "invariant": "existing metadata stays visible"},
    "interruption": {"events": ["plan", "write", "interrupt", "retry"],
                     "invariant": "successful items are not duplicated"},
    "source_change": {"events": ["plan", "change_source", "execute"],
                      "invariant": "changed source is rejected"},
    "target_conflict": {"events": ["plan", "create_target", "execute"],
                        "invariant": "target is not overwritten"},
}
