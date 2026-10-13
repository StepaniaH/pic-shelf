"""First-use requirements: stable paths, read-only originals and explicit intent."""
import json

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from picshelf import auth, config, db, import_plans, indexer, libsvc, ops, search
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.cli import main
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.datasets import draw_image, file_inventory
from tests.scenario_support import build_catalog, cli, metadata_snapshot
from tests.test_migrations import old_database


@pytest.fixture
def catalog(tmp_path):
    return build_catalog(tmp_path)


def test_indexed_read_only_originals_allow_scan_and_metadata(catalog, tmp_path):
    root = tmp_path / "read-only originals"
    draw_image(root / "original.png", 7)
    before = file_inventory(root)
    (root / "original.png").chmod(0o444)
    root.chmod(0o555)
    try:
        with db.open_db(catalog.cfg.db_path) as conn:
            lid = libsvc.create_library(conn, "read-only", str(root), mode="indexed")
            assert indexer.scan_library(conn, catalog.cfg, lid)["added"] == 1
            row = conn.execute("SELECT id FROM images WHERE library_id=?", (lid,)).fetchone()
            iid = row[0]
            uid = catalog.users["curator"]
            ops.set_rating(conn, iid, uid, 0)
            ops.add_tags(conn, iid, ["Reviewed source"], uid)
            found = search.get_image(conn, iid, uid, None)
            assert found["my_rating"] == 0
            assert {tag["name"] for tag in found["tags"]} == {"Reviewed source"}
            with pytest.raises(ServiceError) as caught:
                import_plans.create_plan(conn, AccessContext(uid), lid, str(catalog.roots / "north"))
            assert caught.value.code == "library_indexed"
        assert file_inventory(root) == before
    finally:
        root.chmod(0o755)
        (root / "original.png").chmod(0o644)


