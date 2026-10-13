"""Frozen import scope, byte safety, cancellation, and real process recovery."""
import errno
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db, import_plans, importer, indexer, libsvc, ops
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.datasets import draw_image, file_inventory
from tests.scenario_support import cli


@dataclass
class ImportWorkspace:
    cfg: Config
    conn: object = field(repr=False)
    access: AccessContext
    source: Path
    target: Path
    library_id: int
    password: str = field(repr=False)

    def __iter__(self):
        return iter((self.cfg, self.conn, self.access, self.source, self.target, self.library_id, self.password))


@pytest.fixture
def workspace(tmp_path):
    cfg = Config(tmp_path / "data").ensure()
    conn = db.open_db(cfg.db_path)
    auth.ensure_default_groups(conn)
    password = secrets.token_urlsafe(24)
    uid = auth.create_user(conn, "planner", password, is_admin=True)
    source, target = tmp_path / "source", tmp_path / "target"
    target.mkdir()
    for number, name in enumerate(("a.png", "b.png", "c.png"), 1):
        draw_image(source / name, number)
    lid = libsvc.create_library(conn, "target", str(target))
    yield ImportWorkspace(cfg, conn, AccessContext(uid), source, target, lid, password)
    conn.close()


@pytest.mark.parametrize("mode", ["copy", "hardlink", "move"])
def test_saved_preview_scope_never_expands_or_overwrites(workspace, mode):
    cfg, conn, access, source, target, lid, _ = workspace
    before = file_inventory(source)
    plan = import_plans.create_plan(conn, access, lid, str(source), mode=mode)
    assert file_inventory(source) == before and file_inventory(target) == {}
    draw_image(source / "new.png", 10)
    draw_image(source / "a.png", 20)
    draw_image(target / "b.png", 30)
    changed_source = (source / "a.png").read_bytes()
    occupied = (target / "b.png").read_bytes()
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["state"] == "partial" and result["done"]["error"] == 2
    assert result["done"][mode] == 1
    assert not (target / "new.png").exists() and not (target / "a.png").exists()
    assert (target / "b.png").read_bytes() == occupied
    assert (source / "a.png").read_bytes() == changed_source
    assert file_inventory(target)["c.png"]["sha256"] == before["c.png"]["sha256"]
    assert (source / "c.png").exists() == (mode != "move")
    after = file_inventory(target)
    assert import_plans.execute_plan(conn, cfg, access, plan["plan_id"]) == result
    assert file_inventory(target) == after


@pytest.mark.parametrize("mode", ["copy", "hardlink", "move"])
def test_restart_and_duplicate_execution_preserve_bytes_and_identity(workspace, mode):
    cfg, conn, access, source, target, lid, _ = workspace
    before = file_inventory(source)
    plan = import_plans.create_plan(conn, access, lid, str(source), mode=mode)
    fresh = db.open_db(cfg.db_path)
    try:
        result = import_plans.execute_plan(fresh, cfg, access, plan["plan_id"])
    finally:
        fresh.close()
    assert result["done"][mode] == 3 and result["state"] == "succeeded"
    after = file_inventory(target)
    for name in before:
        assert after[name]["sha256"] == before[name]["sha256"]
        if mode != "move":
            assert (after[name]["inode"] == before[name]["inode"]) == (mode == "hardlink")
    assert file_inventory(source) == ({} if mode == "move" else before)
    iid = conn.execute("SELECT id FROM images WHERE library_id=? ORDER BY id", (lid,)).fetchone()[0]
    ops.set_rating(conn, iid, access.user_id, 10)
    assert import_plans.execute_plan(conn, cfg, access, plan["plan_id"]) == result
    assert file_inventory(target) == after
    assert conn.execute("SELECT value FROM ratings WHERE image_id=?", (iid,)).fetchone()[0] == 10


