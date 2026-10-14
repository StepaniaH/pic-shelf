"""Task lifecycle expectations from files, permissions, and real worker exits."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db, import_plans, jobs, libsvc
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.coordination import library_write_lock, operation_lock, worker_slot
from picshelf.errors import ServiceError
from tests.datasets import draw_image, file_inventory
from tests.scenario_support import build_catalog, cli, metadata_snapshot


@pytest.fixture
def catalog(tmp_path):
    return build_catalog(tmp_path)


def worker(cfg, *args):
    return subprocess.run([sys.executable, "-m", "picshelf.worker", "--data", str(cfg.data_dir), *args],
                          capture_output=True, text=True, timeout=30)


def test_import_progress_has_linear_writes_and_retry_counters():
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE jobs(id TEXT PRIMARY KEY,total INTEGER,completed INTEGER,succeeded INTEGER,failed INTEGER,updated_at TEXT);
        CREATE TABLE job_items(job_id TEXT,item_key TEXT,state TEXT,error_code TEXT,detail TEXT,PRIMARY KEY(job_id,item_key));
        CREATE TABLE import_plan_items(plan_id TEXT,position INTEGER,state TEXT,error_code TEXT,detail TEXT,PRIMARY KEY(plan_id,position));
        INSERT INTO jobs VALUES('job',NULL,0,0,0,0);
    ''')
    count = 200
    conn.executemany("INSERT INTO import_plan_items VALUES('plan',?,'pending',NULL,NULL)", [(n,) for n in range(count)])
    conn.commit()
    jobs._import_progress(conn, 'job', 'plan')
    baseline = conn.total_changes
    for n in range(count):
        conn.execute("UPDATE import_plan_items SET state=? WHERE position=?", ('failed' if n == 0 else 'succeeded', n))
        conn.commit()
        jobs._import_progress(conn, 'job', 'plan', n)
    assert conn.total_changes - baseline <= count * 4
    assert tuple(conn.execute('SELECT total,completed,succeeded,failed FROM jobs').fetchone()) == (count, count, count - 1, 1)
    conn.execute("UPDATE import_plan_items SET state='succeeded' WHERE position=0"); conn.commit()
    for _ in range(2):
        jobs._import_progress(conn, 'job', 'plan', 0)
    assert tuple(conn.execute('SELECT total,completed,succeeded,failed FROM jobs').fetchone()) == (count, count, count, 0)
    conn.close()


@pytest.mark.parametrize('outdated_count', [0, 1])
def test_unreadable_subtree_does_not_expand_outdated_rebuild(catalog, monkeypatch, outdated_count):
    from picshelf import indexer
    lid = catalog.libraries['north']
    access = AccessContext(catalog.users['curator'])
    with db.open_db(catalog.cfg.db_path) as conn:
        if outdated_count:
            conn.execute('UPDATE images SET phash_version=0 WHERE id=?', (catalog.images['n01'],)); conn.commit()
        job = jobs.submit(conn, access, 'rebuild', lid, outdated_only=True)

        def unreadable(root, onerror=None):
            onerror(PermissionError(13, 'unreadable fixture', str(root)))
            yield from ()

        monkeypatch.setattr(indexer, '_iter_image_files', unreadable)
        assert jobs.run_job(catalog.cfg, job['id'])
        result = jobs.get_job(conn, access, job['id'])
        assert result['state'] == ('partial' if outdated_count else 'succeeded')
        assert result['total'] == outdated_count
        assert result['failed'] == outdated_count
        assert result['items_total'] == outdated_count
        assert conn.execute('SELECT COUNT(*) FROM images WHERE library_id=? AND scan_error_code IS NOT NULL', (lid,)).fetchone()[0] == outdated_count


@contextmanager
def held_process(cfg, lock, value=""):
    script = """
