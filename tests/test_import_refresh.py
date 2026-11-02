"""Import refresh work is bounded by its saved destinations."""
import pytest
from PIL import Image

from picshelf import import_plans, indexer, libsvc, ops, reorganize
from picshelf.access import AccessContext


def picture(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new('RGB', (8, 8), color) as image:
        image.save(path)


@pytest.mark.parametrize('mode', ['copy', 'hardlink', 'move'])
@pytest.mark.parametrize('legacy_root', [False, True])
def test_one_import_visits_one_destination_and_keeps_existing_catalog(
        conn, cfg, admin, tmp_path, monkeypatch, mode, legacy_root):
    target, source = tmp_path / 'library', tmp_path / 'source'
    for number in range(12):
        picture(target / 'photos' / f'old-{number:02}.png', (number * 20, 0, 0))
    picture(source / 'photos' / 'new.png', (0, 255, 0))
    library = libsvc.create_library(conn, 'fixture', str(target))
    indexer.scan_library(conn, cfg, library)
    if legacy_root:
        conn.execute('UPDATE libraries SET root_device=NULL,root_inode=NULL WHERE id=?', (library,))
    existing = [dict(row) for row in conn.execute('SELECT * FROM images ORDER BY id')]
    ops.set_rating(conn, existing[0]['id'], admin['id'], 8)
    manual = ops.create_album(conn, 'Selected', library)['id']
    ops.album_add_images(conn, manual, [existing[0]['id']])
    conn.commit()
    # Unrelated filesystem changes belong to a later explicit library scan.
    picture(target / 'unindexed.png', (0, 0, 255))
    (target / existing[1]['rel_path']).unlink()
    access = AccessContext(admin['id'])
    plan = import_plans.create_plan(conn, access, library, str(source), mode=mode, album_id=manual)
    visited = []
    walk = indexer._iter_image_files

    def counted(*args, **kwargs):
        for path in walk(*args, **kwargs):
            visited.append(path.relative_to(target).as_posix())
            yield path

    monkeypatch.setattr(indexer, '_iter_image_files', counted)
    result = import_plans.execute_plan(conn, cfg, access, plan['plan_id'])
    assert result['state'] == 'succeeded', result
    assert visited == ['photos/new.png']
    assert result['scan']['processed'] == 1 and result['scan']['added'] == 1
    assert result['scan']['unchanged'] == 0 and result['scan']['missing'] == 0
    assert [dict(row) for row in conn.execute('SELECT * FROM images WHERE id<=12 ORDER BY id')] == existing
    assert conn.execute('SELECT value FROM ratings WHERE image_id=?', (existing[0]['id'],)).fetchone()[0] == 8
    new = conn.execute("SELECT id FROM images WHERE rel_path='photos/new.png'").fetchone()[0]
    assert {row[0] for row in conn.execute('SELECT image_id FROM album_images WHERE album_id=?', (manual,))} == {existing[0]['id'], new}
    folder = conn.execute("SELECT id,cover_image_id FROM albums WHERE kind='folder' AND rel_dir='photos'").fetchone()
    assert {row[0] for row in conn.execute('SELECT image_id FROM album_images WHERE album_id=?', (folder['id'],))} == {row['id'] for row in existing} | {new}
    assert folder['cover_image_id'] == existing[0]['id']
    if legacy_root:
        assert conn.execute('SELECT root_device FROM libraries WHERE id=?', (library,)).fetchone()[0] is None
    assert import_plans.execute_plan(conn, cfg, access, plan['plan_id']) == result
    assert visited == ['photos/new.png']


def test_duplicate_only_album_import_does_not_walk_library(conn, cfg, admin, tmp_path, monkeypatch):
    target, source = tmp_path / 'library', tmp_path / 'source'
    picture(target / 'existing.png', (10, 20, 30))
    picture(source / 'duplicate.png', (10, 20, 30))
    library = libsvc.create_library(conn, 'fixture', str(target))
    indexer.scan_library(conn, cfg, library)
    image = conn.execute('SELECT id FROM images').fetchone()[0]
    album = ops.create_album(conn, 'Selected', library)['id']
    access = AccessContext(admin['id'])
    plan = import_plans.create_plan(conn, access, library, str(source), mode='copy', album_id=album)
    visited = []
    walk = indexer._iter_image_files

    def counted(*args, **kwargs):
        for path in walk(*args, **kwargs):
            visited.append(path)
            yield path

    monkeypatch.setattr(indexer, '_iter_image_files', counted)
    result = import_plans.execute_plan(conn, cfg, access, plan['plan_id'])
    assert result['state'] == 'succeeded' and result['done']['skip'] == 1
    assert visited == []
    assert [row[0] for row in conn.execute('SELECT image_id FROM album_images WHERE album_id=?', (album,))] == [image]


def test_reorganization_refreshes_both_old_and_new_folder_memberships(conn, cfg, admin, tmp_path):
    root = tmp_path / 'library'
    picture(root / 'old' / 'image.png', (10, 20, 30))
    library = libsvc.create_library(conn, 'fixture', str(root), mode='managed')
    indexer.scan_library(conn, cfg, library)
    image = conn.execute('SELECT id FROM images').fetchone()[0]
    old_album = conn.execute("SELECT id FROM albums WHERE kind='folder' AND rel_dir='old'").fetchone()[0]
    ops.set_rating(conn, image, admin['id'], 8)
    access = AccessContext(admin['id'])
    plan = reorganize.preview(conn, cfg, access, library, 'new/{stem}{ext}')
    result = import_plans.execute_plan(conn, cfg, access, plan['plan_id'])
    assert result['state'] == 'succeeded' and result['scan']['processed'] == 1
    assert conn.execute('SELECT rel_path FROM images WHERE id=?', (image,)).fetchone()[0] == 'new/image.png'
    assert conn.execute('SELECT value FROM ratings WHERE image_id=?', (image,)).fetchone()[0] == 8
    assert conn.execute('SELECT COUNT(*) FROM album_images WHERE album_id=?', (old_album,)).fetchone()[0] == 0
    assert conn.execute('SELECT cover_image_id FROM albums WHERE id=?', (old_album,)).fetchone()[0] is None
    new_album = conn.execute("SELECT id,cover_image_id FROM albums WHERE kind='folder' AND rel_dir='new'").fetchone()
    assert new_album['cover_image_id'] == image
    assert [row[0] for row in conn.execute('SELECT image_id FROM album_images WHERE album_id=?', (new_album['id'],))] == [image]
