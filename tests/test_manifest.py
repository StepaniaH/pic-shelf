"""Restore known user work onto copied originals with unrelated local row IDs."""
import copy
import json
import secrets
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db, indexer, libsvc, manifest, metadata, ops, play_rules, saved_queries
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.scenario_support import build_catalog, cli, metadata_snapshot


@pytest.fixture
def collection(tmp_path):
    source = build_catalog(tmp_path / 'source')
    with db.open_db(source.cfg.db_path) as conn:
        reader = AccessContext(source.users['reader'], local_admin=True)
        iid = source.images['n01']
        metadata.mark(conn, reader, [iid], 'done')
        metadata.add_source(conn, reader, iid, original_filename='original name.png',
                            collected_at='2026-10-10T12:00:00+08:00', url='https://example.com/art/42', note='Source note')
        ops.add_comment(conn, iid, reader.user_id, 'First portable comment')
        ops.add_note(conn, iid, reader.user_id, 'Precise portable note', '{"x":0.25}')
        album = conn.execute("SELECT id FROM albums WHERE name='Selections'").fetchone()[0]
        conn.execute('UPDATE albums SET cover_image_id=? WHERE id=?', (iid, album))
        tag = conn.execute("SELECT id FROM tags WHERE name='色彩:红色'").fetchone()[0]
        conn.execute('INSERT INTO tag_aliases(entity_id,name,match_key,created_by,created_at) VALUES(?,?,?,?,?)',
                     (tag, 'Crimson', 'crimson', reader.user_id, db.utcnow()))
        conn.commit()
        saved_queries.create(conn, reader, name='Zero and unrated', query=f'album:{album} rated:true rating:0', kind='smart')
        play_rules.create(conn, reader, name='Selected playback', spec={'source': {'album_id': album}, 'order': 'shuffle', 'seed': 42})
        document = manifest.export(conn, reader, [source.libraries['north']])
    target_cfg = Config(tmp_path / 'target' / 'data').ensure()
    target_roots = tmp_path / 'target' / 'relocated originals'
    shutil.copytree(source.roots / 'north', target_roots)
    with db.open_db(target_cfg.db_path) as conn:
        auth.ensure_default_groups(conn)
        password = secrets.token_urlsafe(24)
        for name in ('administrator', 'unused', 'restored-owner'):
            auth.create_user(conn, name, password, is_admin=name == 'administrator')
        owner = conn.execute("SELECT id FROM users WHERE username='restored-owner'").fetchone()[0]
        root = tmp_path / 'empty library'
        root.mkdir()
        libsvc.create_library(conn, 'unrelated', str(root))
        library_id = libsvc.create_library(conn, 'Restored north', str(target_roots))
        # Deliberately reserve different local row IDs, without altering fixture bytes.
        conn.execute("INSERT INTO images(id,library_id,rel_path,filename,ext,size_bytes,mtime,imported_at,missing) VALUES(99,1,'missing.png','missing.png','png',0,0,?,1)", (db.utcnow(),))
        conn.commit()
        indexer.scan_library(conn, target_cfg, library_id)
    maps = {'libraries': {document['records']['libraries'][0]['id']: library_id},
            'users': {document['owner']: owner}}
    return source, target_cfg, target_roots, owner, maps, document