@pytest.mark.parametrize("mode,step", [
    ("copy", "writing"), ("copy", "ready"),
    ("hardlink", "writing"), ("hardlink", "ready"),
    ("move", "writing"), ("move", "ready"), ("move", "source_removed"),
])
def test_process_interruption_resumes_without_losing_or_duplicating_files(workspace, mode, step):
    cfg, conn, access, source, target, lid, _ = workspace
    expected = {name: row["sha256"] for name, row in file_inventory(source).items()}
    plan = import_plans.create_plan(conn, access, lid, str(source), mode=mode)
    script = """
import os, sys
from pathlib import Path
from picshelf import db, import_plans
from picshelf.access import AccessContext
from picshelf.config import Config
cfg = Config(Path(sys.argv[1])); c=db.open_db(cfg.db_path)
save=import_plans._record_item
def interrupt(c, pid, position, state, **kwargs):
    if position == 1 and sys.argv[4] == 'source_removed' and state == 'succeeded':
        os._exit(73)
    save(c, pid, position, state, **kwargs)
    if position == 1 and state == sys.argv[4]: os._exit(73)
import_plans._record_item=interrupt
import_plans.execute_plan(c,cfg,AccessContext(int(sys.argv[3])),sys.argv[2])
"""
    result = subprocess.run([sys.executable, "-c", script, str(cfg.data_dir), plan["plan_id"],
                             str(access.user_id), step], capture_output=True, timeout=20)
    assert result.returncode == 73, result.stderr
    first_identity = (target / "a.png").stat().st_ino
    scan = indexer.scan_library(conn, cfg, lid)
    assert conn.execute("SELECT COUNT(*) FROM images WHERE filename='b.png'").fetchone()[0] == 0
    assert scan["added"] == 1
    resumed = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert resumed["state"] == "succeeded" and resumed["done"][mode] == 3
    assert (target / "a.png").stat().st_ino == first_identity
    assert {name: row["sha256"] for name, row in file_inventory(target).items()} == expected
    assert conn.execute("SELECT COUNT(*) FROM images WHERE library_id=?", (lid,)).fetchone()[0] == 3
    remaining = {name: row["sha256"] for name, row in file_inventory(source).items()}
    assert remaining == ({} if mode == "move" else expected)


def test_disk_error_partial_file_can_resume_but_external_edits_are_preserved(workspace, monkeypatch):
    cfg, conn, access, source, target, lid, _ = workspace
    original = (source / "a.png").read_bytes()
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="copy")
    copy = import_plans.shutil.copyfileobj
    failed = False

    def disk_full(src, dst, length):
        nonlocal failed
        if not failed:
            failed = True
            dst.write(src.read(80))
            raise OSError(errno.ENOSPC, "Injected full disk")
        return copy(src, dst, length)

    monkeypatch.setattr(import_plans.shutil, "copyfileobj", disk_full)
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["done"]["copy"] == 2 and result["done"]["error"] == 1
    assert (source / "a.png").read_bytes() == original
    assert (target / "a.png").read_bytes() == original[:80]
    assert conn.execute("SELECT COUNT(*) FROM images WHERE filename='a.png'").fetchone()[0] == 0
    conn.execute("UPDATE import_plans SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (plan["plan_id"],))
    conn.commit()
    restored = import_plans.execute_plan(conn, cfg, access, plan["plan_id"], retry=True)
    assert restored["done"]["copy"] == 3 and (target / "a.png").read_bytes() == original
    assert restored["resumed_after_expiry"] is True


def test_changed_owned_partial_is_a_conflict_and_is_never_cleaned_up(workspace, monkeypatch):
    cfg, conn, access, source, target, lid, _ = workspace
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="move")
    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "Injected cross-device link")
    monkeypatch.setattr(import_plans.os, "link", cross_device)
    with monkeypatch.context() as patch:
        def partial(src, dst, length):
            dst.write(src.read(40))
            raise OSError(errno.ENOSPC, "Injected full disk")
        patch.setattr(import_plans.shutil, "copyfileobj", partial)
        result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
        assert result["done"]["error"] == 3
    (target / "a.png").write_bytes(b"A later user edit")
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"], retry=True)
    assert result["done"]["move"] == 2 and result["done"]["error"] == 1
    assert (target / "a.png").read_bytes() == b"A later user edit"
    assert (source / "a.png").exists()


def test_move_keeps_source_when_it_changes_during_copy(workspace, monkeypatch):
    cfg, conn, access, source, target, lid, _ = workspace
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="move")
    copy = import_plans.shutil.copyfileobj
    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "Injected cross-device link")
    monkeypatch.setattr(import_plans.os, "link", cross_device)

    def changing_copy(src, dst, length):
        copy(src, dst, length)
        if Path(src.name).name == "a.png":
            Path(src.name).write_bytes(b"New source content")

    monkeypatch.setattr(import_plans.shutil, "copyfileobj", changing_copy)
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["done"]["error"] == 1 and result["done"]["move"] == 2
    assert (source / "a.png").read_bytes() == b"New source content"


def test_hardlink_fallback_records_actual_copy(workspace, monkeypatch):
    cfg, conn, access, source, target, lid, _ = workspace
    plan = import_plans.create_plan(conn, access, lid, str(source))
    def unsupported(*args, **kwargs):
        raise OSError(errno.EXDEV, "Injected cross-device link")
    monkeypatch.setattr(import_plans.os, "link", unsupported)
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["done"]["copy"] == 3 and result["done"]["hardlink"] == 0
    assert (source / "a.png").stat().st_ino != (target / "a.png").stat().st_ino


