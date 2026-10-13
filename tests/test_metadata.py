"""Metadata expectations are authored independently of serializers and search."""
import json
import sqlite3
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from picshelf import db, entity_edits, import_plans, indexer, libsvc, metadata, names, ops, search
from picshelf.config import Config
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import file_inventory
from tests.scenario_support import build_catalog, cli, metadata_snapshot
from tests.test_migrations import old_database


@pytest.fixture
def catalog(tmp_path):
    return build_catalog(tmp_path)


def test_name_matching_handles_unicode_whitespace_and_aliases_without_merging(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        one = ops.ensure_tag(conn, "Café  blue")
        two = conn.execute("INSERT INTO tags(name,created_at) VALUES(?,?)", ("CAFÉ blue", db.utcnow())).lastrowid
        conn.commit()
        assert names.match_key("  Cafe\u0301\tBLUE  ") == "café blue"
        assert names.find(conn, "tag", " cafe\u0301 blue ") == [one, two]
        assert ops.ensure_tag(conn, "Café  blue") == one
        with pytest.raises(ServiceError) as caught:
            ops.ensure_tag(conn, "café blue")
        assert caught.value.code == "name_ambiguous"
        assert list(map(tuple, conn.execute("SELECT id,name FROM tags WHERE id IN (?,?) ORDER BY id", (one,two)))) == [(one,"Café  blue"),(two,"CAFÉ blue")]
        access = AccessContext(catalog.users["curator"])
        preview = entity_edits.preview_alias(conn, access, "tag", one, "色彩: 咖啡")
        assert preview["alias"] == "色彩: 咖啡" and preview["entity_id"] == one
        assert names.find(conn, "tag", "色彩:咖啡") == []
        entity_edits.execute(conn, access, preview["id"])
        assert names.find(conn, "tag", "  色彩 : 咖啡 ") == [one]
        assert ops.ensure_tag(conn, "色彩:咖啡") == one


def test_personal_workflow_is_independent_of_ratings_favorites_and_missing(catalog):
    uid = catalog.users["reader"]
    access = AccessContext(uid)
    ids = [catalog.images[key] for key in ("n01", "n02", "n03")]
    with db.open_db(catalog.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        assert [metadata.get(conn, access, iid)["workflow"]["state"] for iid in ids] == ["inbox"] * 3
        metadata.mark(conn, access, ids[:2], "done")
        assert [metadata.get(conn, access, iid)["workflow"]["state"] for iid in ids] == ["done", "done", "inbox"]
        assert [metadata.get(conn, AccessContext(catalog.users["curator"]), iid)["workflow"]["state"] for iid in ids] == ["inbox"] * 3
        conn.execute("UPDATE images SET missing=1 WHERE id=?", (ids[0],)); conn.commit()
        assert metadata.get(conn, access, ids[0])["workflow"]["state"] == "done"
        metadata.mark(conn, access, [ids[0]], "inbox")
        assert metadata.get(conn, access, ids[0])["workflow"]["state"] == "inbox"
        assert metadata_snapshot(conn) == before
        with pytest.raises(ServiceError):
            metadata.mark(conn, access, [ids[2], catalog.images["s01"]], "done")
        assert metadata.get(conn, access, ids[2])["workflow"]["state"] == "inbox"


def test_sources_are_separate_records_with_explicit_timezone_and_no_fetch(catalog, monkeypatch):
    def unexpected_network(*args, **kwargs):
        raise AssertionError("Provenance must not fetch a source URL")
    monkeypatch.setattr(socket.socket, "connect", unexpected_network)
    access = AccessContext(catalog.users["curator"])
    iid = catalog.images["n01"]
    with db.open_db(catalog.cfg.db_path) as conn:
        before = conn.execute("SELECT mtime,imported_at,sha256 FROM images WHERE id=?", (iid,)).fetchone()
        source = metadata.add_source(conn, access, iid, original_filename="Original sketch.png",
                                     url="https://example.org/work/123", note="Artist-supplied copy",
                                     collected_at="2026-10-10T21:35:00+08:00", created_time="2020-01-02T03:04:05-05:00")
        assert source["origin"] == "manual" and source["collected_at"] == "2026-10-10T13:35:00+00:00"
        assert source["created_time"] == "2020-01-02T08:04:05+00:00"
        assert metadata.get(conn, access, iid)["sources"] == [source]
        assert tuple(conn.execute("SELECT mtime,imported_at,sha256 FROM images WHERE id=?", (iid,)).fetchone()) == tuple(before)
        for field, value in [("collected_at", "2026-10-10T21:35:00"), ("url", "javascript:alert(1)")]:
            with pytest.raises(ServiceError):
                metadata.add_source(conn, access, iid, **{field: value})
        assert len(metadata.get(conn, access, iid)["sources"]) == 1


def test_import_records_original_name_and_time_without_source_path(catalog, tmp_path):
    root = tmp_path / "managed"; root.mkdir()
    source = tmp_path / "incoming"; source.mkdir()
    original = (catalog.roots / "north").glob("*.png")
    (source / "Original name?.png").write_bytes(next(original).read_bytes())
    before = file_inventory(source)
    access = AccessContext(catalog.users["curator"])
    with db.open_db(catalog.cfg.db_path) as conn:
        lid = libsvc.create_library(conn, "managed", str(root), mode="managed")
        plan = import_plans.create_plan(conn, access, lid, str(source), mode="copy")
        result = import_plans.execute_plan(conn, catalog.cfg, access, plan["plan_id"])
        assert result["state"] == "succeeded"
        iid = conn.execute("SELECT id FROM images WHERE library_id=?", (lid,)).fetchone()[0]
        records = metadata.get(conn, access, iid)["sources"]
        assert len(records) == 1 and records[0]["original_filename"] == "Original name?.png"
        assert records[0]["origin"] == "imported" and records[0]["collected_at"].endswith("+00:00")
        assert str(source) not in json.dumps(records)
        import_plans.execute_plan(conn, catalog.cfg, access, plan["plan_id"])
        indexer.scan_library(conn, catalog.cfg, lid)
        assert metadata.get(conn, access, iid)["sources"] == records
    assert file_inventory(source) == before


def test_tag_suggestions_do_not_overwrite_manual_evidence(catalog):
    access = AccessContext(catalog.users["curator"])
    iid = catalog.images["n01"]
    with db.open_db(catalog.cfg.db_path) as conn:
        ops.add_tags(conn, iid, ["confirmed"], access.user_id)
        claim = metadata.add_tag_claim(conn, access, iid, "candidate", origin="suggested", source_ref="offline fixture")
        assert claim["confirmed"] == 0 and claim["origin"] == "suggested"
        assert conn.execute("SELECT COUNT(*) FROM image_tags WHERE image_id=? AND tag_id=?", (iid,claim["tag_id"])).fetchone()[0] == 0
        metadata.confirm_tag_claim(conn, access, claim["id"])
        metadata.confirm_tag_claim(conn, access, claim["id"])
        claims = metadata.get(conn, access, iid)["tag_claims"]
        assert [(row["origin"],row["confirmed"]) for row in claims if row["name"] == "confirmed"] == [("manual",1)]
        approved = [row for row in claims if row["name"] == "candidate"]
        assert len(approved) == 1 and approved[0]["confirmed_by"] == access.user_id
        assert conn.execute("SELECT COUNT(*) FROM image_tags WHERE image_id=? AND tag_id=?", (iid,claim["tag_id"])).fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["tag", "artist"])
def test_merge_preview_moves_only_selected_links_and_preserves_target(catalog, kind):
    access = AccessContext(catalog.users["curator"])
    chosen, unchanged = catalog.images["n01"], catalog.images["n02"]
    table, column = ("image_tags", "tag_id") if kind == "tag" else ("image_artists", "artist_id")
    with db.open_db(catalog.cfg.db_path) as conn:
        if kind == "tag":
            source, target = [ops.ensure_tag(conn, name) for name in ("source", "target")]; conn.commit()
            for iid in (chosen, unchanged): ops.add_tags(conn, iid, ["source"], access.user_id)
            ops.add_tags(conn, chosen, ["target"], catalog.users["reader"])
        else:
            source, target = [ops.create_artist(conn, name)["id"] for name in ("source", "target")]
            for iid in (chosen, unchanged): ops.link_artist(conn, iid, source)
            ops.link_artist(conn, chosen, target)
        before_target = tuple(conn.execute(f"SELECT * FROM {table} WHERE image_id=? AND {column}=?", (chosen,target)).fetchone())
        preview = entity_edits.preview_merge(conn, access, kind, source, target, [chosen])
        assert preview["image_ids"] == [chosen] and preview["count"] == 1
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (source,)).fetchone()[0] == 2
        result = entity_edits.execute(conn, access, preview["id"])
        assert entity_edits.execute(conn, access, preview["id"]) == result
        assert list(map(tuple, conn.execute(f"SELECT image_id FROM {table} WHERE {column}=? ORDER BY image_id", (source,)))) == [(unchanged,)]
        assert tuple(conn.execute(f"SELECT * FROM {table} WHERE image_id=? AND {column}=?", (chosen,target)).fetchone()) == before_target
        assert conn.execute(f"SELECT COUNT(*) FROM {kind}s WHERE id IN (?,?)", (source,target)).fetchone()[0] == 2