import sys
from pathlib import Path
from picshelf.config import Config
from picshelf.coordination import library_write_lock, operation_lock, worker_slot
cfg = Config(Path(sys.argv[1]))
lock = sys.argv[2]
with (library_write_lock(cfg, Path(sys.argv[3])) if lock == 'library' else
      worker_slot(cfg) if lock == 'slot' else operation_lock(cfg, sys.argv[3])):
    print('ready', flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(cfg.data_dir), lock, str(value)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        yield process
    finally:
        process.communicate("stop\n", timeout=10)
        assert process.returncode == 0


def test_duplicate_submission_worker_and_retry_do_not_rewrite_finished_images(catalog):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    try:
        before = metadata_snapshot(conn)
        job = jobs.submit(conn, access, "rebuild", catalog.libraries["north"], request_key="reviewed-north")
        assert job["state"] == "queued" and job["completed"] == 0
        assert jobs.submit(conn, access, "rebuild", catalog.libraries["north"], request_key="reviewed-north")["id"] == job["id"]
        with pytest.raises(ServiceError) as caught:
            jobs.submit(conn, access, "scan", catalog.libraries["south"], request_key="reviewed-north")
        assert caught.value.code == "request_conflict"
        result = worker(catalog.cfg, "--once")
        assert result.returncode == 0, result.stderr
        done = jobs.get_job(conn, access, job["id"])
        assert (done["state"], done["total"], done["completed"], done["succeeded"], done["failed"]) == ("succeeded", 12, 12, 12, 0)
        assert metadata_snapshot(conn) == before
        media = file_inventory(catalog.cfg.media_dir)
        assert jobs.retry(conn, access, job["id"])["state"] == "succeeded"
        assert worker(catalog.cfg, "--once").returncode == 0
        assert file_inventory(catalog.cfg.media_dir) == media
    finally:
        conn.close()


def test_busy_library_does_not_block_another_and_restore_is_exclusive(catalog, tmp_path):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    try:
        export = tmp_path / "backup.zip"
        assert cli(catalog.cfg, "export", str(export)).returncode == 0
        north = jobs.submit(conn, access, "scan", catalog.libraries["north"])
        south = jobs.submit(conn, access, "scan", catalog.libraries["south"])
        before = metadata_snapshot(conn)
        with held_process(catalog.cfg, "library", catalog.roots / "north"):
            assert worker(catalog.cfg, "--once").returncode == 0
            assert jobs.get_job(conn, access, north["id"])["state"] == "queued"
            assert jobs.get_job(conn, access, south["id"])["state"] == "succeeded"
            restored = cli(catalog.cfg, "import-backup", str(export), "--force")
            assert restored.returncode != 0 and "operation_busy" in restored.stderr
        assert metadata_snapshot(conn) == before
        assert worker(catalog.cfg, "--once").returncode == 0
        assert jobs.get_job(conn, access, north["id"])["state"] == "succeeded"
    finally:
        conn.close()


def test_worker_capacity_is_shared_across_processes(catalog):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    try:
        job = jobs.submit(conn, access, "scan", catalog.libraries["north"])
        with held_process(catalog.cfg, "slot"), held_process(catalog.cfg, "slot"):
            assert worker(catalog.cfg, "--once").returncode == 0
            assert jobs.get_job(conn, access, job["id"])["state"] == "queued"
        assert worker(catalog.cfg, "--once").returncode == 0
        assert jobs.get_job(conn, access, job["id"])["state"] == "succeeded"
    finally:
        conn.close()


def test_partial_rebuild_retry_only_touches_failed_path(catalog):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    root = catalog.roots / "north"
    damaged = next(root.rglob("*.png"))
    original = damaged.read_bytes()
    try:
        damaged.write_bytes(b"unreadable test image")
        job = jobs.submit(conn, access, "rebuild", catalog.libraries["north"])
        assert worker(catalog.cfg, "--once").returncode == 0
        failed = jobs.get_job(conn, access, job["id"])
        assert (failed["state"], failed["completed"], failed["failed"]) == ("partial", 12, 1)
        assert [item["item_key"] for item in failed["items"] if item["state"] == "failed"] == [damaged.relative_to(root).as_posix()]
        image_id = conn.execute("SELECT id FROM images WHERE library_id=? AND rel_path=?",
                                (catalog.libraries["north"], damaged.relative_to(root).as_posix())).fetchone()[0]
        unaffected = {row[0]: row[1] for row in conn.execute("SELECT id,thumb_rel FROM images WHERE id!=?", (image_id,))}
        media_before = {iid: (catalog.cfg.media_dir / path).stat().st_mtime_ns for iid, path in unaffected.items()}
        damaged.write_bytes(original)
        draw_image(root / "later.png", 91)
        jobs.retry(conn, access, job["id"])
        assert worker(catalog.cfg, "--once").returncode == 0
        done = jobs.get_job(conn, access, job["id"])
        assert (done["state"], done["succeeded"], done["failed"]) == ("succeeded", 12, 0)
        assert not conn.execute("SELECT id FROM images WHERE filename='later.png'").fetchone()
        assert {iid: (catalog.cfg.media_dir / path).stat().st_mtime_ns for iid, path in unaffected.items()} == media_before
    finally:
        conn.close()


def test_cancel_queued_then_resume_and_pagination(catalog):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    try:
        before = file_inventory(catalog.cfg.media_dir)
        job = jobs.submit(conn, access, "rebuild", catalog.libraries["north"])
        assert jobs.cancel(conn, access, job["id"])["state"] == "cancelled"
        assert worker(catalog.cfg, "--once").returncode == 0
        assert file_inventory(catalog.cfg.media_dir) == before
        jobs.retry(conn, access, job["id"])
        assert worker(catalog.cfg, "--once").returncode == 0
        pages = [jobs.get_job(conn, access, job["id"], offset=n, limit=5) for n in (0, 5, 10)]
        keys = [item["item_key"] for page in pages for item in page["items"]]
        assert len(keys) == len(set(keys)) == 12
        assert pages[-1]["next_offset"] is None
    finally:
        conn.close()


def test_revocation_disallows_execution_status_and_list(catalog):
    conn = db.open_db(catalog.cfg.db_path)
    reader = AccessContext(catalog.users["reader"])
    admin = AccessContext(catalog.users["curator"])
    lid = catalog.libraries["north"]
    try:
        conn.execute("UPDATE library_grants SET access='manage' WHERE library_id=?", (lid,))
        conn.commit()
        job = jobs.submit(conn, reader, "rebuild", lid)
        before = file_inventory(catalog.cfg.media_dir)
        conn.execute("DELETE FROM library_grants WHERE library_id=?", (lid,))
        conn.commit()
        assert worker(catalog.cfg, "--once").returncode == 0
        assert file_inventory(catalog.cfg.media_dir) == before
        failed = jobs.get_job(conn, admin, job["id"])
        assert failed["state"] == "failed" and failed["error"]["error"]["code"] == "permission_denied"
        with pytest.raises(ServiceError): jobs.get_job(conn, reader, job["id"])
        assert jobs.list_jobs(conn, reader)["items"] == []
        outsider = AccessContext(catalog.users["outsider"])
        with pytest.raises(ServiceError) as caught: jobs.get_job(conn, outsider, job["id"])
        assert caught.value.code == "job_not_found"
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ["copy", "hardlink", "move"])
@pytest.mark.parametrize("boundary", ["writing", "succeeded"])
def test_crashed_worker_is_detected_and_import_resumes_once(catalog, tmp_path, mode, boundary):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    source, target = tmp_path / "incoming", tmp_path / "target"
    target.mkdir()
    for n in range(3): draw_image(source / f"{n}.png", n + 60)
    original = file_inventory(source)
    try:
        lid = libsvc.create_library(conn, "target", str(target))
        plan = import_plans.create_plan(conn, access, lid, str(source), mode=mode)
        job = jobs.submit(conn, access, "import", lid, plan_id=plan["plan_id"])
        script = """
import os, sys
from pathlib import Path
from picshelf import jobs, import_plans
from picshelf.config import Config
original = import_plans._record_item
def interrupted(*args, **kwargs):
    original(*args, **kwargs)
    position = 1 if sys.argv[3] == 'writing' else 0
    if args[2] == position and args[3] == sys.argv[3]: os._exit(73)
import_plans._record_item = interrupted
jobs.run_job(Config(Path(sys.argv[1])), sys.argv[2])
"""
        result = subprocess.run([sys.executable, "-c", script, str(catalog.cfg.data_dir), job["id"], boundary], capture_output=True, timeout=20)
        assert result.returncode == 73
        interrupted = jobs.get_job(conn, access, job["id"])
        assert interrupted["state"] == "interrupted"
        assert interrupted["succeeded"] == 1
        first_inode = (target / "0.png").stat().st_ino
        jobs.retry(conn, access, job["id"])
        assert worker(catalog.cfg, "--once").returncode == 0
        done = jobs.get_job(conn, access, job["id"])
        assert (done["state"], done["completed"], done["succeeded"]) == ("succeeded", 3, 3)
        assert (target / "0.png").stat().st_ino == first_inode
        assert {key: row["sha256"] for key, row in file_inventory(target).items()} == {key: row["sha256"] for key, row in original.items()}
        assert bool(file_inventory(source)) == (mode != "move")
    finally:
        conn.close()