def test_cancel_preserves_completed_items_and_retry_only_continues_pending(workspace, monkeypatch):
    cfg, conn, access, source, target, lid, _ = workspace
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="copy")
    save = import_plans._record_item
    with monkeypatch.context() as patch:
        def cancel(c, pid, position, state, **kwargs):
            save(c, pid, position, state, **kwargs)
            if position == 0 and state == "succeeded":
                import_plans.cancel_plan(conn, access, pid)
        patch.setattr(import_plans, "_record_item", cancel)
        result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["state"] == "cancelled" and result["done"]["copy"] == 1 and result["done"]["pending"] == 2
    inode = (target / "a.png").stat().st_ino
    assert import_plans.execute_plan(conn, cfg, access, plan["plan_id"])["state"] == "cancelled"
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"], retry=True)
    assert result["state"] == "succeeded" and result["done"]["copy"] == 3
    assert (target / "a.png").stat().st_ino == inode


def test_api_pagination_expiry_options_and_ownership(workspace):
    cfg, conn, access, source, target, lid, password = workspace
    for n in range(4, 305):
        draw_image(source / f"extra-{n:03}.png", n)
    with TestClient(create_app(cfg), headers={"Origin": "http://testserver"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "planner", "password": password}).status_code == 200
        plan = client.post("/api/v1/import/plan", json={"source_dir": str(source), "library_id": lid, "mode": "copy"}).json()
        assert plan["items_total"] == 304 and len(plan["items"]) == 300
        page = client.get(f"/api/v1/import/plans/{plan['plan_id']}?offset=300").json()
        assert len(page["items"]) == 4 and page["next_offset"] is None
        assert {row["position"] for row in plan["items"] + page["items"]} == set(range(304))
        endpoint = f"/api/v1/import/plans/{plan['plan_id']}/execute"
        assert client.post(endpoint, json={"confirm": True, "mode": "move"}).status_code == 422
        assert client.post(endpoint, json={"confirm": False}).status_code == 400
        conn.execute("UPDATE import_plans SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (plan["plan_id"],))
        conn.commit()
        expired = client.post(endpoint, json={"confirm": True})
        assert expired.status_code == 409 and expired.json()["error"]["code"] == "plan_expired"
        auth.create_user(conn, "other", password)
        conn.execute("UPDATE library_grants SET access='manage'")
        conn.commit()
        assert client.post("/api/v1/auth/login", json={"username": "other", "password": password}).status_code == 200
        assert client.get(f"/api/v1/import/plans/{plan['plan_id']}").status_code == 404
    assert file_inventory(target) == {}


def test_api_rechecks_revoked_grants_before_execution(workspace):
    cfg, conn, access, source, target, lid, password = workspace
    from picshelf import source_access
    source_access.register(conn, access, str(source))
    uid = auth.create_user(conn, "manager", password)
    conn.execute("UPDATE library_grants SET access='manage'")
    conn.commit()
    with TestClient(create_app(cfg), headers={"Origin": "http://testserver"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "manager", "password": password}).status_code == 200
        plan = client.post("/api/v1/import/plan", json={"source_dir": str(source), "library_id": lid}).json()
        conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
        conn.commit()
        response = client.post(f"/api/v1/import/plans/{plan['plan_id']}/execute", json={"confirm": True})
        assert response.status_code == 403
    assert file_inventory(target) == {}


def test_revocation_during_execution_stops_at_next_file(workspace, monkeypatch):
    cfg, conn, access, source, target, lid, password = workspace
    from picshelf import source_access
    source_access.register(conn, access, str(source))
    uid = auth.create_user(conn, "manager", password)
    conn.execute("UPDATE library_grants SET access='manage'")
    conn.commit()
    manager = AccessContext(uid)
    plan = import_plans.create_plan(conn, manager, lid, str(source), mode="copy")
    save = import_plans._record_item
    def revoke(c, pid, position, state, **kwargs):
        save(c, pid, position, state, **kwargs)
        if position == 0 and state == "succeeded":
            c.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
            c.commit()
    monkeypatch.setattr(import_plans, "_record_item", revoke)
    with pytest.raises(ServiceError) as caught:
        import_plans.execute_plan(conn, cfg, manager, plan["plan_id"])
    assert caught.value.code == "permission_denied"
    assert set(file_inventory(target)) == {"a.png"}
    assert set(file_inventory(source)) == {"a.png", "b.png", "c.png"}
    with pytest.raises(ServiceError):
        import_plans.get_plan(conn, manager, plan["plan_id"])


def test_unrecorded_destination_after_crash_is_left_for_explicit_resolution(workspace):
    cfg, conn, access, source, target, lid, _ = workspace
    original = (source / "a.png").read_bytes()
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="move")
    # A crash between exclusive creation and recording its identity cannot prove
    # ownership. This models that boundary without guessing who owns the file.
    conn.execute("UPDATE import_plan_items SET state='creating' WHERE plan_id=? AND position=0", (plan["plan_id"],))
    conn.commit()
    (target / "a.png").write_bytes(b"Unattributed destination")
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["state"] == "partial" and result["done"]["error"] == 1
    assert (target / "a.png").read_bytes() == b"Unattributed destination"
    assert (source / "a.png").read_bytes() == original