def test_stale_merge_and_revoked_authority_do_not_partially_apply(catalog):
    access = AccessContext(catalog.users["curator"])
    ids = [catalog.images["n01"],catalog.images["n02"]]
    with db.open_db(catalog.cfg.db_path) as conn:
        for iid in ids: ops.add_tags(conn, iid, ["from"], access.user_id)
        source, target = ops.ensure_tag(conn, "from"), ops.ensure_tag(conn, "to"); conn.commit()
        preview = entity_edits.preview_merge(conn, access, "tag", source, target, ids)
        ops.remove_tags(conn, ids[0], ["from"])
        before = metadata_snapshot(conn)
        with pytest.raises(ServiceError) as caught:
            entity_edits.execute(conn, access, preview["id"])
        assert caught.value.code == "metadata_conflict" and metadata_snapshot(conn) == before
        preview = entity_edits.preview_merge(conn, access, "tag", source, target, [ids[1]])
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (access.user_id,)); conn.commit()
        with pytest.raises(ServiceError): entity_edits.execute(conn, access, preview["id"])
        assert metadata_snapshot(conn) == before


def test_upgrade_backfills_manual_claims_without_inventing_sources_or_workflow(tmp_path):
    path = old_database(tmp_path / "old.db")
    with sqlite3.connect(path) as conn: before = metadata_snapshot(conn)
    with db.open_db(path) as conn:
        assert metadata_snapshot(conn) == before
        assert list(map(tuple, conn.execute("SELECT image_id,tag_id,origin,confirmed,created_by,created_at FROM tag_claims"))) == [(31,53,"manual",1,7,"2026-10-01T00:00:00+00:00")]
        assert conn.execute("SELECT COUNT(*) FROM image_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM image_workflow").fetchone()[0] == 0