def test_api_worker_lifespan_and_cli_background_contract(catalog):
    app = create_app(catalog.cfg)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "curator", "password": catalog.password}).status_code == 200
        queued = client.post(f"/api/v1/libraries/{catalog.libraries['north']}/scan-jobs", json={"request_key": "api-north"})
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/jobs/{job_id}").json()
            if status["state"] in jobs.TERMINAL: break
            time.sleep(.05)
        assert status["state"] == "succeeded"
        assert client.get("/api/v1/images").json()["total"] == 24
        assert client.post(f"/api/v1/libraries/{catalog.libraries['north']}/scan-jobs", json={"request_key": "api-north"}).json()["id"] == job_id
    assert not app.state.workers.processes
    queued = cli(catalog.cfg, "scan", "south", "--background", "--json")
    assert queued.returncode == 0, queued.stderr
    assert json.loads(queued.stdout)["state"] == "queued"
    job_id = json.loads(queued.stdout)["id"]
    assert cli(catalog.cfg, "worker", "--once").returncode == 0
    status = cli(catalog.cfg, "job", "status", job_id)
    assert status.returncode == 0 and json.loads(status.stdout)["state"] == "succeeded"


@pytest.mark.parametrize("action", ["cancel", "revoke", "disable", "stop"])
def test_running_scan_stops_at_file_boundary_and_remains_browsable(catalog, action):
    conn = db.open_db(catalog.cfg.db_path)
    reader, admin = AccessContext(catalog.users["reader"]), AccessContext(catalog.users["curator"])
    lid = catalog.libraries["north"]
    conn.execute("UPDATE library_grants SET access='manage' WHERE library_id=?", (lid,))
    conn.commit()
    before = metadata_snapshot(conn)
    thumbnails = {r[0]: (catalog.cfg.media_dir / r[1]).stat().st_mtime_ns
                  for r in conn.execute("SELECT id,thumb_rel FROM images")}
    job = jobs.submit(conn, reader, "rebuild", lid)
    script = """
import sys
from pathlib import Path
from picshelf import jobs
from picshelf.config import Config
original = jobs._update_item
paused = False
def checkpoint(conn, job_id, *args, **kwargs):
    global paused
    original(conn, job_id, *args, **kwargs)
    if not paused and conn.execute('SELECT completed FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 1:
        paused = True
        print('file-completed', flush=True)
        sys.stdin.readline()
jobs._update_item = checkpoint
stop_path = Path(sys.argv[1]) / 'stop-worker'
jobs.run_job(Config(Path(sys.argv[1])), sys.argv[2], should_stop=stop_path.exists)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(catalog.cfg.data_dir), job["id"]],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "file-completed"
        running = jobs.get_job(conn, admin, job["id"])
        assert running["state"] == "running" and running["completed"] == 1
        # Actual application handlers must still query and create playback while
        # the other process owns the library and its job lock.
        client = TestClient(create_app(catalog.cfg), headers={"Origin": "http://testserver"})
        try:
            client.post("/api/v1/auth/login", json={"username": "curator", "password": catalog.password})
            assert client.get("/api/v1/images").json()["total"] == 24
            assert client.post("/api/v1/play/sessions", json={"source": {"library_id": lid}}).json()["total"] == 12
            if action == "cancel":
                response = client.post(f"/api/v1/jobs/{job['id']}/cancel")
                assert response.status_code == 200 and response.json()["state"] == "cancelling"
            elif action == "revoke":
                conn.execute("DELETE FROM library_grants WHERE library_id=?", (lid,))
                conn.commit()
            elif action == "disable":
                conn.execute("UPDATE users SET disabled=1 WHERE id=?", (reader.user_id,))
                conn.commit()
            else:
                (catalog.cfg.data_dir / "stop-worker").touch()
        finally:
            client.close()
        _, err = process.communicate("continue\n", timeout=20)
        assert process.returncode == 0, err
        done = jobs.get_job(conn, admin, job["id"])
        assert done["state"] == {"cancel": "cancelled", "revoke": "failed", "disable": "failed", "stop": "interrupted"}[action]
        assert done["succeeded"] == 1
        changed = [r[0] for r in conn.execute("SELECT id,thumb_rel FROM images")
                   if (catalog.cfg.media_dir / r[1]).stat().st_mtime_ns != thumbnails[r[0]]]
        assert len(changed) == 1
        after = metadata_snapshot(conn)
        for field in before.keys() - {"library_grants"}:
            assert after[field] == before[field]
        if action in ("revoke", "disable"):
            with pytest.raises(ServiceError): jobs.get_job(conn, reader, job["id"])
        if action == "cancel":
            jobs.retry(conn, admin, job["id"])
            assert worker(catalog.cfg, "--once").returncode == 0
            assert jobs.get_job(conn, admin, job["id"])["succeeded"] == 12
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        conn.close()


def test_concurrent_idempotency_keys_create_one_job(catalog):
    script = """