def test_managed_default_does_not_change_a_saved_preview(catalog, tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    access = AccessContext(catalog.users["curator"])
    with db.open_db(catalog.cfg.db_path) as conn:
        lid = libsvc.create_library(conn, "managed", str(root), mode="managed", default_import_mode="copy")
        plan = import_plans.create_plan(conn, access, lid, str(catalog.roots / "north"))
        assert plan["mode"] == "copy" and plan["stats"]["copy"] == 12
        libsvc.update_library(conn, catalog.cfg, access, lid, default_import_mode="move")
        result = import_plans.execute_plan(conn, catalog.cfg, access, plan["plan_id"])
        assert result["done"]["copy"] == 12 and result["done"]["move"] == 0
        assert len(list((catalog.roots / "north").glob("*.png"))) == 12
        assert all((root / p.name).stat().st_ino != p.stat().st_ino
                   for p in (catalog.roots / "north").glob("*.png"))


def test_mode_change_blocks_an_older_import_before_writing(catalog, tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    access = AccessContext(catalog.users["curator"])
    with db.open_db(catalog.cfg.db_path) as conn:
        lid = libsvc.create_library(conn, "target", str(root), mode="managed")
        plan = import_plans.create_plan(conn, access, lid, str(catalog.roots / "north"), mode="move")
        before = file_inventory(catalog.roots)
        libsvc.update_library(conn, catalog.cfg, access, lid, mode="indexed")
        with pytest.raises(ServiceError) as caught:
            import_plans.execute_plan(conn, catalog.cfg, access, plan["plan_id"])
        assert caught.value.code == "library_indexed"
        assert file_inventory(root) == {} and file_inventory(catalog.roots) == before


@pytest.mark.parametrize("kind", ["same", "alias", "child", "parent"])
def test_new_registration_rejects_overlapping_physical_roots(catalog, tmp_path, kind):
    root = catalog.roots / "north"
    candidate = root
    if kind == "alias":
        candidate = tmp_path / "alias"
        candidate.symlink_to(root, target_is_directory=True)
    elif kind == "child":
        candidate = root / "subdirectory"
        candidate.mkdir()
    elif kind == "parent":
        candidate = catalog.roots
    with db.open_db(catalog.cfg.db_path) as conn:
        with pytest.raises(ServiceError) as caught:
            libsvc.create_library(conn, "overlapping", str(candidate), mode="indexed")
        assert caught.value.code == "library_root_overlap"
        assert conn.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 2


def test_loop_and_unwritable_managed_root_report_actionable_errors(catalog, tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    root = tmp_path / "read-only"
    root.mkdir(mode=0o555)
    try:
        with db.open_db(catalog.cfg.db_path) as conn:
            for path, mode, code in [(loop, "indexed", "library_path_invalid"),
                                     (root, "managed", "library_root_unwritable")]:
                with pytest.raises(ServiceError) as caught:
                    libsvc.create_library(conn, code, str(path), mode=mode)
                assert caught.value.code == code and caught.value.next_step
        assert list(root.iterdir()) == []
    finally:
        root.chmod(0o755)


def test_upgrade_keeps_legacy_intent_paths_and_manual_data(tmp_path):
    path = old_database(tmp_path / "old.db")
    with db.connect(path) as conn:
        before = metadata_snapshot(conn)
        roots = list(map(tuple, conn.execute("SELECT id,root_path FROM libraries")))
    with db.open_db(path) as conn:
        assert metadata_snapshot(conn) == before
        assert list(map(tuple, conn.execute("SELECT id,root_path FROM libraries"))) == roots
        assert list(map(tuple, conn.execute("SELECT mode,default_import_mode FROM libraries"))) == [(None, "hardlink")]


def test_cli_first_use_managed_preview_and_legacy_compatibility(catalog, tmp_path):
    target = tmp_path / "new managed library"
    added = cli(catalog.cfg, "library", "add", "new", str(target), "--mkdir", "--mode", "managed",
                "--default-import-mode", "copy")
    assert added.returncode == 0, added.stderr
    before = file_inventory(catalog.roots)
    preview = cli(catalog.cfg, "import", str(catalog.roots / "north"), "new", "--json")
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["stats"]["copy"] == 12
    assert file_inventory(target) == {} and file_inventory(catalog.roots) == before
    changed = cli(catalog.cfg, "library", "configure", "new", "--mode", "indexed")
    assert changed.returncode == 0, changed.stderr
    refused = cli(catalog.cfg, "import", str(catalog.roots / "north"), "new", "--json")
    assert refused.returncode != 0 and "library_indexed" in refused.stderr and "Traceback" not in refused.stderr
    legacy = tmp_path / "legacy"
    assert cli(catalog.cfg, "library", "add", "legacy", str(legacy), "--mkdir").returncode == 0
    old_preview = cli(catalog.cfg, "import", str(catalog.roots / "north"), "legacy", "--json")
    assert old_preview.returncode == 0 and json.loads(old_preview.stdout)["mode"] == "hardlink"


def test_api_modes_and_server_path_contract(catalog, tmp_path):
    root = tmp_path / "api-root"
    root.mkdir()
    with TestClient(create_app(catalog.cfg)) as client:
        assert client.post("/api/v1/auth/login", json={"username": "curator", "password": catalog.password}).status_code == 200
        created = client.post("/api/v1/libraries", json={"name": "API", "root_path": str(root),
                              "mode": "managed", "default_import_mode": "copy"})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["mode"] == "managed" and body["default_import_mode"] == "copy"
        invalid = client.post("/api/v1/libraries", json={"name": "relative", "root_path": "browser-local", "mode": "indexed"})
        assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "library_path_invalid"
        assert client.post("/api/v1/libraries", json={"name": "wrong", "root_path": str(root), "mode": "upload"}).status_code == 422
        lid = body["id"]
        assert client.patch(f"/api/v1/libraries/{lid}", json={"root_path": str(tmp_path)}).status_code == 422
        assert client.patch(f"/api/v1/libraries/{lid}", json={"mode": "indexed"}).json()["mode"] == "indexed"
        response = client.post("/api/v1/import/plan", json={"library_id": lid, "source_dir": str(catalog.roots / "north")})
        assert response.status_code == 409 and response.json()["error"]["code"] == "library_indexed"


def test_installed_configuration_requires_explicit_data_and_honors_priority(tmp_path, monkeypatch):
    package_parent = tmp_path / "site-packages"
    package_parent.mkdir()
    monkeypatch.setattr(config, "REPO_ROOT", package_parent)
    monkeypatch.delenv("PICSHELF_DATA", raising=False)
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code != 0 and "--data" in result.output and "init" in result.output
    assert "Traceback" not in result.output and list(package_parent.iterdir()) == []
    explicit = tmp_path / "explicit"
    environment = tmp_path / "environment"
    monkeypatch.setenv("PICSHELF_DATA", str(environment))
    assert Config.resolve().data_dir == environment
    assert Config.resolve(explicit).data_dir == explicit
    result = CliRunner().invoke(main, ["--data", str(explicit), "init"])
    assert result.exit_code == 0 and (explicit / "picshelf.db").is_file()
    assert not environment.exists() and list(package_parent.iterdir()) == []


def test_read_only_data_directory_does_not_fall_back(tmp_path, monkeypatch):
    root = tmp_path / "locked-data"
    root.mkdir(mode=0o555)
    try:
        result = CliRunner().invoke(main, ["--data", str(root), "init"])
        assert result.exit_code != 0 and "--data" in result.output
        assert "data_directory_unwritable" in result.output and "Traceback" not in result.output
        assert list(root.iterdir()) == []
    finally:
        root.chmod(0o755)