def test_cli_api_read_write_and_json_export_agree(catalog):
    iid = catalog.images["n01"]
    marked = cli(catalog.cfg, "organize", str(iid), "--state", "done", "--user", "reader")
    assert marked.returncode == 0 and json.loads(marked.stdout)["image_ids"] == [iid]
    exported = cli(catalog.cfg, "metadata", "show", str(iid), "--user", "reader")
    assert exported.returncode == 0
    payload = json.loads(exported.stdout)
    assert payload["workflow"]["state"] == "done" and payload["schema"] == "picshelf.image-metadata/1"
    with TestClient(create_app(catalog.cfg)) as client:
        client.post("/api/v1/auth/login", json={"username":"reader","password":catalog.password})
        result = client.get(f"/api/v1/images/{iid}/metadata")
        assert result.status_code == 200 and result.json() == payload
        assert client.get(f"/api/v1/images/{catalog.images['s01']}/metadata").status_code == 403


def test_alias_search_has_hand_selected_results_and_does_not_confirm_suggestions(catalog):
    admin = AccessContext(catalog.users["curator"])
    first, second = catalog.images["n01"], catalog.images["n02"]
    with db.open_db(catalog.cfg.db_path) as conn:
        tag = ops.ensure_tag(conn, "Café artwork"); conn.commit()
        ops.add_tags(conn, first, ["Café artwork"], admin.user_id)
        metadata.add_tag_claim(conn, admin, second, "Café artwork", origin="suggested")
        artist = ops.create_artist(conn, "An unrelated creator")["id"]
        ops.link_artist(conn, first, artist)
        for kind, entity, alias in [("tag",tag,"分类: 原创"),("artist",artist,"Café artist")]:
            preview = entity_edits.preview_alias(conn, admin, kind, entity, alias)
            entity_edits.execute(conn, admin, preview["id"])
        for query in ['tag:" cafe\u0301  ARTWORK "', 'tag:"分类 : 原创"', 'artist:"cafe\u0301 ARTIST"']:
            result = search.search_images(conn, catalog.users["reader"], [catalog.libraries["north"]], query=query)
            assert {row["id"] for row in result["items"]} == {first}, query
        assert ops.list_tags(conn, q="分类:原创", allowed_library_ids=[catalog.libraries["north"]])[0]["name_aliases"] == ["分类: 原创"]
        assert ops.get_artist(conn, artist)["name_aliases"] == ["Café artist"]
    linked = cli(catalog.cfg, "artist", "link", str(second), "cafe\u0301 ARTIST")
    assert linked.returncode == 0, linked.stderr
    with db.open_db(catalog.cfg.db_path) as conn:
        assert list(map(tuple, conn.execute("SELECT image_id FROM image_artists WHERE artist_id=? ORDER BY image_id", (artist,)))) == [(first,), (second,)]
        assert ops.remove_tags(conn, first, ["分类 : 原创"]) == 1
        assert conn.execute("SELECT COUNT(*) FROM image_tags WHERE image_id=? AND tag_id=?", (first,tag)).fetchone()[0] == 0


