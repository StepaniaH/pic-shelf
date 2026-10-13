"""Relationships preserve file-specific work and do not reveal hidden versions."""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db, indexer, libsvc, manifest, works
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.datasets import file_inventory
from tests.scenario_support import build_catalog, cli, metadata_snapshot


def test_manual_links_preference_and_correction_leave_original_work_unchanged(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    files = file_inventory(catalog.roots)
    with db.open_db(catalog.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        access = AccessContext(catalog.users['curator'])
        members = [{'image_id': catalog.images['n01'], 'role': 'original'}, {'image_id': catalog.images['n04'], 'role': 'resize'}]
        work = works.create(conn, access, 'A hand-picked work', members, catalog.images['n04'])
        assert work['view_image_id'] == catalog.images['n04']
        assert [image['id'] for image in work['images']] == [catalog.images['n01'], catalog.images['n04']]
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (catalog.images['n01'], catalog.users['reader'])).fetchone()[0] == 0
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (catalog.images['n04'], catalog.users['reader'])).fetchone()[0] == 10
        with pytest.raises(ServiceError) as caught:
            works.create(conn, access, 'Another work', members)
        assert caught.value.code == 'image_already_linked'
        assert conn.execute('SELECT COUNT(*) FROM works').fetchone()[0] == 1
        assert works.unlink(conn, access, work['id'], catalog.images['n04'])['preferred_image_id'] is None
        works.link(conn, access, work['id'], [{'image_id': catalog.images['n04'], 'role': 'revision'}])
        assert works.prefer(conn, access, work['id'], catalog.images['n01'])['preferred_image_id'] == catalog.images['n01']
        assert metadata_snapshot(conn) == before
        assert conn.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 24
    assert file_inventory(catalog.roots) == files


def test_hidden_members_and_preferences_are_filtered_with_live_permissions(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['curator'])
        work = works.create(conn, access, 'Shared work', [{'image_id': catalog.images['n01'], 'role': 'original'}, {'image_id': catalog.images['s01'], 'role': 'crop'}], catalog.images['s01'])
        reader = AccessContext(catalog.users['reader'])
        visible = works.get(conn, reader, work['id'])
        assert [item['id'] for item in visible['images']] == [catalog.images['n01']]
        assert visible['preferred_image_id'] is None and visible['view_image_id'] == catalog.images['n01']
        with pytest.raises(ServiceError):
            works.unlink(conn, reader, work['id'], catalog.images['n01'])
        with pytest.raises(ServiceError) as caught:
            works.get(conn, AccessContext(catalog.users['outsider']), work['id'])
        assert caught.value.status == 404
        document = manifest.export(conn, reader)
        assert len(document['records']['work_images']) == 1
        assert document['records']['works'][0]['preferred_image_id'] is None


