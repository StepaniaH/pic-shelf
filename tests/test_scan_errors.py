"""Scan failures keep recoverable messages and preserve catalog relationships."""
import hashlib
import os

import pytest
from PIL import Image

from picshelf import hashing, i18n, indexer, libsvc, ops
from tests.datasets import draw_image, file_inventory
from tests.scenario_support import metadata_snapshot


@pytest.mark.parametrize(('lang', 'recovery'), [
    ('zh', '请恢复原路径后重扫'),
    ('en', 'restore their original paths and scan again'),
])
def test_ambiguous_move_keeps_localized_recovery(conn, cfg, admin, tmp_path, lang, recovery):
    root = tmp_path / 'ambiguous'
    draw_image(root / 'original.png', 1)
    lid = libsvc.create_library(conn, 'ambiguous', str(root))
    indexer.scan_library(conn, cfg, lid)
    image_id = conn.execute('SELECT id FROM images').fetchone()[0]
    ops.set_rating(conn, image_id, admin['id'], 0)
    before = metadata_snapshot(conn)
    (root / 'original.png').rename(root / 'first.png')
    (root / 'second.png').write_bytes((root / 'first.png').read_bytes())
    files = file_inventory(root)
    token = i18n.push_lang(lang)
    try:
        result = indexer.scan_library(conn, cfg, lid)
    finally:
        i18n.pop_lang(token)
    assert (result['added'], result['moved'], result['missing']) == (0, 0, 1)
    assert len(result['ambiguous_moves']) == 1 and len(result['errors']) == 2
    assert all(recovery in error for error in result['errors'])
    assert conn.execute('SELECT id,rel_path FROM images').fetchone()[:] == (image_id, 'original.png')
    assert metadata_snapshot(conn) == before
    assert file_inventory(root) == files


@pytest.mark.parametrize(('lang', 'recovery'), [
    ('zh', '请保存文件后重新扫描'),
    ('en', 'finish saving the file and scan again'),
])
def test_pending_source_change_keeps_recovery_and_previous_catalog(conn, cfg, library, admin, monkeypatch, lang, recovery):
    indexer.scan_library(conn, cfg, library['id'])
    image_id = conn.execute('SELECT id FROM images ORDER BY id').fetchone()[0]
    ops.set_rating(conn, image_id, admin['id'], 0)
    before = metadata_snapshot(conn)
    old_files = file_inventory(library['root'])
    source = library['root'] / 'pending.bmp'
    Image.new('RGB', (40, 40), 'red').save(source)
    stat = source.stat()
    expected = None
    calls = 0
    original_hash = hashing.sha256_file

    def change_after_hash(path, *args, **kwargs):
        nonlocal calls, expected
        digest = original_hash(path, *args, **kwargs)
        if path == source:
            calls += 1
            if calls == 1:
                Image.new('RGB', (40, 40), 'blue').save(source)
                assert source.stat().st_size == stat.st_size
                os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                expected = hashlib.sha256(source.read_bytes()).hexdigest()
        return digest

    monkeypatch.setattr(hashing, 'sha256_file', change_after_hash)
    token = i18n.push_lang(lang)
    try:
        result = indexer.scan_library(conn, cfg, library['id'])
    finally:
        i18n.pop_lang(token)
    assert result['added'] == 0 and result['missing'] == 0
    assert len(result['errors']) == 1 and recovery in result['errors'][0]
    assert metadata_snapshot(conn) == before
    assert conn.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 4
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
    assert {key: value for key, value in file_inventory(library['root']).items() if key != 'pending.bmp'} == old_files
