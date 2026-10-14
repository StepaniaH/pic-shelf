"""Single-decode encoding and preservation of published output under source races."""
import hashlib
import os

import pytest
from PIL import Image

from picshelf import hashing, indexer
from picshelf.errors import ServiceError
from tests.datasets import image_samples


def test_one_normalized_decode_produces_versioned_results(tmp_path, monkeypatch):
    image_samples(tmp_path / 'inputs')
    opened, normalized = [], []
    original_open, original_color = hashing._open_image, hashing._normalized_color
    def decode(path):
        opened.append(path.name)
        return original_open(path)
    def color(image):
        normalized.append(image.size)
        return original_color(image)
    monkeypatch.setattr(hashing, '_open_image', decode)
    monkeypatch.setattr(hashing, '_normalized_color', color)
    source = tmp_path / 'inputs/orientation.jpg'
    result = hashing.encode_image(source, tmp_path / 'media/thumbs', tmp_path / 'media/previews', 1)
    assert opened == ['orientation.jpg'] and normalized == [(40, 80)]
    assert (result.width, result.height) == (40, 80)
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.phash_version == result.derivative_version == 2
    with Image.open(tmp_path / 'media' / result.preview_rel) as image:
        top, bottom = image.getpixel((20, 10)), image.getpixel((20, 70))
        assert top[0] > top[2] and bottom[2] > bottom[0]


@pytest.mark.parametrize('change', ['same_size_time', 'replace', 'hash_completion'])
def test_source_changes_after_hash_never_replace_published_outputs(tmp_path, monkeypatch, change):
    source = tmp_path / 'source.bmp'
    Image.new('RGB', (40, 40), 'red').save(source)
    thumbs, previews = tmp_path / 'media/thumbs', tmp_path / 'media/previews'
    old = hashing.encode_image(source, thumbs, previews, 7)
    paths = [tmp_path / 'media' / old.thumb_rel, tmp_path / 'media' / old.preview_rel]
    before = {path: path.read_bytes() for path in paths}
    stat = source.stat()
    original = hashing._open_image
    def changed(path):
        replacement = path if change == 'same_size_time' else tmp_path / 'replacement.bmp'
        Image.new('RGB', (40, 40), 'blue').save(replacement)
        if change == 'replace':
            replacement.replace(path)
        assert path.stat().st_size == stat.st_size
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return original(path)
    if change == 'hash_completion':
        original_hash = hashing.sha256_file
        calls = 0
        def changed_hash(path):
            nonlocal calls
            digest = original_hash(path)
            calls += 1
            if calls == 2:
                Image.new('RGB', (40, 40), 'blue').save(path)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return digest
        monkeypatch.setattr(hashing, 'sha256_file', changed_hash)
    else:
        monkeypatch.setattr(hashing, '_open_image', changed)
    with pytest.raises(ServiceError) as failure:
        hashing.encode_image(source, thumbs, previews, 7, expected_sha256=old.sha256, source_stat=stat)
    assert failure.value.code == 'image_changed' and failure.value.retryable
    assert {path: path.read_bytes() for path in paths} == before
    assert not list((tmp_path / 'media').rglob('.encode-*'))
    assert not list((tmp_path / 'media').rglob('.thumb-*'))


def test_expected_digest_is_checked_before_publishing(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (30, 20), 'blue').save(source)
    with pytest.raises(ServiceError) as failure:
        hashing.encode_image(source, tmp_path / 'thumbs', tmp_path / 'previews', 1, expected_sha256='0' * 64)
    assert failure.value.code == 'image_changed'
    assert not list(tmp_path.rglob('*.webp'))


def test_full_rescan_uses_one_decode_and_two_integrity_hashes_per_image(conn, cfg, library, monkeypatch):
    indexer.scan_library(conn, cfg, library['id'])
    opened, hashed = [], []
    original_open, original_hash = hashing._open_image, hashing.sha256_file
    def decode(path):
        opened.append(path)
        return original_open(path)
    def digest(path, *args, **kwargs):
        hashed.append(path)
        return original_hash(path, *args, **kwargs)
    monkeypatch.setattr(hashing, '_open_image', decode)
    monkeypatch.setattr(hashing, 'sha256_file', digest)
    result = indexer.scan_library(conn, cfg, library['id'], full=True)
    assert result['updated'] == 4 and not result['errors']
    assert len(opened) == 4 and len(hashed) == 8