def test_round_trip_uses_known_values_and_stable_contexts(collection):
    source, cfg, root, owner, maps, document = collection
    data = json.dumps(document, ensure_ascii=False)
    assert all(word not in data for word in ('password_hash', 'secret.key', 'token_version', 'session', 'root_path'))
    assert str(source.cfg.data_dir) not in data and str(source.roots) not in data
    assert len(document['records']['images']) == 12
    assert next(row for row in document['records']['ratings'] if row['value'] == 0)['user_id'] == document['owner']
    with db.open_db(cfg.db_path) as conn:
        access = AccessContext(1, local_admin=True)
        before = metadata_snapshot(conn)
        plan = manifest.preview(conn, access, document, maps)
        assert plan['preview']['ready'], plan['preview']['conflicts']
        assert before == metadata_snapshot(conn)
        result = manifest.execute(conn, cfg, access, plan['id'])
        assert result['state'] == 'succeeded'
        iid = conn.execute("SELECT id FROM images WHERE library_id=? AND rel_path='01 zero.png'", (next(iter(maps['libraries'].values())),)).fetchone()[0]
        assert iid != source.images['n01']
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (iid, owner)).fetchone()[0] == 0
        assert conn.execute('SELECT state FROM image_workflow WHERE image_id=? AND user_id=?', (iid, owner)).fetchone()[0] == 'done'
        assert conn.execute('SELECT 1 FROM favorites WHERE image_id=? AND user_id=?', (iid, owner)).fetchone()
        assert conn.execute('SELECT body FROM comments WHERE image_id=? AND user_id=? AND body=?', (iid, owner, 'First portable comment')).fetchone()
        note = conn.execute('SELECT body,geometry FROM notes WHERE image_id=? AND body=?', (iid, 'Precise portable note')).fetchone()
        assert tuple(note) == ('Precise portable note', '{"x":0.25}')
        provenance = conn.execute('SELECT original_filename,collected_at,url,note FROM image_sources WHERE image_id=? AND origin=?', (iid, 'manual')).fetchone()
        assert tuple(provenance) == ('original name.png', '2026-10-10T04:00:00+00:00', 'https://example.com/art/42', 'Source note')
        assert conn.execute("SELECT name FROM tag_aliases WHERE name='Crimson'").fetchone()
        album = conn.execute("SELECT id,cover_image_id FROM albums WHERE name='Selections'").fetchone()
        assert album['cover_image_id'] == iid
        query = conn.execute("SELECT * FROM saved_queries WHERE name='Zero and unrated'").fetchone()
        # Give the restored account view access without importing any grants.
        gid = conn.execute("SELECT group_id FROM user_groups WHERE user_id=?", (owner,)).fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO library_grants VALUES(?,?,'view')", (gid, next(iter(maps['libraries'].values()))))
        conn.commit()
        assert [row['id'] for row in saved_queries.run(conn, AccessContext(owner), query['id'])['items']] == [iid]
        spec = json.loads(conn.execute('SELECT spec FROM play_rules WHERE owner_id=?', (owner,)).fetchone()[0])
        assert spec['source']['album_id'] == album['id'] and spec['seed'] == 42 and spec['order'] == 'shuffle'
        after = metadata_snapshot(conn)
        assert manifest.execute(conn, cfg, access, plan['id'])['state'] == 'succeeded'
        second = manifest.preview(conn, access, document, maps)
        assert second['preview']['ready'], second['preview']['conflicts']
        manifest.execute(conn, cfg, access, second['id'])
        assert metadata_snapshot(conn) == after
        exported = manifest.export(conn, AccessContext(owner, local_admin=True), list(maps['libraries'].values()))
        assert {row['id'] for row in exported['records']['images']} == {row['id'] for row in document['records']['images']}


def test_checksum_versions_unknown_fields_and_changed_target(collection):
    _, cfg, _, owner, maps, document = collection
    with db.open_db(cfg.db_path) as conn:
        access = AccessContext(1, local_admin=True)
        bad = copy.deepcopy(document)
        next(row for row in bad['records']['ratings'] if row['value'] == 0)['value'] = 9
        with pytest.raises(ServiceError, match='清单') as caught:
            manifest.preview(conn, access, bad, maps)
        assert caught.value.code == 'manifest_checksum_mismatch'
        bad['format'] = 999
        with pytest.raises(ServiceError) as caught:
            manifest.preview(conn, access, bad, maps)
        assert caught.value.code == 'manifest_version_unsupported'
        extra = copy.deepcopy(document)
        extra['records']['images'][0]['future_field'] = {'x': 'preserve via report'}
        extra['checksum'] = manifest.checksum({k:v for k,v in extra.items() if k != 'checksum'})
        plan = manifest.preview(conn, access, extra, maps)
        assert plan['preview']['unknown_fields'] == ['records.images[0].future_field']
        iid = conn.execute('SELECT id FROM images WHERE library_id=? ORDER BY id LIMIT 1', (next(iter(maps['libraries'].values())),)).fetchone()[0]
        ops.set_rating(conn, iid, owner, 6)
        with pytest.raises(ServiceError) as caught:
            manifest.execute(conn, cfg, access, plan['id'])
        assert caught.value.code == 'manifest_target_changed'
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (iid, owner)).fetchone()[0] == 6
        assert conn.execute('SELECT COUNT(*) FROM comments').fetchone()[0] == 0