def test_split_manifest_retains_pending_version_then_resolves_stable_identity(tmp_path):
    source = build_catalog(tmp_path / 'source')
    with db.open_db(source.cfg.db_path) as conn:
        actor = AccessContext(source.users['curator'])
        original = works.create(conn, actor, 'Portable variants', [{'image_id': source.images['n01'], 'role': 'original'}, {'image_id': source.images['s01'], 'role': 'crop'}], source.images['s01'])
        north = manifest.export(conn, actor, [source.libraries['north']])
        south = manifest.export(conn, actor, [source.libraries['south']])
        stable_work = north['records']['works'][0]['id']
    target = Config(tmp_path / 'target/data').ensure()
    with db.open_db(target.db_path) as conn:
        auth.ensure_default_groups(conn)
        owner = auth.create_user(conn, 'restored', source.password, is_admin=True)
        maps = {'users': {north['owner']: owner}, 'libraries': {}}
        for document, name in ((north, 'north'),):
            root = tmp_path / 'target' / name
            shutil.copytree(source.roots / name, root)
            lid = libsvc.create_library(conn, 'Copy ' + name, str(root))
            indexer.scan_library(conn, target, lid)
            maps['libraries'][document['records']['libraries'][0]['id']] = lid
        # Simulate independently copied scopes, not a global hash match.
        access = AccessContext(owner)
        north_maps = {'users': maps['users'], 'libraries': {north['records']['libraries'][0]['id']: maps['libraries'][north['records']['libraries'][0]['id']]}}
        first = manifest.preview(conn, access, north, north_maps)
        assert first['preview']['ready'], first['preview']['conflicts']
        assert first['preview']['pending_relations'] == 1
        manifest.execute(conn, target, access, first['id'])
        work_id = conn.execute('SELECT id FROM works').fetchone()[0]
        assert [item['role'] for item in works.get(conn, access, work_id)['images']] == ['original']
        reexport = manifest.export(conn, access, [next(iter(north_maps['libraries'].values()))])
        assert reexport['records']['works'][0]['id'] == stable_work
        assert len(reexport['records']['work_images']) == 2
        pending = works.pending(conn, access, work_id)
        assert len(pending['items']) == 1 and pending['items'][0]['preferred'] == 1
        root = tmp_path / 'target/south'
        shutil.copytree(source.roots / 'south', root)
        south_library = libsvc.create_library(conn, 'Copy south', str(root))
        indexer.scan_library(conn, target, south_library)
        maps['libraries'][south['records']['libraries'][0]['id']] = south_library
        south_maps = {'users': maps['users'], 'libraries': {south['records']['libraries'][0]['id']: maps['libraries'][south['records']['libraries'][0]['id']]}}
        second = manifest.preview(conn, access, south, south_maps)
        assert second['preview']['ready'], second['preview']['conflicts']
        manifest.execute(conn, target, access, second['id'])
        work = works.get(conn, access, work_id)
        assert [item['role'] for item in work['images']] == ['original', 'crop']
        assert work['preferred_image_id'] == next(item['id'] for item in work['images'] if item['role'] == 'crop')
        assert conn.execute('SELECT COUNT(*) FROM pending_work_images').fetchone()[0] == 0
        again = manifest.preview(conn, access, north, north_maps)
        assert again['preview']['ready'], again['preview']['conflicts']
        manifest.execute(conn, target, access, again['id'])
        assert conn.execute('SELECT COUNT(*) FROM work_images').fetchone()[0] == 2
        known_original = next(item['id'] for item in work['images'] if item['role'] == 'original')
        works.prefer(conn, access, work_id, known_original)
        stale = manifest.preview(conn, access, north, north_maps)
        assert not stale['preview']['ready']
        assert works.get(conn, access, work_id)['preferred_image_id'] == known_original


def test_http_and_cli_manual_relationships_and_hidden_version_metadata(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    members = [{'image_id': catalog.images['n01'], 'role': 'original'}, {'image_id': catalog.images['s01'], 'role': 'crop'}]
    created = cli(catalog.cfg, 'works', 'create', 'CLI work', '--members', json.dumps(members), '--preferred', str(catalog.images['s01']))
    assert created.returncode == 0, created.stderr
    work = json.loads(created.stdout)
    with TestClient(create_app(catalog.cfg), headers={'Origin': 'http://testserver'}) as client:
        assert client.post('/api/v1/auth/login', json={'username': 'reader', 'password': catalog.password}).status_code == 200
        value = client.get(f"/api/v1/images/{catalog.images['n01']}").json()['work']
        assert [image['id'] for image in value['images']] == [catalog.images['n01']]
        assert value['preferred_image_id'] is None
        denied = client.put(f"/api/v1/works/{work['id']}/preferred", json={'image_id': catalog.images['s01']})
        assert denied.status_code == 403
        assert client.post('/api/v1/auth/login', json={'username': 'curator', 'password': catalog.password}).status_code == 200
        assert client.put(f"/api/v1/works/{work['id']}/preferred", json={'image_id': catalog.images['n01']}).json()['view_image_id'] == catalog.images['n01']
        url = f"/api/v1/works/{work['id']}/members/{catalog.images['s01']}"
        assert client.request('DELETE', url, json={'confirm': False}).status_code == 400
        assert client.request('DELETE', url, json={'confirm': True}).status_code == 200
