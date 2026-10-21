"""Batch targets, permissions and undo expectations from a fixed two-library catalog."""
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, batches, db, indexer, ops
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import draw_image,file_inventory
from tests.scenario_support import build_catalog,cli,metadata_snapshot


@pytest.fixture
def catalog(tmp_path):
    return build_catalog(tmp_path)


def editor(conn,catalog):
    uid = catalog.users["reader"]
    conn.execute("UPDATE library_grants SET access='edit' WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(uid,));conn.commit()
    return AccessContext(uid)


def tagged(conn,name):
    return {row[0] for row in conn.execute("SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name=?",(name,))}


def test_preview_bulk_reads_keep_order_provenance_and_field_versions(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        ids = [catalog.images[key] for key in ("n08", "n04", "n01", "n12")]
        ops.add_tags(conn, ids[0], ["Provenance"], catalog.users["curator"])
        ops.set_rating(conn, ids[1], access.user_id, 0)
        actions = [{"kind":"tags_remove", "names":["Provenance"]}, {"kind":"rating", "value":3},
                   {"kind":"favorite", "value":True}, {"kind":"workflow", "value":"done"},
                   {"kind":"artist_add", "artist_id":ops.create_artist(conn, "Snapshot artist")["id"]},
                   {"kind":"album_add", "album_id":ops.create_album(conn, "Snapshot album",catalog.libraries["north"])["id"]}]
        for action in actions:
            expected = [(iid,batches._value(conn,iid,action,access.user_id),batches._version(conn,iid,action,access.user_id)) for iid in ids]
            preview = batches.preview(conn,access,action,image_ids=ids)
            assert [(row["image_id"],row["before_value"],row["before_version"]) for row in preview["items"]] == expected
            summary = batches.get(conn,access,preview["id"],include_items=False)
            assert "items" not in summary and "offset" not in summary
            assert summary["counts"] == preview["counts"]
            assert summary["total"] == 4


def test_large_preview_queries_are_bounded_and_status_omits_journal(conn, library, admin):
    from tests.datasets import metadata_rows
    rows = list(metadata_rows(2000,library["id"]))
    fields = list(rows[0])
    conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                     [list(row.values()) for row in rows])
    conn.commit()
    access = AccessContext(admin["id"])
    statements = []
    conn.set_trace_callback(statements.append)
    preview = batches.preview(conn,access,{"kind":"tags_add","names":["Grouped"]},query={"library_id":library["id"],"sort":"path"})
    conn.set_trace_callback(None)
    assert preview["total"] == 2000
    # Reads grow with bounded groups of IDs, rather than several queries per image.
    reads = [sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(reads) < 60
    statements.clear(); conn.set_trace_callback(statements.append)
    summary = batches.get(conn,access,preview["id"],include_items=False)
    conn.set_trace_callback(None)
    assert len(json.dumps(summary)) < 1500
    assert not any("before_value" in sql for sql in statements)
    assert [item["image_id"] for item in batches.get(conn,access,preview["id"],offset=1990)["items"]] == list(range(1991,2001))


def test_selected_ids_are_fixed_retry_is_idempotent_and_undo_restores_relations(catalog):
    selected = [catalog.images[key] for key in ("n01","n05","n12")]
    files = file_inventory(catalog.roots)
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        before = metadata_snapshot(conn)
        preview = batches.preview(conn,access,{"kind":"tags_add","names":["Selection only"]},image_ids=selected,request_key="selected-three")
        assert preview["total"] == 3 and tagged(conn,"Selection only") == set()
        assert batches.preview(conn,access,{"kind":"tags_add","names":["Selection only"]},image_ids=selected,request_key="selected-three")["id"] == preview["id"]
        first = batches.execute(conn,catalog.cfg,access,preview["id"])
        assert first["state"] == "succeeded" and first["counts"]["succeeded"] == 3
        assert tagged(conn,"Selection only") == set(selected)
        versions = [tuple(row) for row in conn.execute("SELECT * FROM metadata_versions ORDER BY image_id,field_key")]
        assert batches.execute(conn,catalog.cfg,access,preview["id"])["counts"] == first["counts"]
        assert [tuple(row) for row in conn.execute("SELECT * FROM metadata_versions ORDER BY image_id,field_key")] == versions
        undone = batches.undo(conn,catalog.cfg,access,preview["id"])
        assert undone["state"] == "undone" and undone["counts"]["undone"] == 3
        assert tagged(conn,"Selection only") == set()
        for table,rows in before.items():
            if table != "tags":
                assert metadata_snapshot(conn)[table] == rows
    assert file_inventory(catalog.roots) == files


def test_all_matching_snapshot_excludes_later_images(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        preview = batches.preview(conn,access,{"kind":"favorite","value":True},query={"library_id":catalog.libraries["north"],"sort":"path"})
        assert preview["total"] == 12
        draw_image(catalog.roots / "north" / "later.png",101010)
        indexer.scan_library(conn,catalog.cfg,catalog.libraries["north"])
        result = batches.execute(conn,catalog.cfg,access,preview["id"])
        assert result["total"] == 12
        observed = {r[0] for r in conn.execute("SELECT image_id FROM favorites WHERE user_id=?",(access.user_id,))}
        expected = {catalog.images[f"n{n:02}"] for n in range(1,13)}
        assert observed == expected


def test_known_scope_and_input_errors_do_not_write_part_of_small_batch(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        before = metadata_snapshot(conn)
        with pytest.raises(ServiceError):
            batches.preview(conn,access,{"kind":"tags_add","names":["Must not appear"]},image_ids=[catalog.images["n01"],catalog.images["s01"]])
        with pytest.raises(ServiceError):
            batches.preview(conn,access,{"kind":"rating","value":11},image_ids=[catalog.images["n01"]])
        assert metadata_snapshot(conn) == before
        preview = batches.preview(conn,access,{"kind":"rating","value":4},image_ids=[catalog.images["n01"],catalog.images["n02"]])
        ops.set_rating(conn,catalog.images["n02"],access.user_id,6)
        before = metadata_snapshot(conn)
        with pytest.raises(ServiceError):
            batches.execute(conn,catalog.cfg,access,preview["id"])
        assert metadata_snapshot(conn) == before


def test_undo_preserves_later_edits_including_change_back_and_other_users(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        ids = [catalog.images[key] for key in ("n01","n02","n03")]
        original = {iid:conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(iid,access.user_id)).fetchone() for iid in ids}
        before_others = [tuple(r) for r in conn.execute("SELECT * FROM ratings WHERE user_id!=?",(access.user_id,))]
        preview = batches.preview(conn,access,{"kind":"rating","value":9},image_ids=ids)
        batches.execute(conn,catalog.cfg,access,preview["id"])
        stamp = conn.execute("SELECT updated_at FROM ratings WHERE image_id=? AND user_id=?",(ids[1],access.user_id)).fetchone()[0]
        ops.set_rating(conn,ids[0],access.user_id,3)
        ops.set_rating(conn,ids[1],access.user_id,2)
        ops.set_rating(conn,ids[1],access.user_id,9)
        conn.execute("UPDATE ratings SET updated_at=? WHERE image_id=? AND user_id=?",(stamp,ids[1],access.user_id));conn.commit()
        ops.set_favorite(conn,ids[2],access.user_id,True)
        undone = batches.undo(conn,catalog.cfg,access,preview["id"])
        assert undone["counts"]["undo_conflict"] == 2 and undone["counts"]["undone"] == 1
        assert [conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(iid,access.user_id)).fetchone()[0] for iid in ids[:2]] == [3,9]
        actual = conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(ids[2],access.user_id)).fetchone()
        assert (actual[0] if actual else None) == (original[ids[2]][0] if original[ids[2]] else None)
        assert [tuple(r) for r in conn.execute("SELECT * FROM ratings WHERE user_id!=?",(access.user_id,))] == before_others
        assert conn.execute("SELECT 1 FROM favorites WHERE image_id=? AND user_id=?",(ids[2],access.user_id)).fetchone()


@pytest.mark.parametrize("kind",["tags_remove","artist_add","album_add","workflow"])
def test_association_and_workflow_batches_restore_exact_prior_membership(catalog,kind):
    ids = [catalog.images["n04"],catalog.images["n06"]]
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        action = {"kind":kind}
        if kind == "tags_remove":
            for iid in ids:ops.add_tags(conn,iid,["Keep provenance"],catalog.users["curator"])
            action["names"] = ["Keep provenance"]
        elif kind == "artist_add":action["artist_id"] = ops.create_artist(conn,"Batch creator")["id"]
        elif kind == "album_add":action["album_id"] = ops.create_album(conn,"Batch album",library_id=catalog.libraries["north"])["id"]
        else:action["value"] = "done"
        before = metadata_snapshot(conn)
        claims = [tuple(r) for r in conn.execute("SELECT * FROM tag_claims ORDER BY id")]
        preview = batches.preview(conn,access,action,image_ids=ids)
        batches.execute(conn,catalog.cfg,access,preview["id"])
        assert batches.undo(conn,catalog.cfg,access,preview["id"])["counts"]["undone"] == 2
        assert metadata_snapshot(conn) == before
        assert [tuple(r) for r in conn.execute("SELECT * FROM tag_claims ORDER BY id")] == claims


def test_owner_and_current_authority_checked_for_get_execute_and_undo(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        preview = batches.preview(conn,access,{"kind":"tags_add","names":["Authority"]},image_ids=[catalog.images["n01"]])
        with pytest.raises(ServiceError) as error:batches.get(conn,AccessContext(catalog.users["outsider"]),preview["id"])
        assert error.value.status == 404
        batches.execute(conn,catalog.cfg,access,preview["id"])
        conn.execute("DELETE FROM user_groups WHERE user_id=?",(access.user_id,));conn.commit()
        for function,args in ((batches.get,(conn,access,preview["id"])),(batches.undo,(conn,catalog.cfg,access,preview["id"]))):
            with pytest.raises(ServiceError):function(*args)
        assert tagged(conn,"Authority") == {catalog.images["n01"]}


def test_cancel_before_execution_is_idempotent_and_explicit_retry_resumes(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        ids = [catalog.images["n01"],catalog.images["n02"]]
        preview = batches.preview(conn,access,{"kind":"tags_add","names":["Resumed"]},image_ids=ids)
        assert batches.cancel(conn,access,preview["id"])["state"] == "cancelled"
        assert batches.execute(conn,catalog.cfg,access,preview["id"])["state"] == "cancelled"
        assert tagged(conn,"Resumed") == set()
        assert batches.execute(conn,catalog.cfg,access,preview["id"],retry=True)["counts"]["succeeded"] == 2


def large_selection(conn,catalog):
    from tests.datasets import metadata_rows
    # Generated metadata-only D05 records; this test does not claim image decode coverage.
    conn.executemany("INSERT INTO images(library_id,rel_path,filename,ext,size_bytes,width,height,mtime,sha256,imported_at) "
                     "VALUES(:library_id,:rel_path,:filename,:ext,:size_bytes,:width,:height,:mtime,:sha256,:imported_at)",
                     [{**row,"rel_path":"scale/"+row["filename"]} for row in metadata_rows(225,catalog.libraries["north"])])
    conn.commit()
    return [r[0] for r in conn.execute("SELECT id FROM images WHERE rel_path LIKE 'scale/%' ORDER BY id")]


def test_large_batch_cancel_counts_match_committed_chunks_and_resume(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        ids = large_selection(conn,catalog)
        assert len(ids) == 225
        preview = batches.preview(conn,access,{"kind":"tags_add","names":["Chunk selection"]},image_ids=ids)
        original = metadata_snapshot(conn)
        def cancel_after_commit(result):batches.cancel(conn,access,preview["id"])
        stopped = batches.execute(conn,catalog.cfg,access,preview["id"],progress=cancel_after_commit)
        assert stopped["state"] == "cancelled"
        assert stopped["counts"]["succeeded"] == 100 and stopped["counts"]["pending"] == 125
        assert tagged(conn,"Chunk selection") == set(ids[:100])
        resumed = batches.execute(conn,catalog.cfg,access,preview["id"],retry=True)
        assert resumed["counts"]["succeeded"] == 225 and tagged(conn,"Chunk selection") == set(ids)
        stopped_undo = batches.undo(conn,catalog.cfg,access,preview["id"],progress=cancel_after_commit)
        assert stopped_undo["state"] == "cancelled" and stopped_undo["counts"]["undone"] == 100
        assert tagged(conn,"Chunk selection") == set(ids[100:])
        assert batches.undo(conn,catalog.cfg,access,preview["id"])["counts"]["undone"] == 225
        assert tagged(conn,"Chunk selection") == set()
        assert metadata_snapshot(conn)["image_tags"] == original["image_tags"]


def test_process_exit_after_chunk_recovers_only_pending_items(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        ids = large_selection(conn,catalog)
        preview = batches.preview(conn,access,{"kind":"favorite","value":True},image_ids=ids)
    script = """
import os,sys
from pathlib import Path
from picshelf import batches,db
from picshelf.access import AccessContext
from picshelf.config import Config
cfg=Config(Path(sys.argv[1]))
with db.open_db(cfg.db_path) as conn:
 batches.execute(conn,cfg,AccessContext(int(sys.argv[2])),sys.argv[3],progress=lambda _:os._exit(47))
"""
    crashed = subprocess.run([sys.executable,"-c",script,str(catalog.cfg.data_dir),str(catalog.users["reader"]),preview["id"]],capture_output=True,text=True,timeout=30)
    assert crashed.returncode == 47, crashed.stderr
    with db.open_db(catalog.cfg.db_path) as conn:
        state = batches.get(conn,access,preview["id"])
        assert state["state"] == "interrupted" and state["counts"]["succeeded"] == 100
        before = [tuple(r) for r in conn.execute("SELECT * FROM favorites WHERE user_id=? AND image_id<=?",(access.user_id,ids[99]))]
        batches.queue(conn,access,preview["id"],retry=True)
    result = cli(catalog.cfg,"worker","--once")
    assert result.returncode == 0, result.stderr
    with db.open_db(catalog.cfg.db_path) as conn:
        state = batches.get(conn,access,preview["id"])
        assert state["state"] == "succeeded" and state["counts"]["succeeded"] == 225
        assert [tuple(r) for r in conn.execute("SELECT * FROM favorites WHERE user_id=? AND image_id<=?",(access.user_id,ids[99]))] == before


def test_small_batch_failure_rolls_back_all_metadata(catalog,monkeypatch):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        ids = [catalog.images["n01"],catalog.images["n02"]]
        preview = batches.preview(conn,access,{"kind":"tags_add","names":["Atomic batch"]},image_ids=ids)
        before = metadata_snapshot(conn)
        write = batches._write
        def fail_second(c,iid,action,owner):
            if iid == ids[1]:raise ServiceError("injected","Controlled storage failure",next_step="Retry")
            return write(c,iid,action,owner)
        with monkeypatch.context() as patch:
            patch.setattr(batches,"_write",fail_second)
            with pytest.raises(ServiceError):batches.execute(conn,catalog.cfg,access,preview["id"])
        assert metadata_snapshot(conn) == before
        assert batches.get(conn,access,preview["id"])["counts"]["pending"] == 2
        assert batches.execute(conn,catalog.cfg,access,preview["id"],retry=True)["counts"]["succeeded"] == 2


def test_target_rename_and_request_key_changes_require_new_preview(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = editor(conn,catalog)
        tag = ops.ensure_tag(conn,"Planned label");conn.commit()
        preview = batches.preview(conn,access,{"kind":"tags_add","names":["Planned label"]},image_ids=[catalog.images["n01"]],request_key="same-request")
        with pytest.raises(ServiceError):
            batches.preview(conn,access,{"kind":"tags_add","names":["Different"]},image_ids=[catalog.images["n01"]],request_key="same-request")
        ops.rename_tag(conn,tag,"Renamed elsewhere")
        with pytest.raises(ServiceError):batches.execute(conn,catalog.cfg,access,preview["id"])
        assert tagged(conn,"Planned label") == set() and tagged(conn,"Renamed elsewhere") == set()


def test_api_cli_preview_execution_and_inbox_use_same_selected_set(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:editor(conn,catalog)
    ids=[catalog.images["n01"],catalog.images["n12"]]
    result=cli(catalog.cfg,"batch","preview",*map(str,ids),"--action",json.dumps({"kind":"workflow","value":"done"}),"--user","reader")
    assert result.returncode==0,result.stderr
    preview=json.loads(result.stdout)
    assert cli(catalog.cfg,"batch","execute",preview["id"],"--user","reader").returncode!=0
    with TestClient(create_app(catalog.cfg),headers={"Origin":"http://testserver"}) as client:
        assert client.post("/api/v1/auth/login",json={"username":"reader","password":catalog.password}).status_code==200
        endpoint=f"/api/v1/batches/{preview['id']}/execute"
        assert client.post(endpoint,json={}).status_code==400
        applied=client.post(endpoint,json={"confirm":True}).json()
        assert applied["counts"]["succeeded"]==2
        done=client.get("/api/v1/images",params={"workflow":"done"}).json()
        assert {row["id"] for row in done["items"]}==set(ids)
        inbox=client.get("/api/v1/images",params={"workflow":"inbox"}).json()
        assert {row["id"] for row in inbox["items"]}=={catalog.images[f"n{n:02}"] for n in range(2,12)}
        assert client.post(f"/api/v1/batches/{preview['id']}/undo",json={"confirm":True}).json()["counts"]["undone"]==2
    assert json.loads(cli(catalog.cfg,"batch","show",preview["id"],"--user","reader").stdout)["state"]=="undone"


def test_removed_album_cover_is_hidden_and_undo_preserves_later_cover_choice(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access=editor(conn,catalog)
        first,second=catalog.images["n01"],catalog.images["n02"]
        album=ops.create_album(conn,"Cover selection",library_id=catalog.libraries["north"])["id"]
        ops.album_add_images(conn,album,[first,second]);ops.update_album(conn,album,cover_image_id=first)
        preview=batches.preview(conn,access,{"kind":"album_remove","album_id":album},image_ids=[first])
        batches.execute(conn,catalog.cfg,access,preview["id"])
        assert ops.get_album(conn,album)["cover_image_id"] is None
        assert next(row for row in ops.list_albums(conn) if row["id"]==album)["cover_image_id"] is None
        ops.update_album(conn,album,cover_image_id=second)
        batches.undo(conn,catalog.cfg,access,preview["id"])
        assert ops.get_album(conn,album)["cover_image_id"]==second


def test_zero_rating_can_be_undone_back_to_unrated_without_touching_favorite(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access=editor(conn,catalog);iid=catalog.images["n08"]
        ops.set_rating(conn,iid,access.user_id,None);ops.set_favorite(conn,iid,access.user_id,True)
        preview=batches.preview(conn,access,{"kind":"rating","value":0},image_ids=[iid])
        assert batches.execute(conn,catalog.cfg,access,preview["id"])["counts"]["succeeded"]==1
        assert conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(iid,access.user_id)).fetchone()[0]==0
        batches.undo(conn,catalog.cfg,access,preview["id"])
        assert conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(iid,access.user_id)).fetchone() is None
        assert conn.execute("SELECT 1 FROM favorites WHERE image_id=? AND user_id=?",(iid,access.user_id)).fetchone()


def test_expiry_and_mid_batch_permission_revocation_preserve_unfinished_values(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access=editor(conn,catalog)
        ids=large_selection(conn,catalog)
        expired=batches.preview(conn,access,{"kind":"rating","value":4},image_ids=ids[:2])
        conn.execute("UPDATE metadata_batches SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(expired["id"],));conn.commit()
        with pytest.raises(ServiceError) as caught:batches.execute(conn,catalog.cfg,access,expired["id"])
        assert caught.value.code=="batch_expired"
        preview=batches.preview(conn,access,{"kind":"rating","value":5},image_ids=ids)
        def revoke_after_chunk(_):
            conn.execute("DELETE FROM library_grants WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(access.user_id,));conn.commit()
        with pytest.raises(ServiceError):batches.execute(conn,catalog.cfg,access,preview["id"],progress=revoke_after_chunk)
        observed={r[0] for r in conn.execute("SELECT image_id FROM ratings WHERE user_id=? AND value=5",(access.user_id,))}
        assert observed.intersection(ids)==set(ids[:100])
        state=batches.get(conn,AccessContext(catalog.users["curator"]),preview["id"])
        assert state["state"]=="interrupted" and state["counts"]["pending"]==125


@pytest.mark.parametrize("query",[{"sort":"undefined"},{"favorites":"false"},{"limit":2},{"offset":1},{"library_id":True},
    {"library_id":2**63},{"album_id":2**63},{"artist_id":2**63},{"workflow":"unknown"}])
def test_invalid_query_scope_never_turns_into_a_broader_selection(catalog,query):
    with db.open_db(catalog.cfg.db_path) as conn:
        access=editor(conn,catalog)
        before=metadata_snapshot(conn)
        with pytest.raises(ServiceError):batches.preview(conn,access,{"kind":"favorite","value":True},query=query)
        assert metadata_snapshot(conn)==before


def test_invalid_nested_batch_actions_are_client_errors_without_writes(catalog):
    actions = [
        {'kind': 'artist_add', 'artist_id': 2**63},
        {'kind': 'album_add', 'album_id': 2**63},
        {'kind': []}, {'kind': {}},
    ]
    with db.open_db(catalog.cfg.db_path) as conn, TestClient(create_app(catalog.cfg), headers={'Origin': 'http://testserver'}) as client:
        before = metadata_snapshot(conn)
        client.post('/api/v1/auth/login', json={'username': 'curator', 'password': catalog.password})
        for action in actions:
            response = client.post('/api/v1/batches/preview', json={'action': action, 'image_ids': [catalog.images['n01']]})
            assert response.status_code == 400
            assert response.json()['error']['code'] == 'batch_invalid'
        for field in ('library_id', 'album_id', 'artist_id'):
            response = client.post('/api/v1/batches/preview', json={'action': {'kind': 'favorite', 'value': True}, 'query': {field: 2**63}})
            assert response.status_code == 400
        with pytest.raises(ServiceError):
            batches.preview(conn, AccessContext(catalog.users['curator']), {'kind': 'favorite', 'value': True}, image_ids=[2**63])
        assert conn.execute('SELECT COUNT(*) FROM metadata_batches').fetchone()[0] == 0
        assert metadata_snapshot(conn) == before


def test_history_pages_only_batches_whose_libraries_are_still_visible(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['reader'])
        gid = conn.execute('SELECT group_id FROM user_groups WHERE user_id=?', (access.user_id,)).fetchone()[0]
        conn.execute("INSERT INTO library_grants VALUES(?,?,'view')", (gid, catalog.libraries['south']))
        conn.commit()
        action = {'kind': 'workflow', 'value': 'done'}
        visible = [batches.preview(conn, access, action, image_ids=[catalog.images['n01']])['id'] for _ in range(3)]
        hidden = [batches.preview(conn, access, action, image_ids=[catalog.images['s01']])['id'] for _ in range(4)]
        mixed = batches.preview(conn, access, action, image_ids=[catalog.images['n01'], catalog.images['s01']])['id']
        conn.execute('DELETE FROM library_grants WHERE group_id=? AND library_id=?', (gid, catalog.libraries['south']))
        conn.commit()
        first = batches.list_batches(conn, access, limit=2)
        assert [item['id'] for item in first['items']] == visible[::-1][:2]
        assert first['next_offset'] == 2
        last = batches.list_batches(conn, access, offset=first['next_offset'], limit=2)
        assert [item['id'] for item in last['items']] == visible[:1]
        assert last['next_offset'] is None
        admin = AccessContext(catalog.users['curator'])
        assert {item['id'] for item in batches.list_batches(conn, admin)['items']} == set(visible + hidden + [mixed])
        assert batches.list_batches(conn, AccessContext(catalog.users['outsider']))['items'] == []
        conn.execute('DELETE FROM library_grants WHERE group_id=?', (gid,))
        conn.commit()
        assert batches.list_batches(conn, access)['items'] == []


def test_history_pagination_has_a_stable_order_when_timestamps_match(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        access = AccessContext(catalog.users['reader'])
        ids = [batches.preview(conn, access, {'kind': 'workflow', 'value': 'done'},
                               image_ids=[catalog.images['n01']])['id'] for _ in range(5)]
        conn.execute('UPDATE metadata_batches SET created_at=?', ('2026-01-01T00:00:00+00:00',))
        conn.commit()
        actual = []
        offset = 0
        while offset is not None:
            result = batches.list_batches(conn, access, limit=2, offset=offset)
            actual.extend(item['id'] for item in result['items'])
            offset = result['next_offset']
        assert actual == sorted(ids, reverse=True)
