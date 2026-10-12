import os

from picshelf import indexer


def test_scan_adds(conn, cfg, library):
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["added"] == 4
    assert stats["errors"] == []

    rows = conn.execute(
        "SELECT * FROM images WHERE library_id=? ORDER BY rel_path", (library["id"],)
    ).fetchall()
    assert len(rows) == 4
    assert [r["rel_path"] for r in rows] == [
        "alpha/blue.png", "alpha/red.png", "beta/green.jpg", "beta/nested/yellow.png",
    ]
    for r in rows:
        assert r["width"] and r["height"]
        assert len(r["sha256"]) == 64
        assert r["phash"] and len(r["phash"]) == 16
        assert (cfg.media_dir / r["thumb_rel"]).exists()
        assert (cfg.media_dir / r["preview_rel"]).exists()

    # 文件夹专辑物化：alpha / beta / beta-nested
    albums = conn.execute(
        "SELECT * FROM albums WHERE library_id=? ORDER BY name", (library["id"],)
    ).fetchall()
    names = sorted(a["name"] for a in albums)
    assert names == ["alpha", "beta", "nested"]
    by_name = {a["name"]: a for a in albums}
    assert by_name["alpha"]["cover_image_id"] is not None
    cnt = conn.execute(
        "SELECT COUNT(*) FROM album_images WHERE album_id=?", (by_name["alpha"]["id"],)
    ).fetchone()[0]
    assert cnt == 2


def test_rescan_unchanged(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["added"] == 0
    assert stats["unchanged"] == 4
    assert stats["missing"] == 0


def test_modify_file(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute(
        "SELECT id, sha256 FROM images WHERE filename='red.png'"
    ).fetchone()
    red = library["root"] / "alpha" / "red.png"
    # 改内容（尺寸不同 + mtime 变化）
    from PIL import Image
    Image.new("RGB", (400, 400), (10, 10, 10)).save(red)
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["updated"] == 1
    new = conn.execute(
        "SELECT sha256, width FROM images WHERE id=?", (row["id"],)
    ).fetchone()
    assert new["sha256"] != row["sha256"]
    assert new["width"] == 400


def test_rename_detected_as_move(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    old_id = conn.execute(
        "SELECT id FROM images WHERE filename='red.png'"
    ).fetchone()["id"]
    os.rename(library["root"] / "alpha" / "red.png",
              library["root"] / "alpha" / "crimson.png")
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["moved"] == 1
    assert stats["added"] == 0
    assert stats["missing"] == 0
    row = conn.execute("SELECT * FROM images WHERE id=?", (old_id,)).fetchone()
    assert row["rel_path"] == "alpha/crimson.png"
    assert row["missing"] == 0


def test_delete_marks_missing(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    os.unlink(library["root"] / "beta" / "green.jpg")
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["missing"] == 1
    row = conn.execute("SELECT missing FROM images WHERE filename='green.jpg'").fetchone()
    assert row["missing"] == 1
    # 恢复文件后重新出现（内容相同但 mtime 变化 → 记一次 updated 或 unchanged，均视为恢复正常）
    from tests.conftest import make_image
    make_image(library["root"] / "beta" / "green.jpg", (40, 200, 40))
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["added"] == 0
    assert stats["unchanged"] + stats["updated"] == 4
    row = conn.execute("SELECT missing FROM images WHERE filename='green.jpg'").fetchone()
    assert row["missing"] == 0


def test_corrupt_file_recorded_as_error(conn, cfg, library):
    (library["root"] / "beta" / "broken.png").write_bytes(b"garbage-not-image")
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["added"] == 5
    assert any("broken.png" in e for e in stats["errors"])
    scan = conn.execute("SELECT status FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    assert scan["status"] == "done"


def test_decode_failure_does_not_hide_or_reassign_existing_image(conn, cfg, library, monkeypatch):
    import shutil
    from picshelf import hashing
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute("SELECT * FROM images WHERE filename='red.png'").fetchone()
    shutil.copyfile(library["root"] / row["rel_path"], library["root"] / "copy.png")
    original = hashing.make_derivatives

    def fail_red(src, *args):
        if src.name == "red.png":
            raise OSError("temporary decoder failure")
        return original(src, *args)

    monkeypatch.setattr(hashing, "make_derivatives", fail_red)
    stats = indexer.scan_library(conn, cfg, library["id"], full=True)
    assert stats["missing"] == 0 and stats["moved"] == 0 and stats["added"] == 1
    assert stats["errors"]
    after = conn.execute("SELECT * FROM images WHERE id=?", (row["id"],)).fetchone()
    assert after["rel_path"] == row["rel_path"] and after["missing"] == 0


def test_move_out_of_last_folder_repairs_membership_and_cover(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    old = conn.execute("SELECT * FROM albums WHERE name='nested'").fetchone()
    src = library["root"] / "beta/nested/yellow.png"
    src.rename(library["root"] / "alpha/yellow.png")
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["moved"] == 1
    assert conn.execute("SELECT COUNT(*) FROM album_images WHERE album_id=?", (old["id"],)).fetchone()[0] == 0
    assert conn.execute("SELECT cover_image_id FROM albums WHERE id=?", (old["id"],)).fetchone()[0] is None


def test_incremental_scan_repairs_missing_derivatives(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute("SELECT * FROM images LIMIT 1").fetchone()
    (cfg.media_dir / row["thumb_rel"]).unlink()
    stats = indexer.scan_library(conn, cfg, library["id"])
    assert stats["updated"] == 1
    assert (cfg.media_dir / row["thumb_rel"]).is_file()


def test_unreadable_subtree_keeps_index(conn, cfg, library, monkeypatch):
    indexer.scan_library(conn, cfg, library["id"])
    walk = indexer.os.walk
    blocked = library["root"] / "beta"

    def walk_with_error(root, **kwargs):
        for directory, directories, files in walk(root, **kwargs):
            if directory == str(root):
                directories.remove("beta")
                kwargs["onerror"](PermissionError(13, "Permission denied", str(blocked)))
            yield directory, directories, files

    monkeypatch.setattr(indexer.os, "walk", walk_with_error)
    result = indexer.scan_library(conn, cfg, library["id"])
    assert result["errors"] and result["missing"] == 0
    assert conn.execute("SELECT COUNT(*) FROM images WHERE missing=0").fetchone()[0] == 4


def test_scanning_does_not_follow_external_file_links(conn, cfg, library, tmp_path):
    from tests.conftest import make_image
    external = make_image(tmp_path / "outside.png")
    (library["root"] / "outside.png").symlink_to(external)
    result = indexer.scan_library(conn, cfg, library["id"])
    assert result["added"] == 4 and result["errors"]