def test_numeric_tag_name_does_not_remove_another_tag_with_that_id(catalog):
    iid = catalog.images["n01"]
    with db.open_db(catalog.cfg.db_path) as conn:
        other = ops.ensure_tag(conn, "other tag"); conn.commit()
        ops.add_tags(conn, iid, ["other tag", str(other)], catalog.users["curator"])
        assert ops.remove_tags(conn, iid, [str(other)]) == 1
        assert conn.execute("SELECT COUNT(*) FROM image_tags WHERE image_id=? AND tag_id=?", (iid,other)).fetchone()[0] == 1


def test_removed_tag_evidence_cannot_be_confirmed_again(catalog):
    access = AccessContext(catalog.users["curator"])
    iid = catalog.images["n01"]
    with db.open_db(catalog.cfg.db_path) as conn:
        claim = metadata.add_tag_claim(conn, access, iid, "imported label", origin="imported")
        metadata.confirm_tag_claim(conn, access, claim["id"])
        ops.remove_tags(conn, iid, ["imported label"])
        with pytest.raises(ServiceError) as caught:
            metadata.confirm_tag_claim(conn, access, claim["id"])
        assert caught.value.code == "metadata_conflict"
        assert conn.execute("SELECT COUNT(*) FROM image_tags WHERE image_id=? AND tag_id=?", (iid,claim["tag_id"])).fetchone()[0] == 0


