"""Copied roots and explicit naming preserve independently marked image work."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from picshelf import db, import_plans, indexer, libsvc, relocations, reorganize, templates
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import file_inventory
from tests.scenario_support import build_catalog, cli, metadata_snapshot


def test_rebind_copied_root_preserves_bytes_ids_and_all_organization(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    target = tmp_path / 'copied originals'
    shutil.copytree(catalog.roots / 'north', target)
    old_files, new_files = file_inventory(catalog.roots / 'north'), file_inventory(target)
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['curator'])
        before = metadata_snapshot(conn)
        ids = [tuple(row) for row in conn.execute('SELECT id,rel_path,sha256 FROM images WHERE library_id=? ORDER BY id', (catalog.libraries['north'],))]
        plan = relocations.preview(conn, access, catalog.libraries['north'], str(target))
        assert plan['preview']['ready'] and len(plan['preview']['matches']) == 12
        assert not plan['preview']['moves_files'] and not plan['preview']['indexes_extra_files']
        assert metadata_snapshot(conn) == before
        assert relocations.execute(conn, catalog.cfg, access, plan['id'])['state'] == 'succeeded'
        assert libsvc.get_library(conn, catalog.libraries['north'])['root_path'] == str(target)
        assert [tuple(row) for row in conn.execute('SELECT id,rel_path,sha256 FROM images WHERE library_id=? ORDER BY id', (catalog.libraries['north'],))] == ids
        assert metadata_snapshot(conn) == before
        assert relocations.execute(conn, catalog.cfg, access, plan['id'])['state'] == 'succeeded'
        assert indexer.scan_library(conn, catalog.cfg, catalog.libraries['north'])['added'] == 0
    assert file_inventory(catalog.roots / 'north') == old_files
    assert file_inventory(target) == new_files


def test_missing_identical_candidates_and_target_changes_block_rebinding(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    target = tmp_path / 'target'
    shutil.copytree(catalog.roots / 'north', target)
    shutil.copyfile(target / '01 zero.png', target / 'candidate-a.png')
    shutil.copyfile(target / '01 zero.png', target / 'candidate-b.png')
    (target / '01 zero.png').unlink()
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['curator'])
        plan = relocations.preview(conn, access, catalog.libraries['north'], str(target))
        assert not plan['preview']['ready']
        assert plan['preview']['missing'] == [{'image_id': catalog.images['n01'], 'rel_path': '01 zero.png'}]
        assert plan['preview']['ambiguities'][0]['candidates'] == ['candidate-a.png', 'candidate-b.png']
        with pytest.raises(ServiceError):
            relocations.execute(conn, catalog.cfg, access, plan['id'])
        shutil.copyfile(target / 'candidate-a.png', target / '01 zero.png')
        plan = relocations.preview(conn, access, catalog.libraries['north'], str(target))
        assert plan['preview']['ready']
        (target / 'later.png').write_bytes(b'not part of the preview')
        with pytest.raises(ServiceError) as caught:
            relocations.execute(conn, catalog.cfg, access, plan['id'])
        assert caught.value.code == 'relocation_target_changed'
        assert libsvc.get_library(conn, catalog.libraries['north'])['root_path'] == str(catalog.roots / 'north')


@pytest.mark.parametrize('template', ['/outside/{stem}', '../{stem}', '{stem.__class__}', '{stem!r}', '{stem:>20}', '{}', 'a//{stem}'])
def test_templates_refuse_escape_and_python_field_access(template):
    with pytest.raises(ServiceError) as caught:
        templates.render(template, 'file.png')
    assert caught.value.code == 'naming_template_invalid'


def test_template_import_fixed_paths_and_deterministic_multiple_values(tmp_path):
    catalog = build_catalog(tmp_path / 'catalog')
    source, root = tmp_path / 'incoming', tmp_path / 'managed'
    source.mkdir(); root.mkdir()
    shutil.copyfile(catalog.roots / 'north' / '01 zero.png', source / 'new.png')
    with db.open_db(catalog.cfg.db_path) as conn:
        library = libsvc.create_library(conn, 'managed', str(root), mode='managed')
        access = AccessContext(catalog.users['curator'])
        fields = {'artists': ['Zed', 'Amy'], 'tags': ['beta', 'alpha'], 'source': '../../example', 'date': '2026-10-13'}
        plan = import_plans.create_plan(conn, access, library, str(source), mode='copy',
                                       template='{artists}/{tags}/{source}/{date}/{stem}{ext}', template_fields=fields)
        # Leading dots are stripped by the existing portable component policy.
        expected = root / 'Amy + Zed/alpha + beta/_.._example/2026-10-13/new.png'
        assert Path(plan['items'][0]['dst']) == expected
        shutil.copyfile(source / 'new.png', source / 'later.png')
        assert import_plans.execute_plan(conn, catalog.cfg, access, plan['plan_id'])['state'] == 'succeeded'
        assert expected.read_bytes() == (source / 'new.png').read_bytes()
        assert len(list(root.rglob('*.png'))) == 1
        assert templates.render('{artists}/{date}/{stem}', 'missing.png') == 'unknown-artist/undated/missing.png'


def test_reorganization_recovers_file_commit_before_catalog_publish(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    north = catalog.libraries['north']
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute("UPDATE libraries SET mode='managed' WHERE id=?", (north,)); conn.commit()
        access = AccessContext(catalog.users['curator'])
        before = metadata_snapshot(conn)
        originals = {row['id']: row['sha256'] for row in conn.execute('SELECT id,sha256 FROM images WHERE library_id=?', (north,))}
        plan = reorganize.preview(conn, catalog.cfg, access, north, '{artists}/{tags}/{stem}{ext}')
        assert plan['reorganize'] and plan['stats']['move'] == 12
        assert metadata_snapshot(conn) == before
    conn.close()
    script = '''
import os,sys
from pathlib import Path
from picshelf import db,import_plans,reorganize
from picshelf.access import AccessContext
from picshelf.config import Config
cfg=Config(Path(sys.argv[1]))
conn=db.open_db(cfg.db_path)
reorganize.publish=lambda *args: os._exit(93)
import_plans.execute_plan(conn,cfg,AccessContext(int(sys.argv[2])),sys.argv[3])
'''
    failed = subprocess.run([sys.executable, '-c', script, str(catalog.cfg.data_dir), str(access.user_id), plan['plan_id']], capture_output=True, timeout=20)
    assert failed.returncode == 93
    expected = catalog.roots / 'north' / 'Jane Doe/night sky + 色彩_红色/01 zero.png'
    assert expected.is_file() and not (catalog.roots / 'north' / '01 zero.png').exists()
    with db.open_db(catalog.cfg.db_path) as conn:
        # An intervening scan must not create a new image for unpublished output.
        indexer.scan_library(conn, catalog.cfg, north)
        assert conn.execute('SELECT COUNT(*) FROM images WHERE library_id=?', (north,)).fetchone()[0] == 12
        assert conn.execute('SELECT rel_path FROM images WHERE id=?', (catalog.images['n01'],)).fetchone()[0] == '01 zero.png'
        assert import_plans.execute_plan(conn, catalog.cfg, access, plan['plan_id'], retry=True)['state'] == 'succeeded'
        assert metadata_snapshot(conn) == before
        for row in conn.execute('SELECT id,rel_path,sha256 FROM images WHERE library_id=?', (north,)):
            assert row['sha256'] == originals[row['id']]
            assert hashlib.sha256((catalog.roots / 'north' / row['rel_path']).read_bytes()).hexdigest() == originals[row['id']]
        assert conn.execute('SELECT rel_path FROM images WHERE id=?', (catalog.images['n01'],)).fetchone()[0] == 'Jane Doe/night sky + 色彩_红色/01 zero.png'
        assert import_plans.execute_plan(conn, catalog.cfg, access, plan['plan_id'], retry=True)['state'] == 'succeeded'


def test_cli_and_http_relocation_require_confirmation_and_current_permissions(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    root = tmp_path / 'copy'
    shutil.copytree(catalog.roots / 'north', root)
    response = cli(catalog.cfg, 'paths', 'preview', 'north', str(root))
    assert response.returncode == 0, response.stderr
    plan = json.loads(response.stdout)
    refused = cli(catalog.cfg, 'paths', 'execute', plan['id'])
    assert refused.returncode == 1 and json.loads(refused.stdout)['error']['code'] == 'confirmation_required'
    with TestClient(create_app(catalog.cfg), headers={'Origin': 'http://testserver'}) as client:
        assert client.post('/api/v1/auth/login', json={'username': 'reader', 'password': catalog.password}).status_code == 200
        assert client.post('/api/v1/paths/relocate', json={'library_id': catalog.libraries['north'], 'target_path': str(root)}).status_code == 403
        assert client.post('/api/v1/auth/login', json={'username': 'curator', 'password': catalog.password}).status_code == 200
        assert client.post(f"/api/v1/paths/relocate/{plan['id']}/execute", json={'confirm': True}).status_code == 200


def test_reorganization_preserves_equal_bytes_in_distinct_contexts_and_conflicts(tmp_path):
    from picshelf import ops
    catalog = build_catalog(tmp_path / 'source')
    root = catalog.roots / 'north'
    shutil.copyfile(root / '01 zero.png', root / 'other context.png')
    with db.open_db(catalog.cfg.db_path) as conn:
        north = catalog.libraries['north']
        conn.execute("UPDATE libraries SET mode='managed' WHERE id=?", (north,)); conn.commit()
        indexer.scan_library(conn, catalog.cfg, north)
        other = conn.execute("SELECT id FROM images WHERE library_id=? AND rel_path='other context.png'", (north,)).fetchone()[0]
        ops.set_rating(conn, other, catalog.users['reader'], 9)
        before = metadata_snapshot(conn)
        access = AccessContext(catalog.users['curator'])
        plan = reorganize.preview(conn, catalog.cfg, access, north, '{artists}/{stem}{ext}')
        first = Path(plan['items'][0]['dst'])
        first.parent.mkdir(parents=True)
        first.write_bytes(b'occupied after preview')
        result = import_plans.execute_plan(conn, catalog.cfg, access, plan['plan_id'])
        assert result['state'] == 'partial' and result['done']['error'] == 1
        assert first.read_bytes() == b'occupied after preview'
        assert (root / '01 zero.png').is_file()
        first.unlink()  # Remove only this deliberately created disposable conflict.
        assert import_plans.execute_plan(conn, catalog.cfg, access, plan['plan_id'], retry=True)['state'] == 'succeeded'
        assert metadata_snapshot(conn) == before
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (catalog.images['n01'], catalog.users['reader'])).fetchone()[0] == 0
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (other, catalog.users['reader'])).fetchone()[0] == 9
        assert conn.execute('SELECT COUNT(*) FROM images WHERE library_id=?', (north,)).fetchone()[0] == 13


def test_preview_metadata_changes_and_indexed_reorganization_are_refused(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    north = catalog.libraries['north']
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['curator'])
        conn.execute("UPDATE libraries SET mode='indexed' WHERE id=?", (north,)); conn.commit()
        with pytest.raises(ServiceError) as caught:
            reorganize.preview(conn, catalog.cfg, access, north, '{tags}/{stem}')
        assert caught.value.code == 'reorganization_requires_managed'
        conn.execute("UPDATE libraries SET mode='managed' WHERE id=?", (north,)); conn.commit()
        plan = reorganize.preview(conn, catalog.cfg, access, north, '{tags}/{stem}')
        inventory = file_inventory(catalog.roots / 'north')
        conn.execute("UPDATE tags SET description='New shared metadata'"); conn.commit()
        with pytest.raises(ServiceError) as caught:
            import_plans.execute_plan(conn, catalog.cfg, access, plan['plan_id'])
        assert caught.value.code == 'reorganization_target_changed'
        assert file_inventory(catalog.roots / 'north') == inventory


def test_rebinding_offline_original_and_expired_or_revoked_plans(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    target = tmp_path / 'copy'
    shutil.copytree(catalog.roots / 'north', target)
    (catalog.roots / 'north').rename(catalog.roots / 'offline-mount')
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['curator'])
        plan = relocations.preview(conn, access, catalog.libraries['north'], str(target))
        conn.execute("UPDATE relocation_plans SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (plan['id'],)); conn.commit()
        with pytest.raises(ServiceError) as caught:
            relocations.execute(conn, catalog.cfg, access, plan['id'])
        assert caught.value.code == 'relocation_plan_expired' and caught.value.status == 410
        plan = relocations.preview(conn, access, catalog.libraries['north'], str(target))
        conn.execute('UPDATE users SET disabled=1 WHERE id=?', (access.user_id,)); conn.commit()
        with pytest.raises(ServiceError) as caught:
            relocations.execute(conn, catalog.cfg, access, plan['id'])
        assert caught.value.status == 401
        conn.execute('UPDATE users SET disabled=0 WHERE id=?', (access.user_id,)); conn.commit()
        assert relocations.execute(conn, catalog.cfg, access, plan['id'])['state'] == 'succeeded'
