"""Independent filesystem and pixel expectations for scanning and derivatives."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import zlib

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from picshelf import auth, hashing, indexer, jobs, libsvc, ops
from picshelf.api import create_app
from picshelf.access import AccessContext
from picshelf.errors import ServiceError
from tests.datasets import draw_image, file_inventory, image_samples
from tests.scenario_support import cli, metadata_snapshot


def test_replaced_root_is_not_an_empty_library(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    original = library["root"]
    moved = original.with_name("temporarily-offline")
    original.rename(moved)
    original.mkdir()
    try:
        with pytest.raises(ServiceError) as caught:
            indexer.scan_library(conn, cfg, library["id"])
        assert caught.value.code == "library_root_changed"
        assert conn.execute("SELECT COUNT(*) FROM images WHERE missing=0").fetchone()[0] == 4
    finally:
        original.rmdir()
        moved.rename(original)
    assert indexer.scan_library(conn, cfg, library["id"])["unchanged"] == 4


def test_root_disappearing_during_walk_does_not_mark_missing(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    root = library["root"]
    moved = root.with_name("disconnected")
    interrupted = False

    def after_file(*_):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            root.rename(moved)
            root.mkdir()

    try:
        with pytest.raises(ServiceError):
            indexer.scan_library(conn, cfg, library["id"], item_result=after_file)
        assert conn.execute("SELECT COUNT(*) FROM images WHERE missing=1").fetchone()[0] == 0
    finally:
        root.rmdir()
        moved.rename(root)


def test_upgraded_library_without_saved_directory_identity_refuses_empty_replacement(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    conn.execute("UPDATE libraries SET root_device=NULL,root_inode=NULL")
    conn.commit()
    root = library["root"]
    saved = root.with_name("original-directory")
    root.rename(saved)
    root.mkdir()
    try:
        with pytest.raises(ServiceError) as caught:
            indexer.scan_library(conn, cfg, library["id"])
        assert caught.value.code == "library_root_unverified"
        assert conn.execute("SELECT COUNT(*) FROM images WHERE missing=0").fetchone()[0] == 4
        assert conn.execute("SELECT root_device FROM libraries WHERE id=?", (library["id"],)).fetchone()[0] is None
    finally:
        root.rmdir()
        saved.rename(root)
    assert indexer.scan_library(conn, cfg, library["id"])["unchanged"] == 4


def test_ambiguous_copies_never_inherit_arbitrary_ratings(conn, cfg, admin, tmp_path):
    root = tmp_path / "duplicates"
    draw_image(root / "a.png", 1)
    shutil.copyfile(root / "a.png", root / "b.png")
    lid = libsvc.create_library(conn, "duplicates", str(root))
    indexer.scan_library(conn, cfg, lid)
    rows = conn.execute("SELECT id,rel_path FROM images ORDER BY rel_path").fetchall()
    ops.set_rating(conn, rows[0]["id"], admin["id"], 1)
    ops.set_rating(conn, rows[1]["id"], admin["id"], 9)
    before = metadata_snapshot(conn)
    (root / "a.png").rename(root / "c.png")
    (root / "b.png").rename(root / "d.png")
    result = indexer.scan_library(conn, cfg, lid)
    assert result["moved"] == 0 and result["added"] == 0 and result["missing"] == 2
    assert result["errors"] and len(result["ambiguous_moves"]) == 1
    assert [r[0] for r in conn.execute("SELECT rel_path FROM images ORDER BY id")] == ["a.png", "b.png"]
    assert metadata_snapshot(conn) == before
    again = indexer.scan_library(conn, cfg, lid)
    assert again["moved"] == 0 and again["added"] == 0 and again["errors"]
    assert (root / "c.png").read_bytes() == (root / "d.png").read_bytes()


def test_full_verification_finds_same_size_same_time_changes(conn, cfg, tmp_path):
    root = tmp_path / "equal-size"
    root.mkdir()
    source = root / "image.bmp"
    Image.new("RGB", (40, 40), "red").save(source)
    lid = libsvc.create_library(conn, "equal-size", str(root))
    indexer.scan_library(conn, cfg, lid)
    previous = source.stat()
    before = conn.execute("SELECT id,sha256 FROM images").fetchone()
    Image.new("RGB", (40, 40), "blue").save(source)
    assert source.stat().st_size == previous.st_size
    os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    assert expected != before["sha256"]
    assert indexer.scan_library(conn, cfg, lid)["unchanged"] == 1
    assert indexer.scan_library(conn, cfg, lid, full=True)["updated"] == 1
    after = conn.execute("SELECT id,sha256 FROM images").fetchone()
    assert after["id"] == before["id"] and after["sha256"] == expected


def test_decode_error_and_missing_are_distinct_and_recoverable(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute("SELECT * FROM images WHERE filename='red.png'").fetchone()
    source = library["root"] / row["rel_path"]
    original = source.read_bytes()
    before = metadata_snapshot(conn)
    source.write_bytes(b"a corrupt disposable image")
    indexer.scan_library(conn, cfg, library["id"])
    failed = conn.execute("SELECT * FROM images WHERE id=?", (row["id"],)).fetchone()
    assert failed["missing"] == 0 and failed["scan_error_code"] == "image_decode_error"
    assert metadata_snapshot(conn) == before
    source.write_bytes(original)
    indexer.scan_library(conn, cfg, library["id"])
    repaired = conn.execute("SELECT * FROM images WHERE id=?", (row["id"],)).fetchone()
    assert repaired["scan_error_code"] is None and repaired["missing"] == 0
    assert repaired["sha256"] == hashlib.sha256(original).hexdigest()


def test_oriented_transparent_gray_profile_and_first_frame_pixels(tmp_path):
    root = tmp_path / "samples"
    image_samples(root)
    before = file_inventory(root)
    for number, source in enumerate(sorted(root.iterdir()), 1):
        thumb, preview, width, height = hashing.make_derivatives(source, tmp_path / "thumbs", tmp_path / "previews", number)
        expected_size = (40, 80) if source.name in ("orientation.jpg", "alpha.png") else (80, 40)
        assert (width, height) == expected_size
        for rel in (thumb, preview):
            with Image.open(tmp_path / rel) as output:
                assert output.size == expected_size
                assert output.info.get("icc_profile")
                pixels = output.convert("RGBA")
                if source.name == "alpha.png":
                    assert pixels.getpixel((0, 0))[3] == 0
                    assert pixels.getpixel((20, 40))[3] == 255
                elif source.name == "gray.png":
                    rgb = pixels.getpixel((20, 20))[:3]
                    assert max(rgb) - min(rgb) <= 2
                else:
                    left = pixels.getpixel((20, 10)) if source.name == "orientation.jpg" else pixels.getpixel((20, 20))
                    right = pixels.getpixel((20, 60)) if source.name == "orientation.jpg" else pixels.getpixel((60, 20))
                    assert left[0] > 200 and left[2] < 35
                    assert right[2] > 200 and right[0] < 35
    assert file_inventory(root) == before


def test_transparent_hidden_colors_do_not_change_fingerprint(tmp_path):
    for name, color in (("one.png", (255, 0, 0, 0)), ("two.png", (0, 0, 255, 0))):
        image = Image.new("RGBA", (80, 60), color)
        image.paste((0, 255, 0, 255), (20, 20, 60, 40))
        image.save(tmp_path / name)
    assert hashing.phash_file(tmp_path / "one.png") == hashing.phash_file(tmp_path / "two.png")


def test_downscaling_preserves_the_declared_aspect_ratio(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGB", (4000, 2000), "red").save(source)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    thumb, preview, width, height = hashing.make_derivatives(source, tmp_path / "thumbs", tmp_path / "previews", 1)
    assert (width, height) == (4000, 2000)
    with Image.open(tmp_path / thumb) as image:
        assert image.size == (400, 200)
    with Image.open(tmp_path / preview) as image:
        assert image.size == (1600, 800)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_huge_header_is_rejected_before_decode_and_batch_continues(conn, cfg, tmp_path):
    root = tmp_path / "limits"
    draw_image(root / "good.png", 5)
    # A PNG with huge declared dimensions, only a tiny encoded stream. Never
    # allocate the claimed pixels or fill the host disk to test resource limits.
    raw = bytearray((root / "good.png").read_bytes())
    raw[16:24] = struct.pack(">II", 20000, 20000)
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]))
    (root / "huge.png").write_bytes(raw)
    original = file_inventory(root)
    lid = libsvc.create_library(conn, "limits", str(root))
    result = indexer.scan_library(conn, cfg, lid)
    assert result["added"] == 2 and result["errors"]
    huge = conn.execute("SELECT * FROM images WHERE filename='huge.png'").fetchone()
    assert huge["scan_error_code"] == "image_too_large" and huge["missing"] == 0
    assert huge["thumb_rel"] is None
    assert conn.execute("SELECT thumb_rel FROM images WHERE filename='good.png'").fetchone()[0]
    assert file_inventory(root) == original


def test_invalid_profile_is_an_actionable_item_error(conn, cfg, tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    Image.new("RGB", (20, 20), "red").save(root / "bad-profile.png", icc_profile=b"not an ICC profile")
    draw_image(root / "good.png", 7)
    lid = libsvc.create_library(conn, "profiles", str(root))
    result = indexer.scan_library(conn, cfg, lid)
    assert result["added"] == 2 and len(result["errors"]) == 1
    failed = conn.execute("SELECT scan_error_code,missing FROM images WHERE filename='bad-profile.png'").fetchone()
    assert tuple(failed) == ("invalid_color_profile", 0)


def test_failed_second_derivative_preserves_both_visible_previous_images(conn, cfg, library, monkeypatch):
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute("SELECT * FROM images WHERE filename='red.png'").fetchone()
    old_bytes = {rel: (cfg.media_dir / rel).read_bytes() for rel in (row["thumb_rel"], row["preview_rel"])}
    source = library["root"] / row["rel_path"]
    Image.new("RGB", (70, 100), "blue").save(source)
    original_save, saves = hashing._save_webp, 0

    def fail_preview(img, path, quality):
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError("injected preview write failure")
        original_save(img, path, quality)

    monkeypatch.setattr(hashing, "_save_webp", fail_preview)
    result = indexer.scan_library(conn, cfg, library["id"])
    assert result["errors"]
    after = conn.execute("SELECT * FROM images WHERE id=?", (row["id"],)).fetchone()
    assert (after["thumb_rel"], after["preview_rel"], after["sha256"]) == (row["thumb_rel"], row["preview_rel"], row["sha256"])
    assert {rel: (cfg.media_dir / rel).read_bytes() for rel in old_bytes} == old_bytes
    for rel in old_bytes:
        with Image.open(cfg.media_dir / rel) as image:
            image.load()


def test_explicit_outdated_rebuild_preserves_current_outputs(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library["id"])
    rows = conn.execute("SELECT * FROM images ORDER BY id").fetchall()
    before = {row["id"]: (cfg.media_dir / row["thumb_rel"]).stat().st_mtime_ns for row in rows}
    conn.execute("UPDATE images SET phash_version=0,derivative_version=0 WHERE id=?", (rows[0]["id"],))
    conn.commit()
    assert indexer.scan_library(conn, cfg, library["id"])["unchanged"] == 4
    assert (cfg.media_dir / rows[0]["thumb_rel"]).stat().st_mtime_ns == before[rows[0]["id"]]
    result = cli(cfg, "rebuild", "testlib", "--outdated-only")
    assert result.returncode == 0, result.stderr
    done = json.loads(result.stdout)
    assert (done["state"], done["total"], done["succeeded"]) == ("succeeded", 1, 1)
    assert conn.execute("SELECT phash_version,derivative_version FROM images WHERE id=?", (rows[0]["id"],)).fetchone()[:] == (2, 2)
    for row in rows[1:]:
        assert (cfg.media_dir / row["thumb_rel"]).stat().st_mtime_ns == before[row["id"]]


def test_move_repairs_missing_derivative_without_losing_identity(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute("SELECT * FROM images WHERE filename='red.png'").fetchone()
    ops.set_rating(conn, row["id"], admin["id"], 0)
    original = library["root"] / row["rel_path"]
    original.rename(library["root"] / "renamed.png")
    (cfg.media_dir / row["thumb_rel"]).unlink()
    result = indexer.scan_library(conn, cfg, library["id"])
    assert result["moved"] == 1 and not result["errors"]
    assert conn.execute("SELECT id FROM images WHERE filename='renamed.png'").fetchone()[0] == row["id"]
    assert conn.execute("SELECT value FROM ratings WHERE image_id=?", (row["id"],)).fetchone()[0] == 0
    assert (cfg.media_dir / row["thumb_rel"]).is_file()


def test_phash_comparison_never_mixes_algorithm_versions(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    conn.execute("UPDATE images SET phash='0000000000000000',phash_version=2")
    ids = [r[0] for r in conn.execute("SELECT id FROM images ORDER BY id")]
    conn.execute("UPDATE images SET phash_version=0 WHERE id=?", (ids[0],))
    conn.commit()
    groups = libsvc.similar_dupes(conn, threshold=0)
    assert len(groups) == 1
    assert {row["id"] for row in groups[0]["images"]} == set(ids[1:])


@pytest.mark.parametrize("format,extension", [("JPEG", "jpg"), ("PNG", "png"), ("GIF", "gif"),
                                              ("WEBP", "webp"), ("BMP", "bmp"), ("TIFF", "tiff"), ("AVIF", "avif")])
def test_format_decode_matrix(tmp_path, format, extension):
    if format not in Image.SAVE:
        pytest.skip(f"{format} encoder unavailable in this Pillow build")
    source = tmp_path / ("source." + extension)
    Image.new("RGB", (80, 40), (240, 30, 20)).save(source, format=format)
    before = source.read_bytes()
    thumb, preview, width, height = hashing.make_derivatives(source, tmp_path / "thumbs", tmp_path / "previews", 1)
    assert (width, height) == (80, 40) and hashing.phash_file(source)
    with Image.open(tmp_path / preview) as image:
        red, green, blue = image.convert("RGB").getpixel((40, 20))
        assert red > 210 and green < 65 and blue < 55
    assert source.read_bytes() == before


def test_api_cache_keys_change_with_derivative_version_and_offline_root_is_not_served(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library["id"])
    row = conn.execute("SELECT * FROM images WHERE filename='red.png'").fetchone()
    conn.execute("UPDATE images SET derivative_version=0,phash_version=0 WHERE id=?", (row["id"],))
    conn.commit()
    app = create_app(cfg)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + auth.sign_token(app.state.secret, admin["id"])
        old = client.get(f"/api/v1/images/{row['id']}").json()
        old_media = client.get(old["thumb_url"])
        result = cli(cfg, "rebuild", "testlib", "--outdated-only")
        assert result.returncode == 0, result.stderr
        new = client.get(f"/api/v1/images/{row['id']}").json()
        assert new["thumb_url"] != old["thumb_url"] and new["original_url"] == old["original_url"]
        assert client.get(new["thumb_url"]).headers["etag"] != old_media.headers["etag"]
        assert new["derivative_version"] == 2 and new["phash_version"] == 2
        root, saved = library["root"], library["root"].with_name("offline-root")
        root.rename(saved)
        draw_image(root / row["rel_path"], 999)
        try:
            response = client.get(new["original_url"])
            assert response.status_code == 409 and response.json()["error"]["code"] == "library_root_changed"
            assert client.get(new["thumb_url"]).status_code == 200
        finally:
            (root / row["rel_path"]).unlink()
            (root / row["rel_path"]).parent.rmdir()
            root.rmdir()
            saved.rename(root)
