"""Browser uploads use owned staging, frozen previews and normal import recovery."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db, import_plans, jobs, libsvc, source_access, uploads
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.conftest import make_image


@pytest.fixture
def incoming(cfg, conn, admin, alice, tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    lid = libsvc.create_library(conn, 'Uploads', str(target), mode='managed')
    conn.execute("UPDATE library_grants SET access='manage' WHERE library_id=? AND group_id IN "
                 "(SELECT group_id FROM user_groups WHERE user_id=?)", (lid, alice['id']))
    conn.commit()
    client = TestClient(create_app(cfg), headers={'Origin': 'http://testserver'})
    client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'alice-pass-123'})
    image = make_image(tmp_path / 'original.png').read_bytes()
    return client, lid, target, image


def start(client, lid):
    response = client.post('/api/v1/import/uploads', json={'library_id': lid})
    assert response.status_code == 200, response.text
    return response.json()['id']


def send(client, upload_id, path, content, **kwargs):
    return client.put(f'/api/v1/import/uploads/{upload_id}/file', params={'path': path},
                      content=content, **kwargs)


def test_upload_preserves_folders_deduplicates_and_keeps_original(incoming, conn, cfg):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, 'Holiday/nested/a.png', image).status_code == 200
    assert send(client, uid, 'Holiday/duplicate.png', image).status_code == 200
    response = client.post(f'/api/v1/import/uploads/{uid}/plan', json={})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan['browser_upload'] and plan['stats']['skip'] == 1
    assert plan['stats']['hardlink'] + plan['stats']['copy'] == 1
    assert not list(target.rglob('*.png'))
    assert send(client, uid, 'late.png', image).status_code == 409
    endpoint = f'/api/v1/import/plans/{plan["plan_id"]}/execute'
    assert client.post(endpoint, json={}).status_code == 400
    done = client.post(endpoint, json={'confirm': True})
    assert done.status_code == 200, done.text
    assert done.json()['state'] == 'succeeded'
    assert len(list(target.rglob('*.png'))) == 1
    assert next(target.rglob('*.png')).read_bytes() == image
    assert (target.parent / 'original.png').read_bytes() == image
    assert not Path(plan['source']).exists()
    assert client.get(f'/api/v1/import/plans/{plan["plan_id"]}').status_code == 200
    assert client.post(endpoint, json={'confirm': True}).json() == done.json()


@pytest.mark.parametrize('path', ['../outside.png', '/outside.png', 'a/../../outside.png',
                                 'C:/outside.png', 'a\\outside.png', 'a//b.png', 'notes.txt'])
def test_upload_rejects_unsafe_paths_and_nonimages(incoming, path):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, path, image).status_code == 400
    assert not list(target.rglob('*'))


def test_upload_ownership_grants_conflicts_and_limits(incoming, conn, cfg, admin, monkeypatch):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, 'one.png', image).status_code == 200
    assert send(client, uid, 'one.png', b'changed').status_code == 409
    monkeypatch.setattr(uploads, 'MAX_BYTES', len(image))
    assert send(client, uid, 'two.png', image).status_code == 413
    other = TestClient(create_app(cfg), headers={'Origin': 'http://testserver'})
    other.post('/api/v1/auth/login', json={'username':'admin', 'password':'admin-pass-123'})
    assert send(other, uid, 'two.png', image).status_code == 404
    conn.execute("UPDATE library_grants SET access='view' WHERE library_id=?", (lid,))
    conn.commit()
    assert send(client, uid, 'two.png', image).status_code == 403
    assert client.post('/api/v1/import/uploads', json={'library_id':lid}).status_code == 403
    assert client.post(f'/api/v1/import/uploads/{uid}/plan', json={}).status_code == 403


def test_oversized_or_incomplete_stream_is_removed(incoming, conn, monkeypatch):
    client, lid, target, image = incoming
    uid = start(client, lid)
    for declared in (len(image) - 1, len(image) + 1):
        response = send(client, uid, 'broken.png', image, headers={'Content-Length':str(declared)})
        assert response.status_code in (400, 413), response.text
        row = conn.execute('SELECT * FROM import_uploads WHERE id=?', (uid,)).fetchone()
        assert row['bytes'] == 0 and row['file_count'] == 0
        assert not (Path(row['source']) / 'broken.png').exists()
    monkeypatch.setattr(uploads, 'MAX_FILE_BYTES', len(image) - 1)
    assert send(client, uid, 'huge.png', image).status_code == 413


def test_discard_and_expiry_release_only_unstarted_staging(incoming, conn, cfg):
    client, lid, target, image = incoming
    uid = start(client, lid)
    send(client, uid, 'one.png', image)
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={}).json()
    assert client.post(f'/api/v1/import/uploads/{uid}/discard').status_code == 200
    assert not Path(plan['source']).exists()
    assert (target.parent / 'original.png').read_bytes() == image
    uid = start(client, lid)
    send(client, uid, 'expired.png', image)
    conn.execute("UPDATE import_uploads SET expires_at='2000-01-01' WHERE id=?", (uid,))
    conn.commit()
    assert client.post(f'/api/v1/import/uploads/{uid}/plan', json={}).status_code == 409
    uploads.cleanup(conn, cfg)
    row = conn.execute('SELECT * FROM import_uploads WHERE id=?', (uid,)).fetchone()
    assert row['state'] == 'expired' and not Path(row['source']).exists()


def test_cleanup_rechecks_a_plan_started_after_candidate_selection(incoming, conn, cfg, alice, monkeypatch):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, 'one.png', image).status_code == 200
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={}).json()
    conn.execute("UPDATE import_uploads SET expires_at='2000-01-01' WHERE id=?", (uid,))
    conn.commit()
    access = AccessContext(alice['id'])
    original_lock = uploads.operation_lock
    started = False

    def interrupt():
        raise jobs.WorkerStopped()

    @contextmanager
    def start_before_cleanup_locks(*args, **kwargs):
        nonlocal started
        if not started:
            started = True
            with pytest.raises(jobs.WorkerStopped):
                import_plans.execute_plan(conn, cfg, access, plan['plan_id'], check=interrupt)
        with original_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(uploads, 'operation_lock', start_before_cleanup_locks)
    uploads.cleanup(conn, cfg)
    assert (Path(plan['source']) / 'one.png').read_bytes() == image
    assert conn.execute('SELECT state FROM import_uploads WHERE id=?', (uid,)).fetchone()[0] == 'sealed'
    result = import_plans.execute_plan(conn, cfg, access, plan['plan_id'], retry=True)
    assert result['state'] == 'succeeded'
    assert (target / 'one.png').read_bytes() == image
    assert not Path(plan['source']).exists()


@pytest.mark.parametrize('action', ['cleanup', 'discard'])
def test_start_commit_serializes_with_staging_removal(incoming, conn, cfg, alice, action):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, 'one.png', image).status_code == 200
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={}).json()
    conn.execute("UPDATE import_uploads SET expires_at='2000-01-01' WHERE id=?", (uid,))
    conn.commit()
    access = AccessContext(alice['id'])
    outcomes = []

    def remove_staging():
        with db.open_db(cfg.db_path) as other:
            try:
                if action == 'cleanup':
                    uploads.cleanup(other, cfg)
                else:
                    uploads.discard(other, cfg, access, uid)
                return None
            except ServiceError as error:
                return error.code

    def before_start_write(sql):
        if sql.startswith("UPDATE import_plans SET state='running'"):
            # A separate connection/thread attempts deletion before started_at is visible.
            with ThreadPoolExecutor(max_workers=1) as executor:
                outcomes.append(executor.submit(remove_staging).result(timeout=5))
            outcomes.append(Path(plan['source']).exists())

    def interrupt():
        raise jobs.WorkerStopped()

    conn.set_trace_callback(before_start_write)
    try:
        with pytest.raises(jobs.WorkerStopped):
            import_plans.execute_plan(conn, cfg, access, plan['plan_id'], check=interrupt)
    finally:
        conn.set_trace_callback(None)
    assert outcomes == [None if action == 'cleanup' else 'operation_busy', True]
    assert (Path(plan['source']) / 'one.png').read_bytes() == image
    with pytest.raises(ServiceError, match='无法接收所选文件') as denied:
        uploads.discard(conn, cfg, access, uid)
    assert denied.value.code == 'upload_closed'
    result = import_plans.execute_plan(conn, cfg, access, plan['plan_id'], retry=True)
    assert result['state'] == 'succeeded'
    assert (target / 'one.png').read_bytes() == image


def test_uploaded_job_retains_staging_for_retry_after_worker_stop(incoming, conn, cfg, alice):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, 'one.png', image).status_code == 200
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={}).json()
    access = AccessContext(alice['id'])
    job = jobs.submit(conn, access, 'import', lid, plan_id=plan['plan_id'])

    def stop_after_start():
        return conn.execute('SELECT started_at FROM import_plans WHERE id=?', (plan['plan_id'],)).fetchone()[0] is not None

    assert jobs.run_job(cfg, job['id'], should_stop=stop_after_start)
    assert jobs.get_job(conn, access, job['id'])['state'] == 'interrupted'
    conn.execute("UPDATE import_uploads SET expires_at='2000-01-01' WHERE id=?", (uid,))
    conn.commit()
    uploads.cleanup(conn, cfg)
    assert (Path(plan['source']) / 'one.png').read_bytes() == image
    jobs.retry(conn, access, job['id'])
    assert jobs.run_job(cfg, job['id'])
    assert jobs.get_job(conn, access, job['id'])['state'] == 'succeeded'
    assert (target / 'one.png').read_bytes() == image
    assert not Path(plan['source']).exists()


def test_server_picker_obeys_source_roots_and_library_permissions(incoming, conn, admin, alice, tmp_path):
    client, lid, target, image = incoming
    assert client.get('/api/v1/import/sources', params={'library_id':lid}).json()['roots'] == []
    source = tmp_path / 'approved'
    (source / 'nested').mkdir(parents=True)
    (source / 'escape').symlink_to(target, target_is_directory=True)
    source_access.register(conn, AccessContext(admin['id']), str(source))
    response = client.get('/api/v1/import/sources', params={'library_id':lid, 'path':str(source)})
    assert response.status_code == 200, response.text
    assert response.json()['directories'] == [{'name':'nested', 'path':str(source / 'nested')}]
    assert response.json()['parent'] is None
    for forbidden in (tmp_path, source / 'escape'):
        assert client.get('/api/v1/import/sources', params={'library_id':lid, 'path':str(forbidden)}).status_code == 403
    source_access.revoke(conn, AccessContext(admin['id']), conn.execute('SELECT id FROM import_source_roots').fetchone()[0])
    assert client.get('/api/v1/import/sources', params={'library_id':lid, 'path':str(source)}).status_code == 403


def test_stream_route_keeps_origin_protection(incoming):
    client, lid, target, image = incoming
    uid = start(client, lid)
    assert send(client, uid, 'image.png', image, headers={'Origin':'https://other.example'}).status_code == 403


def test_upload_stream_can_exceed_json_budget_and_account_slots_can_be_released(incoming):
    client, lid, target, image = incoming
    ids = [start(client, lid) for _ in range(4)]
    assert client.post('/api/v1/import/uploads', json={'library_id':lid}).status_code == 429
    payload = image + b'\x00' * (1024 * 1024)
    assert send(client, ids[0], 'large.png', payload).status_code == 200
    assert client.post(f'/api/v1/import/uploads/{ids[0]}/discard').status_code == 200
    assert start(client, lid)


def test_managed_library_allocates_storage_without_a_path(cfg, conn, admin, incoming):
    client, lid, target, image = incoming
    assert client.post('/api/v1/libraries', json={'name':'Forbidden', 'mode':'managed'}).status_code == 403
    client.post('/api/v1/auth/login', json={'username':'admin', 'password':'admin-pass-123'})
    response = client.post('/api/v1/libraries', json={'name':'Automatic', 'mode':'managed'})
    assert response.status_code == 200, response.text
    root = Path(response.json()['root_path'])
    assert root.is_dir() and root.parent == cfg.data_dir.resolve() / 'libraries'
    assert client.post('/api/v1/libraries', json={'name':'Existing', 'mode':'indexed'}).status_code == 400
    assert client.post('/api/v1/libraries', json={'name':'Automatic', 'mode':'managed'}).status_code == 409
    assert list(root.parent.iterdir()) == [root]


def test_import_into_album_includes_existing_duplicates_and_rechecks_edit_grants(incoming, conn, cfg):
    client, lid, target, image = incoming
    album = client.post('/api/v1/albums', json={'name':'Trip', 'library_id':lid}).json()
    uid = start(client, lid)
    send(client, uid, 'trip.png', image)
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={'album_id':album['id']}).json()
    result = client.post(f'/api/v1/import/plans/{plan["plan_id"]}/execute', json={'confirm':True})
    assert result.status_code == 200, result.text
    assert result.json()['album_added'] == 1
    assert client.get(f'/api/v1/albums/{album["id"]}/images').json()['total'] == 1
    second_album = client.post('/api/v1/albums', json={'name':'Existing', 'library_id':lid}).json()
    uid = start(client, lid)
    send(client, uid, 'same-content.png', image)
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={'album_id':second_album['id']}).json()
    assert plan['stats']['skip'] == 1
    result = client.post(f'/api/v1/import/plans/{plan["plan_id"]}/execute', json={'confirm':True})
    assert result.json()['album_added'] == 1
    assert client.get(f'/api/v1/albums/{second_album["id"]}/images').json()['total'] == 1
    uid = start(client, lid)
    send(client, uid, 'revoked.png', image)
    plan = client.post(f'/api/v1/import/uploads/{uid}/plan', json={'album_id':album['id']}).json()
    conn.execute("UPDATE groups SET capabilities='[\"view\"]' WHERE name='members'")
    conn.commit()
    assert client.post(f'/api/v1/import/plans/{plan["plan_id"]}/execute', json={'confirm':True}).status_code == 403


def test_album_target_cannot_cross_libraries_or_use_folder_album(incoming, conn, cfg, admin, tmp_path):
    from picshelf import indexer
    client, lid, target, image = incoming
    make_image(target / 'folder' / 'one.png')
    indexer.scan_library(conn, cfg, lid)
    folder_id = conn.execute("SELECT id FROM albums WHERE library_id=? AND kind='folder'", (lid,)).fetchone()[0]
    uid = start(client, lid)
    send(client, uid, 'one.png', image)
    assert client.post(f'/api/v1/import/uploads/{uid}/plan', json={'album_id':folder_id}).status_code == 400
    second = tmp_path / 'other-library'; second.mkdir()
    other_id = libsvc.create_library(conn, 'Other', str(second), mode='managed')
    client.post('/api/v1/auth/login', json={'username':'admin','password':'admin-pass-123'})
    other = client.post('/api/v1/albums', json={'name':'Other', 'library_id':other_id}).json()
    admin_upload = start(client, lid)
    send(client, admin_upload, 'one.png', image)
    assert client.post(f'/api/v1/import/uploads/{admin_upload}/plan', json={'album_id':other['id']}).status_code == 400
