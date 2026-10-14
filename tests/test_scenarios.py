"""User journeys checked against a catalog authored before implementation."""
import asyncio
import hashlib
import json
import os
import zipfile

import pytest
from PIL import Image

from picshelf import auth, db, importer, indexer, libsvc, search
from picshelf.config import Config
from tests.datasets import CATALOG, FAULTS, boundary_files, file_inventory, image_samples, metadata_rows
from tests.scenario_support import build_catalog, cli, http_server, metadata_snapshot


@pytest.fixture
def catalog(tmp_path):
    return build_catalog(tmp_path)


def test_import_preview_preserves_source_and_target(catalog, tmp_path):
    target = tmp_path / "destination"
    target.mkdir()
    (target / "keep.txt").write_text("Existing unrelated file")
    before_source = file_inventory(catalog.roots)
    before_target = file_inventory(target)
    with db.open_db(catalog.cfg.db_path) as conn:
        lid = libsvc.create_library(conn, "destination", str(target))
        library = libsvc.get_library(conn, lid)
        plan = importer.plan_import(conn, library, catalog.roots / "north", "copy")
    assert plan["stats"]["copy"] == 12
    assert file_inventory(catalog.roots) == before_source
    assert file_inventory(target) == before_target


def test_move_preserves_identity_and_all_manual_associations(catalog):
    original = catalog.roots / "north" / CATALOG["images"][0]["path"]
    original_bytes = original.read_bytes()
    moved = original.with_name("moved original.png")
    with db.open_db(catalog.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        original.rename(moved)
        result = indexer.scan_library(conn, catalog.cfg, catalog.libraries["north"])
        assert result["moved"] == 1 and result["added"] == 0 and not result["errors"]
        row = conn.execute("SELECT id,rel_path,missing FROM images WHERE id=?",
                           (catalog.images["n01"],)).fetchone()
        assert tuple(row) == (catalog.images["n01"], moved.name, 0)
        assert metadata_snapshot(conn) == before
    assert moved.read_bytes() == original_bytes and not original.exists()


@pytest.mark.parametrize("username,expected_count", [("curator", 24), ("reader", 12), ("outsider", 0)])
def test_catalog_permissions_follow_manifest(catalog, username, expected_count):
    expected = {catalog.images[item["key"]] for item in CATALOG["images"]
                if item["library"] in CATALOG["users"][username]["libraries"]}
    assert len(expected) == expected_count
    with db.open_db(catalog.cfg.db_path) as conn:
        user = auth.get_user(conn, catalog.users[username])
        allowed = auth.visible_library_ids(conn, user)
        result = search.search_images(conn, user["id"], allowed, limit=100)
        assert result["total"] == expected_count
        assert {row["id"] for row in result["items"]} == expected
        for item in CATALOG["images"]:
            detail = search.get_image(conn, catalog.images[item["key"]], user["id"], allowed)
            assert (detail is not None) == (catalog.images[item["key"]] in expected)


def test_search_matches_hand_authored_sets_and_stable_pages(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        for case in CATALOG["queries"]:
            result = search.search_images(conn, catalog.users["reader"],
                                          [catalog.libraries["north"]], query=case["query"])
            assert {row["id"] for row in result["items"]} == {
                catalog.images[key] for key in case["expected"]}, case["query"]
            assert bool(result["errors"]) == case.get("error", False), case["query"]
        observed = []
        for offset in range(0, 12, 5):
            result = search.search_images(conn, catalog.users["reader"], [catalog.libraries["north"]],
                                          sort="path", limit=5, offset=offset)
            assert result["total"] == 12
            observed.extend(row["id"] for row in result["items"])
        assert observed == [catalog.images[f"n{n:02}"] for n in range(1, 13)]


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_cli_process_json_exit_status_and_no_input_wait(catalog, lang):
    for alias in ("ls", "search"):
        result = cli(catalog.cfg, alias, "--user", "reader", "-q", 'tag:"night sky"', "--json", lang=lang)
        assert result.returncode == 0, result.stderr
        body = json.loads(result.stdout)
        assert body["total"] == 2
        assert {row["id"] for row in body["items"]} == {catalog.images["n01"], catalog.images["n04"]}
    invalid = cli(catalog.cfg, "ls", "--limit", "-1", "--json", lang=lang)
    assert invalid.returncode != 0 and invalid.stderr
    assert "Traceback" not in invalid.stderr
    empty = cli(Config(catalog.cfg.data_dir.parent / "uninitialized"), "ls", "--json", lang=lang)
    assert empty.returncode != 0 and "init" in empty.stderr


def test_backup_restores_committed_wal_and_rejects_truncated_archive(catalog, tmp_path):
    writer = db.open_db(catalog.cfg.db_path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE notes SET body='Committed in WAL' WHERE image_id=?", (catalog.images["n01"],))
        writer.commit()
        assert catalog.cfg.db_path.with_name("picshelf.db-wal").stat().st_size > 0
        before = metadata_snapshot(writer)
        out = tmp_path / "backup.zip"
        exported = cli(catalog.cfg, "export", str(out))
        assert exported.returncode == 0, exported.stderr
        with zipfile.ZipFile(out) as archive:
            assert set(archive.namelist()) == {"meta.json", "picshelf.db"}
        restored = Config(tmp_path / "restored")
        result = cli(restored, "import-backup", str(out))
        assert result.returncode == 0, result.stderr
        with db.open_db(restored.db_path) as conn:
            assert metadata_snapshot(conn) == before
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        corrupt = tmp_path / "truncated.zip"
        corrupt.write_bytes(out.read_bytes()[:100])
        rejected = cli(restored, "import-backup", str(corrupt), "--force")
        assert rejected.returncode != 0 and "Traceback" not in rejected.stderr
        with db.open_db(restored.db_path) as conn:
            assert metadata_snapshot(conn) == before
    finally:
        writer.close()


@pytest.mark.skipif(os.environ.get("PICSHELF_HTTP_TESTS") != "1", reason="opt-in real HTTP suite")
@pytest.mark.http
def test_real_http_permissions_and_queries(catalog):
    with http_server(catalog.cfg) as client:
        assert client.get("/api/v1/images").status_code == 401
        for username, spec in CATALOG["users"].items():
            client.cookies.clear()
            response = client.post("/api/v1/auth/login", json={"username": username, "password": catalog.password})
            assert response.status_code == 200
            expected = {catalog.images[item["key"]] for item in CATALOG["images"]
                        if item["library"] in spec["libraries"]}
            images = client.get("/api/v1/images", params={"limit": 100}).json()
            assert {row["id"] for row in images["items"]} == expected
            assert client.get("/api/v1/stats").json()["images"] == len(expected)
            for key in ("n01", "s01"):
                iid = catalog.images[key]
                for endpoint in (f"images/{iid}", f"media/original/{iid}", f"media/thumb/{iid}"):
                    response = client.get("/api/v1/" + endpoint)
                    assert response.status_code == (200 if iid in expected else 403)
            if username == "reader":
                for case in CATALOG["queries"]:
                    result = client.get("/api/v1/images", params={"q": case["query"]}).json()
                    assert {row["id"] for row in result["items"]} == {catalog.images[key] for key in case["expected"]}
                assert "private" not in {tag["name"] for tag in client.get("/api/v1/tags").json()}


def test_tui_keyboard_search_and_rating_follow_catalog(catalog):
    from picshelf.tui import PicShelfTUI

    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("/")
            for char in "file:100%": await pilot.press(char)
            await pilot.press("enter")
            await pilot.pause()
            assert app.total == 1
            await pilot.press("r", "6", "enter")
            await pilot.pause()

    asyncio.run(scenario())
    with db.open_db(catalog.cfg.db_path) as conn:
        row = conn.execute("SELECT image_id,value FROM ratings WHERE user_id=?", (catalog.users["curator"],)).fetchone()
        assert tuple(row) == (catalog.images["n05"], 6)
        assert conn.execute("SELECT value FROM ratings WHERE user_id=? AND image_id=?",
                            (catalog.users["reader"], catalog.images["n05"])).fetchone()[0] == 2


def test_boundary_and_image_generators_report_capabilities(tmp_path):
    boundaries = boundary_files(tmp_path / "files")
    assert boundaries["files"]["中文.png"]["sha256"] == boundaries["files"]["duplicate.png"]["sha256"]
    assert isinstance(boundaries["capabilities"]["case_sensitive"], bool)
    samples = image_samples(tmp_path / "images")
    for filename in ("animation.gif", "pages.tiff"):
        with Image.open(tmp_path / "images" / filename) as image:
            assert image.n_frames == samples["expected"][filename]["frames"]
    assert FAULTS["disk_full"]["invariant"] == "source bytes remain intact"


def test_metadata_scale_has_reproducible_independent_identity():
    rows = list(metadata_rows(10000, 1))
    assert len({row["rel_path"] for row in rows}) == 10000
    assert rows[0]["sha256"] == hashlib.sha256(b"image-000000.png").hexdigest()
    assert rows[-1]["filename"] == "image-009999.png"
    assert list(metadata_rows(2, 1)) == rows[:2]
