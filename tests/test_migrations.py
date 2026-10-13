"""Upgrade safety checked against a frozen released schema and raw SQLite."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from picshelf import db
from picshelf.api import create_app
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.scenario_support import cli, metadata_snapshot

V1 = Path(__file__).parent / "fixtures/schema_v1.sql"


def old_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(V1.read_text())
    conn.executescript("""
        INSERT INTO users VALUES(7,'legacy','invalid-fixture-hash','Legacy',1,0,'2026-10-01T00:00:00+00:00');
        INSERT INTO groups(id,name,created_at) VALUES(13,'legacy-group','2026-10-01T00:00:00+00:00');
        INSERT INTO user_groups VALUES(7,13);
        INSERT INTO libraries VALUES(19,'legacy','images',NULL,'2026-10-01T00:00:00+00:00');
        INSERT INTO library_grants VALUES(13,19,'manage');
        INSERT INTO images(id,library_id,rel_path,filename,ext,size_bytes,mtime,missing,imported_at)
          VALUES(31,19,'one.png','one.png','png',123,1,0,'2026-10-01T00:00:00+00:00'),
                (47,19,'missing.png','missing.png','png',456,2,1,'2026-10-02T00:00:00+00:00');
        INSERT INTO ratings VALUES(31,7,0,'2026-10-01T00:00:00+00:00');
        INSERT INTO favorites VALUES(31,7,'2026-10-01T00:00:00+00:00');
        INSERT INTO tags(id,name,created_at) VALUES(53,'色彩:蓝色','2026-10-01T00:00:00+00:00');
        INSERT INTO image_tags VALUES(31,53,7,'2026-10-01T00:00:00+00:00');
        INSERT INTO artists(id,name,created_at) VALUES(59,'An Artist','2026-10-01T00:00:00+00:00');
        INSERT INTO image_artists VALUES(31,59);
        INSERT INTO comments(id,image_id,user_id,body,created_at) VALUES(61,31,7,'A comment','2026-10-01T00:00:00+00:00');
        INSERT INTO notes(id,image_id,user_id,body,geometry,created_at) VALUES(67,31,7,'A note','{"x":0.25}','2026-10-01T00:00:00+00:00');
        INSERT INTO albums(id,library_id,name,kind,cover_image_id,created_at) VALUES(71,19,'Saved','manual',31,'2026-10-01T00:00:00+00:00');
        INSERT INTO album_images VALUES(71,31,0,'2026-10-01T00:00:00+00:00'),(71,47,1,'2026-10-01T00:00:00+00:00');
    """)
    conn.close()
    return path


def observe(path):
    with sqlite3.connect(path) as conn:
        result = metadata_snapshot(conn)
        result["images"] = list(conn.execute("SELECT id,library_id,rel_path,filename,missing FROM images ORDER BY id"))
        result["version"] = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        result["schema"] = list(conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name"))
        return result


def add_migrations(monkeypatch, *scripts):
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1 + len(scripts))
    monkeypatch.setattr(db, "MIGRATIONS", {1: V1.read_text().split("INSERT INTO meta")[0],
                                           **dict(enumerate(scripts, 2))})


def test_upgrade_and_reopen_preserve_all_released_metadata(tmp_path, monkeypatch):
    path = old_database(tmp_path / "old.db")
    before = observe(path)
    add_migrations(monkeypatch,
                   "ALTER TABLE images ADD COLUMN fixture_state TEXT NOT NULL DEFAULT 'unreviewed';",
                   "CREATE INDEX fixture_state_index ON images(fixture_state);")
    for _ in range(2):
        conn = db.open_db(path)
        assert db.schema_version(conn) == 3
        assert list(map(tuple, conn.execute("SELECT id,fixture_state FROM images ORDER BY id"))) == [
            (31, "unreviewed"), (47, "unreviewed")]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.close()
    after = observe(path)
    for field in before.keys() - {"schema", "version"}:
        assert after[field] == before[field]
    snapshots = list((tmp_path / "migration-backups").glob("*.db"))
    assert len(snapshots) == 1
    assert observe(snapshots[0]) == before
    assert snapshots[0].stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("failure", [
    "CREATE TABLE unfinished(id INTEGER); INSERT INTO no_such_table VALUES(1);",
    "CREATE TABLE unfinished(id INTEGER); COMMIT; INSERT INTO no_such_table VALUES(1);",
    "CREATE TABLE unfinished(id INTEGER); INSERT INTO ratings VALUES(31,999,4,'now');",
])
def test_failed_upgrade_leaves_old_schema_and_values_intact(tmp_path, monkeypatch, failure):
    path = old_database(tmp_path / "old.db")
    before = observe(path)
    add_migrations(monkeypatch, "ALTER TABLE images ADD COLUMN first_step INTEGER DEFAULT 0;", failure)
    with pytest.raises(ServiceError) as caught:
        db.open_db(path)
    assert caught.value.code == "migration_failed"
    assert caught.value.next_step
    assert observe(path) == before
    snapshot = next((tmp_path / "migration-backups").glob("*.db"))
    assert observe(snapshot) == before


def test_snapshot_includes_wal_and_restores_independently(tmp_path, monkeypatch):
    path = old_database(tmp_path / "old.db")
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("UPDATE notes SET body='WAL value' WHERE id=67")
    writer.commit()
    try:
        assert path.with_name("old.db-wal").stat().st_size > 0
        before = observe(path)
        add_migrations(monkeypatch, "ALTER TABLE images ADD COLUMN fixture_value INTEGER DEFAULT 0;")
        db.open_db(path).close()
        snapshot = next((tmp_path / "migration-backups").glob("*.db"))
        assert observe(snapshot) == before
        with sqlite3.connect(snapshot) as src, sqlite3.connect(tmp_path / "restored.db") as dst:
            src.backup(dst)
        assert observe(tmp_path / "restored.db") == before
    finally:
        writer.close()


@pytest.mark.parametrize("value,code", [("999", "unsupported_schema"), ("broken", "invalid_schema"), ("-1", "invalid_schema")])
def test_invalid_or_future_schema_is_read_only_failure(tmp_path, value, code):
    path = old_database(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (value,))
    original = path.read_bytes()
    with pytest.raises(ServiceError) as caught:
        db.open_db(path)
    assert caught.value.code == code
    assert path.read_bytes() == original
    assert not (tmp_path / "migration-backups").exists()


def test_unknown_database_is_not_initialized_as_pic_shelf(tmp_path):
    path = tmp_path / "foreign.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated(id INTEGER)")
    original = path.read_bytes()
    with pytest.raises(ServiceError) as caught:
        db.open_db(path)
    assert caught.value.code == "invalid_schema"
    assert path.read_bytes() == original


def test_future_schema_errors_are_actionable_in_cli_and_api(tmp_path):
    cfg = Config(tmp_path / "data")
    old_database(cfg.db_path)
    with sqlite3.connect(cfg.db_path) as conn:
        conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    result = cli(cfg, "stats")
    assert result.returncode != 0 and "Traceback" not in result.stderr
    assert "unsupported_schema" in result.stderr
    with TestClient(create_app(cfg)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unsupported_schema"
    assert response.json()["error"]["next_step"]


def test_concurrent_initialization_uses_one_complete_schema(tmp_path):
    path = tmp_path / "shared.db"
    script = "from pathlib import Path; from picshelf import db; import sys; c=db.open_db(Path(sys.argv[1])); print(db.schema_version(c)); c.close()"
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    try:
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
            assert out.strip() == str(db.SCHEMA_VERSION)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_uncommitted_caller_work_is_never_committed_by_migration(tmp_path):
    path = old_database(tmp_path / "old.db")
    conn = db.connect(path)
    try:
        conn.execute("UPDATE notes SET body='not committed' WHERE id=67")
        with pytest.raises(ServiceError) as caught:
            db.migrate(conn)
        assert caught.value.code == "transaction_active"
        assert conn.in_transaction
        with sqlite3.connect(path) as observer:
            assert observer.execute("SELECT body FROM notes WHERE id=67").fetchone()[0] == "A note"
    finally:
        conn.rollback()
        conn.close()


def test_trigger_and_quoted_semicolons_migrate_without_splitting_sql(tmp_path, monkeypatch):
    path = old_database(tmp_path / "old.db")
    add_migrations(monkeypatch, """
        ALTER TABLE tags ADD COLUMN fixture_label TEXT DEFAULT 'first;second';
        CREATE TABLE fixture_audit(body TEXT);
        CREATE TRIGGER note_audit AFTER UPDATE OF body ON notes BEGIN
          INSERT INTO fixture_audit VALUES(OLD.body || ';' || NEW.body);
          INSERT INTO fixture_audit VALUES('second;event');
        END;
    """)
    conn = db.open_db(path)
    try:
        assert conn.execute("SELECT fixture_label FROM tags WHERE id=53").fetchone()[0] == "first;second"
        conn.execute("UPDATE notes SET body='changed' WHERE id=67")
        conn.commit()
        assert [r[0] for r in conn.execute("SELECT body FROM fixture_audit")] == ["A note;changed", "second;event"]
    finally:
        conn.close()


def test_process_interruption_during_ddl_rolls_back_on_reopen(tmp_path):
    path = old_database(tmp_path / "old.db")
    before = observe(path)
    script = """