def test_scan_recovers_provenance_after_interruption_before_import_scan(catalog, tmp_path):
    import subprocess
    import sys
    root = tmp_path / "target"; root.mkdir()
    access = AccessContext(catalog.users["curator"])
    with db.open_db(catalog.cfg.db_path) as conn:
        lid = libsvc.create_library(conn, "interrupted", str(root), mode="managed")
        preview = import_plans.create_plan(conn, access, lid, str(catalog.roots / "north"), mode="copy")
    script = """
import os,sys
from pathlib import Path
from picshelf import db,import_plans
from picshelf.access import AccessContext
from picshelf.config import Config
cfg=Config(Path(sys.argv[1])); conn=db.open_db(cfg.db_path)
original=import_plans._record_item
def record(*args,**kwargs):
    original(*args,**kwargs)
    if args[3]=='succeeded': os._exit(43)
import_plans._record_item=record
import_plans.execute_plan(conn,cfg,AccessContext(int(sys.argv[2])),sys.argv[3])
"""
    process = subprocess.run([sys.executable,"-c",script,str(catalog.cfg.data_dir),str(access.user_id),preview["plan_id"]], capture_output=True, timeout=10)
    assert process.returncode == 43
    with db.open_db(catalog.cfg.db_path) as conn:
        assert indexer.scan_library(conn, catalog.cfg, lid)["added"] == 1
        iid = conn.execute("SELECT id FROM images WHERE library_id=?", (lid,)).fetchone()[0]
        records = metadata.get(conn, access, iid)["sources"]
        assert len(records) == 1 and records[0]["origin"] == "imported"
        assert records[0]["original_filename"] == (next(root.glob("*.png"))).name
        assert records[0]["collected_at"].endswith("+00:00")


def test_provenance_and_workflow_survive_database_export_and_restore(catalog, tmp_path):
    iid = catalog.images["n01"]
    added = cli(catalog.cfg, "metadata", "source", str(iid), "--original-filename", "first.png", "--url", "https://example.org/source",
                "--collected-at", "2026-10-01T08:00:00+08:00", "--note", "Provided by the artist")
    assert added.returncode == 0, added.stderr
    assert cli(catalog.cfg, "organize", str(iid), "--state", "done", "--user", "reader").returncode == 0
    backup = tmp_path / "metadata-backup.zip"
    assert cli(catalog.cfg, "export", str(backup)).returncode == 0
    restored = Config(tmp_path / "restored")
    result = cli(restored, "import-backup", str(backup), "--force")
    assert result.returncode == 0, result.stderr
    exported = cli(restored, "metadata", "show", str(iid), "--user", "reader")
    assert exported.returncode == 0
    result = json.loads(exported.stdout)
    assert result["workflow"]["state"] == "done"
    assert [(row["origin"],row["original_filename"],row["url"],row["collected_at"],row["note"]) for row in result["sources"]] == [
        ("manual","first.png","https://example.org/source","2026-10-01T00:00:00+00:00","Provided by the artist")]
    assert all(row["origin"] == "manual" and row["confirmed"] == 1 for row in result["tag_claims"])


def test_api_confirmation_scope_bilingual_validation_and_literal_text(catalog):
    iid = catalog.images["n01"]
    note = '<img src=x onerror="window.bad=true">'
    with TestClient(create_app(catalog.cfg)) as client:
        client.post("/api/v1/auth/login", json={"username":"curator","password":catalog.password})
        response = client.post(f"/api/v1/images/{iid}/sources", json={"note": note})
        assert response.status_code == 200 and response.json()["note"] == note
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda lang: client.post(f"/api/v1/images/{iid}/sources",
                           headers={"Accept-Language":lang}, json={"collected_at":"2026-10-10T12:00:00"}), ["zh", "en"]))
        assert [r.status_code for r in results] == [400,400]
        assert results[0].json()["detail"].startswith("时间必须")
        assert results[1].json()["detail"].startswith("Time must")
        with db.open_db(catalog.cfg.db_path) as conn:
            tag = ops.ensure_tag(conn, "catalog name"); conn.commit()
        plan = client.post("/api/v1/metadata/aliases/preview", json={"kind":"tag","entity_id":tag,"alias":"Alternate label"}).json()
        refused = client.post(f"/api/v1/metadata/plans/{plan['id']}/execute", json={})
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "confirmation_required"
        assert client.post(f"/api/v1/metadata/plans/{plan['id']}/execute", json={"confirm":True}).status_code == 200
        client.post("/api/v1/auth/login", json={"username":"reader","password":catalog.password})
        assert client.post(f"/api/v1/images/{iid}/sources", json={"note":"No edit grant"}).status_code == 403
        assert client.post("/api/v1/metadata/aliases/preview", json={"kind":"tag","entity_id":tag,"alias":"No authority"}).status_code == 403
