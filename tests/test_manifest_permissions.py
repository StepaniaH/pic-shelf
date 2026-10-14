"""Stable portable album identities never bypass current member permissions."""
import secrets

import pytest

from picshelf import auth, db, indexer, libsvc, manifest, ops
from picshelf.access import AccessContext
from picshelf.errors import ServiceError
from tests.datasets import draw_image
from tests.scenario_support import metadata_snapshot


@pytest.fixture
def album_manifest(conn, cfg, library, tmp_path):
    indexer.scan_library(conn, cfg, library['id'])
    owner = auth.create_user(conn, 'editor', secrets.token_urlsafe(24))
    group_id = conn.execute('SELECT group_id FROM user_groups WHERE user_id=?', (owner,)).fetchone()[0]
    conn.execute('DELETE FROM library_grants WHERE group_id=?', (group_id,))
    conn.execute("INSERT INTO library_grants VALUES(?,?,'edit')", (group_id, library['id']))
    conn.commit()
    rows = conn.execute('SELECT id,rel_path FROM images ORDER BY id').fetchall()
    album_id = ops.create_album(conn, 'Portable album', library['id'])['id']
    ops.album_add_images(conn, album_id, [rows[0]['id']])
    access = AccessContext(owner)
    document = manifest.export(conn, access, [library['id']])
    album_uid = next(row['id'] for row in document['records']['albums'] if row['name'] == 'Portable album')
    second_uid = next(row['id'] for row in document['records']['images'] if row['rel_path'] == rows[1]['rel_path'])
    document['records']['album_images'].append({'album_id': album_uid, 'image_id': second_uid,
                                               'position': 1, 'added_at': db.utcnow()})
    document['checksum'] = manifest.checksum({key: value for key, value in document.items() if key != 'checksum'})
    mappings = {'libraries': {document['records']['libraries'][0]['id']: library['id']},
                'users': {document['owner']: owner}}
    hidden_root = tmp_path / 'hidden'
    draw_image(hidden_root / 'hidden.png', 19)
    hidden_library = libsvc.create_library(conn, 'hidden', str(hidden_root))
    indexer.scan_library(conn, cfg, hidden_library)
    conn.execute('DELETE FROM library_grants WHERE group_id=? AND library_id=?', (group_id, hidden_library))
    conn.commit()
    hidden_image = conn.execute('SELECT id FROM images WHERE library_id=?', (hidden_library,)).fetchone()[0]
    ops.album_add_images(conn, album_id, [hidden_image])
    return access, document, mappings, album_id, group_id, hidden_library


def test_automatic_album_reuse_requires_every_current_member_library(conn, cfg, album_manifest):
    access, document, mappings, album_id, _, _ = album_manifest
    with pytest.raises(ServiceError) as denied:
        access.require_album(conn, album_id, 'edit')
    assert denied.value.code == 'permission_denied'
    before = metadata_snapshot(conn)
    plan = manifest.preview(conn, access, document, mappings)
    assert not plan['preview']['ready']
    assert any(row['table'] == 'albums' and row['code'] == 'permission_denied' for row in plan['preview']['conflicts'])
    with pytest.raises(ServiceError):
        manifest.execute(conn, cfg, access, plan['id'])
    assert metadata_snapshot(conn) == before


def test_album_permissions_are_rechecked_after_preview(conn, cfg, album_manifest):
    access, document, mappings, album_id, group_id, hidden_library = album_manifest
    conn.execute("INSERT INTO library_grants VALUES(?,?,'edit')", (group_id, hidden_library))
    conn.commit()
    plan = manifest.preview(conn, access, document, mappings)
    assert plan['preview']['ready'], plan['preview']['conflicts']
    conn.execute('DELETE FROM library_grants WHERE group_id=? AND library_id=?', (group_id, hidden_library))
    conn.commit()
    before = metadata_snapshot(conn)
    with pytest.raises(ServiceError):
        manifest.execute(conn, cfg, access, plan['id'])
    assert manifest.get_plan(conn, access, plan['id'])['state'] == 'preview'
    assert metadata_snapshot(conn) == before
    with pytest.raises(ServiceError):
        access.require_album(conn, album_id, 'edit')