def test_cli_preview_then_execute_uses_same_saved_scope(workspace):
    cfg, conn, access, source, target, lid, _ = workspace
    preview = cli(cfg, "import", str(source), "target", "--mode", "copy", "--json")
    assert preview.returncode == 0, preview.stderr
    plan_id = json.loads(preview.stdout)["plan_id"]
    draw_image(source / "later.png", 30)
    refused = cli(cfg, "import-execute", plan_id, "--json")
    assert refused.returncode != 0 and file_inventory(target) == {}
    executed = cli(cfg, "import-execute", plan_id, "--yes", "--json")
    assert executed.returncode == 0, executed.stderr
    assert json.loads(executed.stdout)["done"]["copy"] == 3
    status = cli(cfg, "import-status", plan_id, "--offset", "1", "--limit", "1")
    assert status.returncode == 0 and json.loads(status.stdout)["items"][0]["position"] == 1
    assert not (target / "later.png").exists()


def test_physical_root_alias_shares_lock_but_another_library_can_scan(workspace, tmp_path):
    cfg, conn, access, source, target, lid, _ = workspace
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    # A historical catalog may contain aliases. New registrations reject them,
    # but old aliases must still share the physical-root lock after upgrade.
    alias_id = conn.execute("INSERT INTO libraries(name,root_path,created_at) VALUES(?,?,?)",
                            ("alias", str(alias), db.utcnow())).lastrowid
    conn.commit()
    other = tmp_path / "other"
    draw_image(other / "one.png", 8)
    other_id = libsvc.create_library(conn, "other", str(other))
    ready = tmp_path / "locked"
    script = """
import sys
from pathlib import Path
from picshelf.config import Config
from picshelf.coordination import library_write_lock
with library_write_lock(Config(Path(sys.argv[1])),Path(sys.argv[2])):
    Path(sys.argv[3]).touch()
    sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(cfg.data_dir), str(target), str(ready)], stdin=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        with pytest.raises(ServiceError) as caught:
            indexer.scan_library(conn, cfg, alias_id)
        assert caught.value.code == "library_busy" and caught.value.retryable
        result = cli(cfg, "scan", "target")
        assert result.returncode != 0 and "library_busy" in result.stderr
        with TestClient(create_app(cfg), headers={"Origin": "http://testserver"}) as client:
            assert client.post("/api/v1/auth/login", json={"username": "planner", "password": workspace.password}).status_code == 200
            response = client.post(f"/api/v1/libraries/{lid}/scan")
            assert response.status_code == 409 and response.json()["error"]["code"] == "library_busy"
        assert indexer.scan_library(conn, cfg, other_id)["added"] == 1
    finally:
        process.stdin.close()
        process.wait(timeout=10)
    assert indexer.scan_library(conn, cfg, lid)["errors"] == []


def test_portable_names_are_explicit_in_preview_without_changing_sources(workspace):
    cfg, conn, access, source, target, lid, _ = workspace
    draw_image(source / "CON.png", 40)
    draw_image(source / "folder?" / "UPPER.png", 41)
    draw_image(target / "upper.PNG", 50)
    draw_image(source / "upper.png", 42)
    before = file_inventory(source)
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="copy")
    destinations = {Path(row["src"]).name: Path(row["dst"]).relative_to(target).as_posix()
                    for row in plan["items"] if row["dst"]}
    assert destinations["CON.png"] == "_CON.png"
    assert destinations["UPPER.png"] == "folder_/UPPER.png"
    assert destinations["upper.png"] == "upper-1.png"
    assert file_inventory(source) == before
    assert len(importer.sanitize_filename("图" * 100 + ".png").encode()) <= 220
    assert importer.sanitize_filename("e\u0301.png") == "é.png"


def test_decode_failure_is_partial_success_and_preserves_original_bytes(workspace):
    cfg, conn, access, source, target, lid, _ = workspace
    (source / "broken.png").write_bytes(b"Not a decodable image")
    plan = import_plans.create_plan(conn, access, lid, str(source), mode="copy")
    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    assert result["state"] == "partial" and result["done"]["copy"] == 4
    # added counts file records; an undecodable file is retained with a scan
    # error and no derivative, distinct from a missing file.
    assert result["scan_error_count"] == 1 and result["scan"]["added"] == 4
    assert conn.execute("SELECT COUNT(*) FROM images WHERE thumb_rel IS NOT NULL").fetchone()[0] == 3
    broken = conn.execute("SELECT missing,thumb_rel FROM images WHERE filename='broken.png'").fetchone()
    assert tuple(broken) == (0, None)
    assert (target / "broken.png").read_bytes() == (source / "broken.png").read_bytes()