def test_actual_file_change_failure_rolls_back_and_foreign_mapping_denied(collection):
    _, cfg, root, _, maps, document = collection
    with db.open_db(cfg.db_path) as conn:
        access = AccessContext(1, local_admin=True)
        plan = manifest.preview(conn, access, document, maps)
        assert plan['preview']['ready'], plan['preview']['conflicts']
        before = metadata_snapshot(conn)
        path = root / document['records']['images'][-1]['rel_path']
        path.write_bytes(b'changed after preview')
        with pytest.raises(ServiceError) as caught:
            manifest.execute(conn, cfg, access, plan['id'])
        assert caught.value.code == 'manifest_target_changed'
        assert metadata_snapshot(conn) == before
        fresh = manifest.preview(conn, access, document, maps)
        assert not fresh['preview']['ready']
        assert any(row['code'] == 'manifest_file_changed' for row in fresh['preview']['conflicts'])
        with pytest.raises(ServiceError) as caught:
            manifest.preview(conn, AccessContext(2), document, maps)
        assert caught.value.status == 403


def test_cli_manifest_export_and_http_preview_execute(collection, tmp_path):
    source, cfg, _, _, maps, _ = collection
    result = cli(source.cfg, 'manifest', 'export', '--library', 'north', '--user', 'reader')
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    file = tmp_path / 'metadata.json'
    file.write_text(result.stdout)
    preview = cli(cfg, 'manifest', 'preview', str(file), '--mappings', json.dumps(maps))
    assert preview.returncode == 0, preview.stderr
    plan = json.loads(preview.stdout)
    assert plan['preview']['ready'], plan
    denied = cli(cfg, 'manifest', 'execute', plan['id'])
    assert denied.returncode == 1 and json.loads(denied.stdout)['error']['code'] == 'confirmation_required'
    # Authenticate via an actual HTTP request, using a fixture-only random password.
    password = secrets.token_urlsafe(24)
    with db.open_db(cfg.db_path) as conn:
        auth.set_password(conn, 1, password)
    with TestClient(create_app(cfg), headers={'Origin': 'http://testserver'}) as client:
        assert client.post('/api/v1/auth/login', json={'username': 'administrator', 'password': password}).status_code == 200
        result = client.post(f"/api/v1/manifests/plans/{plan['id']}/execute", json={'confirm': True})
        assert result.status_code == 200 and result.json()['state'] == 'succeeded'
        assert client.get('/api/v1/manifests/export').status_code == 200


def test_equal_bytes_remain_distinct_and_import_has_no_permission_side_effects(collection):
    source, cfg, root, owner, maps, _ = collection
    shutil.copyfile(source.roots / 'north' / '01 zero.png', source.roots / 'north' / 'another context.png')
    shutil.copyfile(root / '01 zero.png', root / 'another context.png')
    with db.open_db(source.cfg.db_path) as conn:
        indexer.scan_library(conn, source.cfg, source.libraries['north'])
        copy_id = conn.execute("SELECT id FROM images WHERE rel_path='another context.png'").fetchone()[0]
        ops.set_rating(conn, copy_id, source.users['reader'], 9)
        document = manifest.export(conn, AccessContext(source.users['reader']), [source.libraries['north']])
    originals = [row for row in document['records']['images'] if row['rel_path'] in ('01 zero.png', 'another context.png')]
    assert len({row['sha256'] for row in originals}) == 1 and len({row['id'] for row in originals}) == 2
    with db.open_db(cfg.db_path) as conn:
        lid = next(iter(maps['libraries'].values()))
        indexer.scan_library(conn, cfg, lid)
        access = AccessContext(1, local_admin=True)
        permissions = [tuple(row) for row in conn.execute('SELECT * FROM library_grants')]
        groups = [tuple(row) for row in conn.execute('SELECT * FROM user_groups')]
        plan = manifest.preview(conn, access, document, maps)
        assert plan['preview']['ready'], plan['preview']['conflicts']
        manifest.execute(conn, cfg, access, plan['id'])
        pairs = [tuple(row) for row in conn.execute("SELECT i.rel_path,r.value FROM images i JOIN ratings r ON r.image_id=i.id WHERE r.user_id=? AND i.rel_path IN ('01 zero.png','another context.png') ORDER BY i.rel_path", (owner,))]
        assert pairs == [('01 zero.png', 0), ('another context.png', 9)]
        assert [tuple(row) for row in conn.execute('SELECT * FROM library_grants')] == permissions
        assert [tuple(row) for row in conn.execute('SELECT * FROM user_groups')] == groups
        assert not auth.get_user(conn, owner)['is_admin']