import os, sys
from pathlib import Path
from picshelf import db
db.SCHEMA_VERSION = 2
db.MIGRATIONS = {**db.MIGRATIONS, 2: 'CREATE TABLE unfinished(id INTEGER); SELECT interrupt_process();'}
c = db.connect(Path(sys.argv[1]))
c.create_function('interrupt_process', 0, lambda: os._exit(73))
db.migrate(c)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=20)
    assert result.returncode == 73
    assert observe(path) == before
    db.open_db(path).close()
    assert observe(path) == before


def test_missing_migration_and_snapshot_failure_do_not_change_old_data(tmp_path, monkeypatch):
    path = old_database(tmp_path / "old.db")
    before = observe(path)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    with pytest.raises(ServiceError) as caught:
        db.open_db(path)
    assert caught.value.code == "migration_unavailable"
    assert observe(path) == before
    add_migrations(monkeypatch, "ALTER TABLE images ADD COLUMN fixture_value INTEGER;")
    # A blocked snapshot directory is a real filesystem failure, not a mocked backup.
    (tmp_path / "migration-backups").write_text("Existing unrelated file")
    with pytest.raises(ServiceError) as caught:
        db.open_db(path)
    assert caught.value.code == "migration_failed"
    assert observe(path) == before
    assert (tmp_path / "migration-backups").read_text() == "Existing unrelated file"


def test_released_v1_migration_remains_unchanged():
    frozen = V1.read_text().split("\n", 1)[1].split("INSERT INTO meta")[0].strip()
    assert db.MIGRATIONS[1].strip() == frozen


def test_restore_rejects_future_schema_before_replacing_current_library(tmp_path):
    cfg = Config(tmp_path / "data")
    old_database(cfg.db_path)
    before = observe(cfg.db_path)
    future = old_database(tmp_path / "future.db")
    with sqlite3.connect(future) as conn:
        conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        conn.execute("UPDATE notes SET body='Must never replace the current library'")
    archive = tmp_path / "future.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.write(future, "picshelf.db")
    result = cli(cfg, "import-backup", str(archive), "--force")
    assert result.returncode != 0 and "unsupported_schema" in result.stderr
    assert "Traceback" not in result.stderr
    assert observe(cfg.db_path) == before