import sys
from pathlib import Path
from picshelf import db, jobs
from picshelf.access import AccessContext
c=db.open_db(Path(sys.argv[1]))
try: print(jobs.submit(c,AccessContext(int(sys.argv[2])),'scan',int(sys.argv[3]),request_key='same-request')['id'])
finally: c.close()
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(catalog.cfg.db_path),
                                    str(catalog.users["curator"]), str(catalog.libraries["north"])],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    try:
        ids = []
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
            ids.append(out.strip())
        assert len(set(ids)) == 1
        with db.open_db(catalog.cfg.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_exclusive_maintenance_blocks_queue_writes_and_workers(catalog):
    conn = db.open_db(catalog.cfg.db_path)
    access = AccessContext(catalog.users["curator"])
    try:
        job = jobs.submit(conn, access, "scan", catalog.libraries["north"])
        before = tuple(conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
        conn.close()
        with held_process(catalog.cfg, "operation", "catalog"):
            refused = worker(catalog.cfg, "--once")
            assert refused.returncode == 1 and 'operation_busy' in refused.stderr
            with pytest.raises(ServiceError) as caught:
                db.open_db(catalog.cfg.db_path)
            assert caught.value.code == "operation_busy"
        conn = db.open_db(catalog.cfg.db_path)
        assert tuple(conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()) == before
    finally:
        conn.close()


def test_existing_local_scan_works_before_account_creation(cfg, conn, library):
    result = cli(cfg, "scan", "testlib")
    assert result.returncode == 0, result.stderr
    assert "新增 4" in result.stdout
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 4


def test_legacy_sync_scan_respects_decode_slots_and_keeps_metadata(catalog):
    from picshelf import indexer
    from fastapi.testclient import TestClient
    from picshelf.api import create_app
    from picshelf.coordination import worker_slot
    conn = db.open_db(catalog.cfg.db_path)
    before = metadata_snapshot(conn)
    with held_process(catalog.cfg, 'slot'), held_process(catalog.cfg, 'slot'):
        with TestClient(create_app(catalog.cfg), headers={'Origin':'http://testserver'}) as client:
            assert client.post('/api/v1/auth/login',json={'username':'curator','password':catalog.password}).status_code == 200
            response = client.post(f"/api/v1/libraries/{catalog.libraries['north']}/scan")
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'workers_busy'
            assert response.json()['error']['retryable']
        assert metadata_snapshot(conn) == before
    # A nested scan must reuse its worker slot while another process holds the other.
    with held_process(catalog.cfg, 'slot'), worker_slot(catalog.cfg):
        result = indexer.scan_library(conn, catalog.cfg, catalog.libraries['north'])
        assert result['unchanged'] == 12 and not result['errors']
    assert metadata_snapshot(conn) == before
    conn.close()