def test_name_collision_requires_explicit_mapping_and_write_failure_is_atomic(collection):
    _, cfg, _, _, maps, document = collection
    with db.open_db(cfg.db_path) as conn:
        access = AccessContext(1, local_admin=True)
        source_tag = next(row for row in document['records']['tags'] if row['name'] == '色彩:红色')
        tag_id = conn.execute('INSERT INTO tags(name,namespace,created_at) VALUES(?,?,?)', ('色彩:红色', '色彩', db.utcnow())).lastrowid
        conn.commit()
        plan = manifest.preview(conn, access, document, maps)
        assert any(item['code'] == 'manifest_name_conflict' for item in plan['preview']['conflicts'])
        explicit = {**maps, 'tags': {source_tag['id']: tag_id}}
        plan = manifest.preview(conn, access, document, explicit)
        assert plan['preview']['ready'], plan['preview']['conflicts']
        before = metadata_snapshot(conn)
        # Fail a real SQLite write late in the transaction, after albums and ratings.
        conn.execute("CREATE TRIGGER fail_manifest_note BEFORE INSERT ON notes BEGIN SELECT RAISE(ABORT,'injected disk write failure'); END")
        conn.commit()
        with pytest.raises(ServiceError):
            manifest.execute(conn, cfg, access, plan['id'])
        assert metadata_snapshot(conn) == before
        assert manifest.get_plan(conn, access, plan['id'])['state'] == 'preview'
        conn.execute('DROP TRIGGER fail_manifest_note')
        conn.commit()
        assert manifest.execute(conn, cfg, access, plan['id'])['state'] == 'succeeded'


def test_permissions_revoked_after_preview_and_missing_file_are_blocked(collection):
    _, cfg, root, _, maps, document = collection
    with db.open_db(cfg.db_path) as conn:
        access = AccessContext(1)
        plan = manifest.preview(conn, access, document, maps)
        assert plan['preview']['ready'], plan['preview']['conflicts']
        conn.execute('UPDATE users SET disabled=1 WHERE id=1')
        conn.commit()
        with pytest.raises(ServiceError) as caught:
            manifest.execute(conn, cfg, access, plan['id'])
        assert caught.value.status == 401
        assert conn.execute('SELECT COUNT(*) FROM ratings').fetchone()[0] == 0
        conn.execute('UPDATE users SET disabled=0 WHERE id=1')
        conn.commit()
        # Move only a disposable fixture file, so the recorded original is unavailable.
        path = root / '01 zero.png'
        path.rename(root / 'unindexed.png')
        plan = manifest.preview(conn, access, document, maps)
        assert not plan['preview']['ready']
        assert any(item['table'] == 'images' for item in plan['preview']['conflicts'])


def test_preview_rejects_change_and_change_back_of_shared_entity(collection):
    _, cfg, _, _, maps, document = collection
    with db.open_db(cfg.db_path) as conn:
        access = AccessContext(1, local_admin=True)
        plan = manifest.preview(conn, access, document, maps)
        lid = next(iter(maps['libraries'].values()))
        # No timestamps or per-image field versions change in this sequence.
        conn.execute("UPDATE libraries SET description='intervening edit' WHERE id=?", (lid,))
        conn.commit()
        conn.execute('UPDATE libraries SET description=NULL WHERE id=?', (lid,))
        conn.commit()
        with pytest.raises(ServiceError) as caught:
            manifest.execute(conn, cfg, access, plan['id'])
        assert caught.value.code == 'manifest_target_changed'
        assert conn.execute('SELECT COUNT(*) FROM comments').fetchone()[0] == 0


@pytest.mark.parametrize('value', [None, 42, 'without timezone'])
def test_invalid_export_time_is_refused_before_any_plan(collection, value):
    _, cfg, _, _, maps, document = collection
    changed = copy.deepcopy(document)
    changed['exported_at'] = value
    changed['checksum'] = manifest.checksum({k: v for k, v in changed.items() if k != 'checksum'})
    with db.open_db(cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        with pytest.raises(ServiceError):
            manifest.preview(conn, AccessContext(1, local_admin=True), changed, maps)
        assert conn.execute('SELECT COUNT(*) FROM manifest_plans').fetchone()[0] == 0
        assert metadata_snapshot(conn) == before


def test_public_example_contains_known_zero_rating_and_no_authentication_data():
    example = Path(__file__).resolve().parents[1] / 'docs/examples/metadata-v1.json'
    document = json.loads(example.read_text())
    records, unknown = manifest.validate(document)
    assert unknown == []
    assert records['ratings'][0]['value'] == 0
    assert records['ratings'][0]['user_id'] == document['owner']
    assert records['images'][0]['rel_path'] == 'sample.png'
    assert records['comments'][0]['body'] == 'A portable comment.'
