"""Single-decode encoding and preservation of published output under source races."""
import hashlib
import os

import pytest
from PIL import Image

from picshelf import hashing, indexer, ops
from picshelf.errors import ServiceError
from tests.datasets import file_inventory, image_samples
from tests.scenario_support import cli, metadata_snapshot


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
    assert result.phash_version == result.derivative_version == 3
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


@pytest.mark.parametrize(('mode', 'hidden_colors', 'visible', 'profile'), [
    ('L', (0, 255), 120, False),
    ('RGB', ((255, 0, 0), (0, 0, 255)), (0, 180, 0), False),
    ('RGB', ((255, 0, 0), (0, 0, 255)), (0, 180, 0), True),
])
def test_png_transparent_color_survives_encoding_and_hidden_color_does_not_change_phash(tmp_path, mode, hidden_colors, visible, profile):
    results = []
    for number, hidden in enumerate(hidden_colors, 1):
        source = tmp_path / f'transparent-{number}.png'
        with Image.new(mode, (80, 40), hidden) as image:
            image.paste(visible, (20, 10, 60, 30))
            options = {'icc_profile': hashing._SRGB.tobytes()} if profile else {}
            image.save(source, transparency=hidden, **options)
        before = source.read_bytes()
        with Image.open(source) as original:
            assert original.mode == mode and 'transparency' in original.info
            assert original.convert('RGBA').getpixel((0, 0))[3] == 0
        result = hashing.encode_image(source, tmp_path / 'media/thumbs', tmp_path / 'media/previews', number)
        for rel in (result.thumb_rel, result.preview_rel):
            with Image.open(tmp_path / 'media' / rel) as derived:
                assert derived.size == (80, 40)
                assert derived.convert('RGBA').getpixel((0, 0))[3] == 0
                assert derived.convert('RGBA').getpixel((40, 20))[3] == 255
        assert source.read_bytes() == before
        assert result.phash_version == result.derivative_version == 3
        results.append(result)
    assert results[0].phash is not None and results[0].phash == results[1].phash


def test_version_two_transparent_color_cache_requires_explicit_rebuild(conn, cfg, library, admin):
    source = library['root'] / 'transparent-color.png'
    with Image.new('RGB', (80, 40), 'red') as image:
        image.paste('green', (20, 10, 60, 30))
        image.save(source, transparency=(255, 0, 0))
    indexer.scan_library(conn, cfg, library['id'])
    row = conn.execute("SELECT * FROM images WHERE filename='transparent-color.png'").fetchone()
    ops.set_rating(conn, row['id'], admin['id'], 0)
    previous = {}
    old_rels = []
    for field in ('thumb_rel', 'preview_rel'):
        old_rel = row[field].replace('-v3-', '-v2-')
        old_path = cfg.media_dir / old_rel
        with Image.open(source) as image, image.convert('RGB') as opaque:
            opaque.save(old_path, 'WEBP')
        previous[old_rel] = old_path.read_bytes()
        old_rels.append(old_rel)
    conn.execute('UPDATE images SET phash_version=2,derivative_version=2,thumb_rel=?,preview_rel=? WHERE id=?',
                 (*old_rels, row['id']))
    conn.commit()
    originals, relationships = file_inventory(library['root']), metadata_snapshot(conn)
    assert indexer.scan_library(conn, cfg, library['id'])['unchanged'] == 5
    assert conn.execute('SELECT derivative_version FROM images WHERE id=?', (row['id'],)).fetchone()[0] == 2
    result = cli(cfg, 'rebuild', 'testlib', '--outdated-only')
    assert result.returncode == 0, result.stderr
    updated = conn.execute('SELECT * FROM images WHERE id=?', (row['id'],)).fetchone()
    assert updated['phash_version'] == updated['derivative_version'] == 3
    assert updated['thumb_rel'] != old_rels[0] and updated['sha256'] == row['sha256']
    with Image.open(cfg.media_dir / updated['thumb_rel']) as image:
        assert image.convert('RGBA').getpixel((0, 0))[3] == 0
    assert {rel: (cfg.media_dir / rel).read_bytes() for rel in previous} == previous
    assert file_inventory(library['root']) == originals and metadata_snapshot(conn) == relationships
