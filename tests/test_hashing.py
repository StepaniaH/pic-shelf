from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from picshelf import hashing


def test_sha256_deterministic(tmp_path: Path):
    f1 = tmp_path / "a.bin"
    f1.write_bytes(b"picshelf-test-content")
    f2 = tmp_path / "b.bin"
    f2.write_bytes(b"picshelf-test-content")
    f3 = tmp_path / "c.bin"
    f3.write_bytes(b"different")
    assert hashing.sha256_file(f1) == hashing.sha256_file(f2)
    assert hashing.sha256_file(f1) != hashing.sha256_file(f3)
    assert len(hashing.sha256_file(f1)) == 64


def _smooth(seed: int, size: int = 256) -> Image.Image:
    """平滑噪声图：贴近真实照片的 DCT 分布（纯渐变是 pHash 退化输入）。"""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (16, 16, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize((size, size), Image.Resampling.BICUBIC)
    return img.filter(ImageFilter.GaussianBlur(3))


def test_phash_similarity(tmp_path: Path):
    base = _smooth(1)
    modified = base.copy()
    modified.paste((255, 255, 255), (150, 150, 200, 220))  # 局部小改动
    other = _smooth(99)  # 完全无关的图

    p1 = tmp_path / "base.png"
    base.save(p1)
    p2 = tmp_path / "mod.png"
    modified.save(p2)
    p3 = tmp_path / "other.png"
    other.save(p3)

    h1 = hashing.phash_file(p1)
    h2 = hashing.phash_file(p2)
    h3 = hashing.phash_file(p3)
    assert h1 and h2 and h3 and len(h1) == 16
    d12 = hashing.hamming(h1, h2)
    d13 = hashing.hamming(h1, h3)
    assert d12 <= 20, f"轻微改动距离过大: {d12}"
    assert d13 >= 25, f"无关图距离应更大: {d13}"
    assert d13 > d12


def test_make_derivatives(tmp_path: Path):
    src = tmp_path / "big.png"
    Image.new("RGB", (1200, 900), (120, 80, 60)).save(src)
    thumbs = tmp_path / "media" / "thumbs"
    previews = tmp_path / "media" / "previews"
    thumb_rel, preview_rel, w, h = hashing.make_derivatives(src, thumbs, previews, 42)
    assert (w, h) == (1200, 900)
    tpath = thumbs.parent / thumb_rel
    ppath = previews.parent / preview_rel
    assert tpath.exists() and ppath.exists()
    with Image.open(tpath) as t:
        assert max(t.size) <= 400
    with Image.open(ppath) as p:
        assert max(p.size) <= 1600


def test_phash_corrupt_returns_none(tmp_path: Path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert hashing.phash_file(bad) is None
