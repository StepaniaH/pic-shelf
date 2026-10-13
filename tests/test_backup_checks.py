"""Recovery and diagnostics are judged against fixture bytes and user work."""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, backups, checks, db
from picshelf.api import create_app
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.scenario_support import build_catalog, cli, metadata_snapshot


def rewrite(source, target, edit):
    with zipfile.ZipFile(source) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    edit(members)
    with zipfile.ZipFile(target, 'w') as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_archive_restores_wal_metadata_derivatives_and_invalidates_sessions(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    archive = tmp_path / 'catalog.zip'
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute('PRAGMA wal_autocheckpoint=0')
        conn.execute("UPDATE notes SET body='Preserved WAL note' WHERE image_id=?", (catalog.images['n01'],))
        conn.commit()
        before = metadata_snapshot(conn)
        result = backups.export(catalog.cfg, archive, with_thumbs=True)
        assert result['format'] == 2
    meta = backups.inspect(archive)
    assert meta['validated'] and not meta['legacy_without_checksums']
    assert len(meta['required_originals']) == 24
    assert meta['contains'] == dict(database=True, derivatives=True, originals=False, signing_key=False, configuration=False)
    with zipfile.ZipFile(archive) as zf:
        assert 'secret.key' not in zf.namelist()
        assert all(name.startswith('media/') or name in ('meta.json', 'picshelf.db') for name in zf.namelist())
    target = Config(tmp_path / 'restored').ensure()
    restored = backups.restore(target, archive)
    assert restored['sessions_invalidated']
    with db.open_db(target.db_path) as conn:
        assert metadata_snapshot(conn) == before
        assert conn.execute('SELECT body FROM notes WHERE image_id=?', (catalog.images['n01'],)).fetchone()[0] == 'Preserved WAL note'
        for row in conn.execute('SELECT thumb_rel,preview_rel FROM images'):
            assert all((target.media_dir / value).is_file() for value in row)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        password = secrets.token_urlsafe(24)
        auth.set_password(conn, catalog.users['curator'], password)
    conn.close()
    with TestClient(create_app(target)) as client:
        token = client.post('/api/v1/auth/login', json={'username': 'curator', 'password': password}).json()['token']
        image = catalog.images['n01']
        for kind in ('thumb', 'preview', 'original'):
            response = client.get(f'/api/v1/media/{kind}/{image}', headers={'Authorization': 'Bearer ' + token})
            assert response.status_code == 200
            if kind == 'original':
                assert hashlib.sha256(response.content).hexdigest() == next(row['sha256'] for row in meta['required_originals'] if row['library_id'] == catalog.libraries['north'] and row['rel_path'] == '01 zero.png')


def test_tampered_archive_duplicate_members_and_paths_never_change_target(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    archive = tmp_path / 'catalog.zip'
    backups.export(catalog.cfg, archive)
    bad = tmp_path / 'bad.zip'
    def corrupt(members):
        data = bytearray(members['picshelf.db'])
        data[-1] ^= 1
        members['picshelf.db'] = bytes(data)
    rewrite(archive, bad, corrupt)
    with db.open_db(catalog.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        with pytest.raises(ServiceError) as caught:
            backups.restore(catalog.cfg, bad, force=True)
        assert caught.value.code == 'backup_checksum_mismatch'
        assert metadata_snapshot(conn) == before
    rewrite(archive, bad, lambda members: members.update({'media/thumbs/../../escape.webp': b'bad'}))
    with pytest.raises(ServiceError) as caught:
        backups.inspect(bad)
    assert caught.value.code == 'backup_path_invalid'
    with zipfile.ZipFile(bad, 'a') as zf:
        with pytest.warns(UserWarning):
            zf.writestr('picshelf.db', b'duplicate')
    with pytest.raises(ServiceError):
        backups.inspect(bad)
    assert not list(catalog.cfg.data_dir.glob('picshelf.db.bak-*'))


def test_active_connections_hold_independent_restore_leases(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    archive = tmp_path / 'catalog.zip'
    backups.export(catalog.cfg, archive)
    first = db.open_db(catalog.cfg.db_path)
    second = db.open_db(catalog.cfg.db_path)
    first.close()
    # Closing the first connection must not release the second connection's lease.
    rejected = cli(catalog.cfg, 'import-backup', str(archive), '--force')
    assert rejected.returncode == 1 and 'operation_busy' in rejected.stderr
    assert second.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 24
    second.close()
    accepted = cli(catalog.cfg, 'import-backup', str(archive), '--force')
    assert accepted.returncode == 0, accepted.stderr
    with sqlite3.connect(next(catalog.cfg.data_dir.glob('picshelf.db.bak-*'))) as saved:
        assert saved.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 24


def test_integrity_check_reports_real_missing_changed_files_without_writes(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    with db.open_db(catalog.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        rel = conn.execute('SELECT thumb_rel FROM images WHERE id=?', (catalog.images['n01'],)).fetchone()[0]
        conn.execute('UPDATE albums SET cover_image_id=999999 WHERE kind=\'manual\'')
        conn.commit()
    conn.close()
    (catalog.cfg.media_dir / rel).unlink()
    path = catalog.roots / 'north' / '01 zero.png'
    path.write_bytes(b'independently changed bytes')
    (catalog.roots / 'south' / '01 zero.png').unlink()
    report = checks.check(catalog.cfg, full=True)
    assert report['read_only'] and report['full_hash_check']
    assert report['counts']['original_hash_mismatch'] == 1
    assert report['counts']['original_missing'] == 1
    assert report['counts']['derivative_missing'] == 1
    assert report['counts']['album_cover_invalid'] >= 1
    result = cli(catalog.cfg, 'check', '--full')
    assert result.returncode == 1 and json.loads(result.stdout)['counts'] == report['counts']
    with db.open_db(catalog.cfg.db_path) as conn:
        after = metadata_snapshot(conn)
        # The fixture-only invalid cover is the sole deliberate metadata change.
        after['manual_albums'] = before['manual_albums']
        assert after == before
        assert conn.execute('SELECT missing FROM images WHERE id=?', (catalog.images['s01'],)).fetchone()[0] == 0


def test_wrong_passphrase_and_declared_schema_mismatch_are_rejected(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    archive = tmp_path / 'catalog.zip'
    backups.export(catalog.cfg, archive)
    bad = tmp_path / 'schema.zip'
    def change_schema(members):
        meta = json.loads(members['meta.json'])
        meta['schema'] = 1
        members['meta.json'] = json.dumps(meta).encode()
    rewrite(archive, bad, change_schema)
    with pytest.raises(ServiceError) as caught:
        backups.inspect(bad)
    assert caught.value.code == 'backup_schema_mismatch'
    encrypted = tmp_path / 'encrypted.fernet'
    phrase = secrets.token_urlsafe(24)
    backups.export(catalog.cfg, encrypted, passphrase=phrase)
    with pytest.raises(ServiceError) as caught:
        backups.restore(catalog.cfg, encrypted, force=True, passphrase='wrong fixture input')
    assert caught.value.code == 'backup_passphrase_invalid'
    assert backups.inspect(encrypted, passphrase=phrase)['validated']


def test_restore_refuses_previous_bearer_session_with_same_signing_key(tmp_path):
    catalog = build_catalog(tmp_path / 'source')
    with TestClient(create_app(catalog.cfg)) as client:
        token = client.post('/api/v1/auth/login', json={'username': 'curator', 'password': catalog.password}).json()['token']
        assert client.get('/api/v1/images', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    archive = tmp_path / 'catalog.zip'
    backups.export(catalog.cfg, archive)
    key = catalog.cfg.secret_path.read_bytes()
    backups.restore(catalog.cfg, archive, force=True)
    assert catalog.cfg.secret_path.read_bytes() == key
    with TestClient(create_app(catalog.cfg)) as client:
        assert client.get('/api/v1/images', headers={'Authorization': 'Bearer ' + token}).status_code == 401
        assert client.post('/api/v1/auth/login', json={'username': 'curator', 'password': catalog.password}).status_code == 200


def test_legacy_backup_upgrades_on_stage_and_preserves_known_work(tmp_path):
    from tests.test_migrations import old_database, observe
    source = old_database(tmp_path / 'old.db')
    before = observe(source)
    archive = tmp_path / 'legacy.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.write(source, 'picshelf.db')
    assert backups.inspect(archive)['legacy_without_checksums']
    cfg = Config(tmp_path / 'restored').ensure()
    assert backups.restore(cfg, archive)['restored']
    after = observe(cfg.db_path)
    for field in before.keys() - {'schema', 'version'}:
        assert after[field] == before[field]
    with db.open_db(cfg.db_path) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
