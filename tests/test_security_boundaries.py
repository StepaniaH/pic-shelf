"""Authorization expectations from roles and current grants, never response IDs."""
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db, import_plans, jobs, ops, source_access
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import draw_image, file_inventory
from tests.scenario_support import build_catalog, cli


@pytest.fixture
def catalog(tmp_path):
    return build_catalog(tmp_path)


def client_for(catalog, username="reader", *, base_url="http://testserver"):
    client = TestClient(create_app(catalog.cfg), base_url=base_url, headers={"Origin": base_url})
    response = client.post("/api/v1/auth/login", json={"username":username,"password":catalog.password})
    assert response.status_code == 200
    return client, response.json()["token"]


def test_group_membership_changes_preserve_other_grants_and_require_admin(catalog):
    reader, _ = client_for(catalog)
    admin, _ = client_for(catalog, 'curator')
    uid = catalog.users['reader']
    with db.open_db(catalog.cfg.db_path) as conn:
        original = {r[0] for r in conn.execute('SELECT group_id FROM user_groups WHERE user_id=?', (uid,))}
        gid = conn.execute("INSERT INTO groups(name,capabilities,created_at) VALUES('Reviewers','[]',?)", (db.utcnow(),)).lastrowid
        conn.commit()
    endpoint = f'/api/v1/groups/{gid}/members/{uid}'
    with reader, admin:
        assert reader.put(endpoint).status_code == 403
        assert reader.delete(endpoint).status_code == 403
        for _ in range(2):
            assert admin.put(endpoint).status_code == 200
        with db.open_db(catalog.cfg.db_path) as conn:
            assert {r[0] for r in conn.execute('SELECT group_id FROM user_groups WHERE user_id=?', (uid,))} == original | {gid}
            assert not auth.has_cap(conn, auth.get_user(conn, uid), 'admin')
        assert admin.put(f'/api/v1/groups/{gid}/members/999999').status_code == 404
        assert admin.put(f'/api/v1/groups/999999/members/{uid}').status_code == 404
        for _ in range(2):
            assert admin.delete(endpoint).status_code == 200
        with db.open_db(catalog.cfg.db_path) as conn:
            assert {r[0] for r in conn.execute('SELECT group_id FROM user_groups WHERE user_id=?', (uid,))} == original


def test_password_change_revokes_all_old_cookie_and_bearer_sessions(catalog):
    first, first_token = client_for(catalog)
    second, second_token = client_for(catalog)
    new_password = secrets.token_urlsafe(24)
    with first, second:
        assert first.post("/api/v1/auth/password", json={"old_password":catalog.password,"new_password":new_password}).status_code == 200
        assert first.get("/api/v1/auth/me").status_code == 401
        assert second.get("/api/v1/auth/me").status_code == 401
        for token in (first_token,second_token):
            assert second.get("/api/v1/auth/me", headers={"Authorization":"Bearer "+token}).status_code == 401
        response = second.post("/api/v1/auth/login", json={"username":"reader","password":new_password})
        assert response.status_code == 200 and second.get("/api/v1/auth/me").status_code == 200


def test_logout_revokes_exact_session_and_explicit_revoke_covers_others(catalog):
    first, first_token = client_for(catalog)
    second, _ = client_for(catalog)
    with first, second:
        assert first.post("/api/v1/auth/logout").status_code == 200
        assert second.get("/api/v1/auth/me", headers={"Authorization":"Bearer "+first_token}).status_code == 401
        assert second.get("/api/v1/auth/me").status_code == 200
        assert second.post("/api/v1/auth/revoke-sessions").status_code == 200
        assert second.get("/api/v1/auth/me").status_code == 401


def test_disable_then_enable_does_not_revive_old_session(catalog):
    reader, _ = client_for(catalog)
    admin, _ = client_for(catalog,"curator")
    uid = catalog.users["reader"]
    with reader, admin:
        assert admin.patch(f"/api/v1/users/{uid}",json={"disabled":True}).status_code == 200
        assert reader.get("/api/v1/auth/me").status_code == 401
        assert admin.patch(f"/api/v1/users/{uid}",json={"disabled":False}).status_code == 200
        assert reader.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("origin", ["https://unrelated.example", "http://testserver.evil.example", "null"])
def test_cross_site_cookie_mutation_is_rejected_without_changing_rating(catalog, origin):
    client, _ = client_for(catalog)
    iid, uid = catalog.images["n01"], catalog.users["reader"]
    with db.open_db(catalog.cfg.db_path) as conn:
        before = list(map(tuple,conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(iid,uid))))
    with client:
        response = client.post(f"/api/v1/images/{iid}/rating",headers={"Origin":origin},json={"value":10})
        assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_origin"
    with db.open_db(catalog.cfg.db_path) as conn:
        assert list(map(tuple,conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(iid,uid)))) == before


def test_cookie_requires_origin_but_bearer_scripts_work_without_it(catalog):
    client, token = client_for(catalog)
    iid = catalog.images["n01"]
    with client:
        del client.headers["Origin"]
        assert client.post(f"/api/v1/images/{iid}/rating",json={"value":1}).status_code == 403
        assert client.post(f"/api/v1/images/{iid}/rating",headers={"Authorization":"Bearer "+token},json={"value":1}).status_code == 200


def test_https_cookie_is_secure_and_untrusted_proxy_headers_do_not_change_http(catalog):
    with TestClient(create_app(catalog.cfg),base_url="https://gallery.example.test",headers={"Origin":"https://gallery.example.test"}) as client:
        response = client.post("/api/v1/auth/login",json={"username":"reader","password":catalog.password})
        cookie_is_secure = "; Secure" in response.headers["set-cookie"]
        assert cookie_is_secure
    with TestClient(create_app(catalog.cfg),headers={"Origin":"http://testserver"}) as client:
        response = client.post("/api/v1/auth/login",headers={"X-Forwarded-Proto":"https","X-Forwarded-Host":"unrelated.example"},
                               json={"username":"reader","password":catalog.password})
        assert response.status_code == 200 and "; Secure" not in response.headers["set-cookie"]
        assert client.get("/api/v1/auth/me").status_code == 200


def test_shared_name_changes_require_global_authority_and_view_is_not_edit(catalog):
    north, south = catalog.images["n01"], catalog.images["s01"]
    with db.open_db(catalog.cfg.db_path) as conn:
        for iid in (north,south): ops.add_tags(conn,iid,["Shared label"],catalog.users["curator"])
        tag = conn.execute("SELECT id FROM tags WHERE name='Shared label'").fetchone()[0]
        artist = ops.create_artist(conn,"Shared creator")["id"]
        for iid in (north,south): ops.link_artist(conn,iid,artist)
    client, _ = client_for(catalog)
    with client:
        assert client.patch(f"/api/v1/tags/{tag}",json={"name":"Unexpected rename"}).status_code == 403
        assert client.patch(f"/api/v1/artists/{artist}",json={"name":"Unexpected creator rename"}).status_code == 403
        assert client.post(f"/api/v1/images/{north}/tags",json={"names":["Not an editor"]}).status_code == 403
        with db.open_db(catalog.cfg.db_path) as conn:
            conn.execute("UPDATE library_grants SET access='edit' WHERE library_id=? AND group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",
                         (catalog.libraries["north"],catalog.users["reader"])); conn.commit()
        assert client.post(f"/api/v1/images/{north}/tags",json={"names":["Editor label"]}).status_code == 200
        assert client.patch(f"/api/v1/tags/{tag}",json={"name":"Still forbidden"}).status_code == 403
    with db.open_db(catalog.cfg.db_path) as conn:
        assert conn.execute("SELECT name FROM tags WHERE id=?",(tag,)).fetchone()[0] == "Shared label"
        assert conn.execute("SELECT name FROM artists WHERE id=?",(artist,)).fetchone()[0] == "Shared creator"


def test_media_is_not_reused_across_accounts_and_revoked_access(catalog):
    client, _ = client_for(catalog)
    iid = catalog.images["n01"]
    with client:
        for kind in ("original","thumb","preview"):
            response = client.get(f"/api/v1/media/{kind}/{iid}")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
        with db.open_db(catalog.cfg.db_path) as conn:
            conn.execute("DELETE FROM library_grants WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)", (catalog.users["reader"],)); conn.commit()
        assert client.get(f"/api/v1/media/thumb/{iid}").status_code == 403


def test_library_manager_needs_an_allowed_import_source_root(catalog, tmp_path):
    access = AccessContext(catalog.users["reader"])
    source = tmp_path / "incoming"; draw_image(source / "one.png",888)
    lid = catalog.libraries["north"]
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute("UPDATE library_grants SET access='manage' WHERE library_id=? AND group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(lid,access.user_id));conn.commit()
        with pytest.raises(ServiceError) as caught:
            import_plans.create_plan(conn, access, lid, str(source), mode="copy")
        assert caught.value.code == "import_source_forbidden"
        from picshelf import source_access
        admin = AccessContext(catalog.users["curator"])
        allowed = source_access.register(conn,admin,str(source))
        with pytest.raises(ServiceError):
            import_plans.create_plan(conn,access,lid,str(source),mode="move")
        preview = import_plans.create_plan(conn,access,lid,str(source),mode="copy")
        before = file_inventory(catalog.roots)
        source_access.revoke(conn,admin,allowed["id"])
        with pytest.raises(ServiceError):
            import_plans.execute_plan(conn,catalog.cfg,access,preview["plan_id"])
        assert file_inventory(catalog.roots) == before


def test_login_attempts_and_oversized_passwords_are_bounded(catalog):
    with TestClient(create_app(catalog.cfg),headers={"Origin":"http://testserver"}) as client:
        for _ in range(5):
            response = client.post("/api/v1/auth/login",json={"username":"reader","password":"incorrect fixture input"})
            assert response.status_code == 401
        limited = client.post("/api/v1/auth/login",json={"username":"reader","password":catalog.password})
        assert limited.status_code == 429 and 0 < int(limited.headers["retry-after"]) <= 60
        assert client.post("/api/v1/auth/login",json={"username":"reader","password":"x"*100000}).status_code == 422


def grant_editor(conn, catalog, *, south=False):
    uid = catalog.users["reader"]
    gid = conn.execute("SELECT group_id FROM user_groups WHERE user_id=?", (uid,)).fetchone()[0]
    conn.execute("UPDATE library_grants SET access='edit' WHERE group_id=?", (gid,))
    if south:
        conn.execute("INSERT INTO library_grants VALUES(?,?,'view')", (gid, catalog.libraries["south"]))
    conn.commit()


def test_cross_library_album_mutation_validates_whole_scope_before_writing(catalog):
    north, south = catalog.images["n01"], catalog.images["s01"]
    with db.open_db(catalog.cfg.db_path) as conn:
        grant_editor(conn,catalog,south=True)
        album = ops.create_album(conn,"North selection",library_id=catalog.libraries["north"])["id"]
        shared = ops.create_album(conn,"Unscoped shared selection")["id"]
        ops.album_add_images(conn,shared,[north,south])
    client, _ = client_for(catalog)
    with client:
        response = client.post(f"/api/v1/albums/{album}/images",json={"image_ids":[north,south]})
        assert response.status_code == 403
        assert client.patch(f"/api/v1/albums/{shared}",json={"name":"Must not change"}).status_code == 403
        assert client.post(f"/api/v1/albums/{album}/images",json={"image_ids":[north]}).status_code == 200
        assert client.patch(f"/api/v1/albums/{album}",json={"cover_image_id":catalog.images["n02"]}).status_code == 400
    with db.open_db(catalog.cfg.db_path) as conn:
        assert [r[0] for r in conn.execute("SELECT image_id FROM album_images WHERE album_id=?",(album,))] == [north]
        assert conn.execute("SELECT name FROM albums WHERE id=?",(shared,)).fetchone()[0] == "Unscoped shared selection"


def test_comment_note_play_and_media_recheck_revoked_scope(catalog):
    client, _ = client_for(catalog)
    outsider, _ = client_for(catalog,"outsider")
    iid = catalog.images["n01"]
    with client, outsider:
        comment = client.post(f"/api/v1/images/{iid}/comments",json={"body":"Keep my observation"}).json()["id"]
        note = client.post(f"/api/v1/images/{iid}/notes",json={"body":"Keep my note"}).json()["id"]
        session = client.post("/api/v1/play/sessions",json={"source":{"library_id":catalog.libraries["north"]}}).json()["token"]
        assert outsider.get(f"/api/v1/play/sessions/{session}").status_code == 404
        with db.open_db(catalog.cfg.db_path) as conn:
            conn.execute("DELETE FROM library_grants WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(catalog.users["reader"],));conn.commit()
        for kind, identity in (("comments",comment),("notes",note)):
            assert client.delete(f"/api/v1/{kind}/{identity}").status_code == 403
        for suffix in ("","/items"):
            assert client.get(f"/api/v1/play/sessions/{session}{suffix}").status_code == 403
        response = client.post(f"/api/v1/play/sessions/{session}/next")
        assert response.status_code == 403 and "image_id" not in response.json()
        assert client.delete(f"/api/v1/play/sessions/{session}").status_code == 200
    with db.open_db(catalog.cfg.db_path) as conn:
        assert conn.execute("SELECT body FROM comments WHERE id=?",(comment,)).fetchone()[0] == "Keep my observation"
        assert conn.execute("SELECT body FROM notes WHERE id=?",(note,)).fetchone()[0] == "Keep my note"


def test_group_view_capability_is_required_even_with_library_grant(catalog):
    client, _ = client_for(catalog)
    with client, db.open_db(catalog.cfg.db_path) as conn:
        conn.execute("UPDATE groups SET capabilities=? WHERE id IN (SELECT group_id FROM user_groups WHERE user_id=?)",
                     (json.dumps(["rate","tag_edit"]),catalog.users["reader"]));conn.commit()
        assert client.get("/api/v1/images").json()["items"] == []
        assert client.get("/api/v1/libraries").json() == []
        iid = catalog.images["n01"]
        assert client.get(f"/api/v1/images/{iid}").status_code == 403
        assert client.post(f"/api/v1/images/{iid}/rating",json={"value":3}).status_code == 403


def test_import_source_symlinks_replacement_and_mid_batch_revocation(catalog,tmp_path,monkeypatch):
    source, outside = tmp_path / "approved", tmp_path / "outside"
    draw_image(source / "a.png",310); draw_image(source / "b.png",311)
    draw_image(outside / "private.png",312)
    (source / "escape").symlink_to(outside,target_is_directory=True)
    admin = AccessContext(catalog.users["curator"])
    manager = AccessContext(catalog.users["reader"])
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute("UPDATE library_grants SET access='manage' WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(manager.user_id,));conn.commit()
        allowed = source_access.register(conn,admin,str(source))
        with pytest.raises(ServiceError):
            import_plans.create_plan(conn,manager,catalog.libraries["north"],str(source / "escape"))
        plan = import_plans.create_plan(conn,manager,catalog.libraries["north"],str(source),mode="copy")
        before = file_inventory(catalog.roots)
        record = import_plans._record_item
        def revoke_after_first(c,pid,position,state,**kwargs):
            record(c,pid,position,state,**kwargs)
            if state == "succeeded":
                source_access.revoke(c,admin,allowed["id"])
        with monkeypatch.context() as patch:
            patch.setattr(import_plans,"_record_item",revoke_after_first)
            with pytest.raises(ServiceError):
                import_plans.execute_plan(conn,catalog.cfg,manager,plan["plan_id"])
        assert set(file_inventory(catalog.roots)) - set(before) == {"north/a.png"}
        source_access.register(conn,admin,str(source))
        source.rename(tmp_path / "saved-approved")
        draw_image(source / "new.png",313)
        with pytest.raises(ServiceError):
            source_access.require_source(conn,manager,str(source))


def test_plan_job_ownership_and_admin_only_path_registration(catalog,tmp_path):
    root = tmp_path / "incoming";draw_image(root / "new.png",330)
    with db.open_db(catalog.cfg.db_path) as conn:
        admin = AccessContext(catalog.users["curator"])
        plan = import_plans.create_plan(conn,admin,catalog.libraries["north"],str(root),mode="copy")
        job = jobs.submit(conn,admin,"scan",catalog.libraries["north"])
        conn.execute("UPDATE library_grants SET access='manage' WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(catalog.users["reader"],));conn.commit()
        manager = AccessContext(catalog.users["reader"])
        for read, identity in ((import_plans.get_plan,plan["plan_id"]),(jobs.get_job,job["id"])):
            with pytest.raises(ServiceError) as error:
                read(conn,manager,identity)
            assert error.value.status == 404
    client, _ = client_for(catalog)
    with client:
        assert client.post("/api/v1/libraries",json={"name":"Unauthorized root","root_path":str(root)}).status_code == 403
        assert client.post("/api/v1/import-sources",json={"root_path":str(root)}).status_code == 403
    result = cli(catalog.cfg,"import-sources","allow",str(root))
    assert result.returncode == 0, result.stderr
    root_id = json.loads(result.stdout)["id"]
    assert cli(catalog.cfg,"import-sources","revoke",str(root_id)).returncode == 0
    assert json.loads(cli(catalog.cfg,"import-sources","list").stdout)[0]["enabled"] == 0


def test_active_legacy_original_is_downloaded_and_validation_never_echoes_password(catalog):
    iid = catalog.images["n01"]
    payload = '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    (catalog.roots / "north" / "unsafe.svg").write_text(payload)
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute("UPDATE images SET rel_path='unsafe.svg',filename='unsafe.svg' WHERE id=?",(iid,));conn.commit()
    client, _ = client_for(catalog)
    with client:
        response = client.get(f"/api/v1/media/original/{iid}")
        assert response.text == payload and response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        password = secrets.token_urlsafe(1800)
        invalid = client.post("/api/v1/auth/login",json={"username":"reader","password":password})
        assert invalid.status_code == 422 and password not in invalid.text and '"input"' not in invalid.text
        assert client.post("/api/v1/auth/login",json={"username":"reader","password":"x"*1100000}).status_code == 413


def test_explicit_public_origin_cookie_and_referer_fallback(catalog):
    from picshelf.config import Config
    cfg = Config(catalog.cfg.data_dir,public_origin="https://gallery.example.test")
    with TestClient(create_app(cfg),headers={"Origin":"https://gallery.example.test"}) as client:
        response = client.post("/api/v1/auth/login",json={"username":"reader","password":catalog.password})
        assert response.status_code == 200
        secure = "; Secure" in response.headers["set-cookie"]
        assert secure
    client, _ = client_for(catalog)
    with client:
        del client.headers["Origin"]
        assert client.post(f"/api/v1/images/{catalog.images['n01']}/rating",json={"value":4},
                           headers={"Referer":"http://testserver/"}).status_code == 200
        assert client.post("/api/v1/auth/logout",headers={"Referer":"http://testserver.evil.example/"}).status_code == 403
    with pytest.raises(ServiceError):
        Config(catalog.cfg.data_dir,trusted_proxies=("*",))


def test_legacy_tokens_expiry_admin_reset_and_cli_reset_invalidate_sessions(catalog):
    import base64,hashlib,hmac,time
    client, _ = client_for(catalog)
    secret = auth.load_secret(catalog.cfg.secret_path)
    uid = catalog.users["reader"]
    body = base64.urlsafe_b64encode(json.dumps({"uid":uid,"exp":int(time.time())+500,"n":"old"}).encode()).rstrip(b"=")
    legacy = body.decode()+"."+hmac.new(secret,body,hashlib.sha256).hexdigest()
    with client:
        for token in (legacy,auth.sign_token(secret,uid,ttl=-1)):
            assert client.get("/api/v1/auth/me",headers={"Authorization":"Bearer "+token}).status_code == 401
        admin, _ = client_for(catalog,"curator")
        new_password = secrets.token_urlsafe(24)
        with admin:
            assert admin.patch(f"/api/v1/users/{uid}",json={"password":new_password}).status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.post("/api/v1/auth/login",json={"username":"reader","password":new_password}).status_code == 200
        result = cli(catalog.cfg,"users","passwd","reader","--password-stdin",input_text=secrets.token_urlsafe(24)+"\n")
        assert result.returncode == 0, result.stderr
        assert client.get("/api/v1/auth/me").status_code == 401


def test_login_limit_expires_and_password_cost_parameters_are_bounded(catalog,monkeypatch):
    import time
    with TestClient(create_app(catalog.cfg),headers={"Origin":"http://testserver"}) as client:
        for _ in range(5):
            assert client.post("/api/v1/auth/login",json={"username":"unknown fixture user","password":"incorrect"}).status_code == 401
        limited = client.post("/api/v1/auth/login",json={"username":"unknown fixture user","password":"incorrect"})
        assert limited.status_code == 429
        now = time.time()
        monkeypatch.setattr(auth.time,"time",lambda:now+61)
        assert client.post("/api/v1/auth/login",json={"username":"unknown fixture user","password":"incorrect"}).status_code == 401
    encoded = auth.hash_password(secrets.token_urlsafe(24)).split("$")
    encoded[3] = "1000000000"
    assert not auth.verify_password("irrelevant","$".join(encoded))


def test_secret_publication_is_atomic_and_owner_only(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "isolated-data" / "secret.key"
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _:auth.load_secret(path),range(16)))
    key_count = len(set(keys))
    sizes = {len(key) for key in keys}
    assert key_count == 1 and sizes == {32}
    assert path.stat().st_mode & 0o777 == 0o600
    assert {p.name for p in path.parent.iterdir()} == {"secret.key"}


def test_proxy_headers_only_work_from_explicit_trusted_peer(catalog):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    # These peers are disposable ASGI test addresses, never discovered host data.
    for peer, expected in (("198.51.100.42",403),("192.0.2.10",200)):
        app = ProxyHeadersMiddleware(create_app(catalog.cfg),trusted_hosts=["192.0.2.10"])
        with TestClient(app,client=(peer,4321),headers={"Origin":"https://testserver"}) as client:
            response = client.post("/api/v1/auth/login",headers={"X-Forwarded-Proto":"https"},
                                   json={"username":"reader","password":catalog.password})
            assert response.status_code == expected
            if expected == 200:
                secure = "; Secure" in response.headers["set-cookie"]
                assert secure


def test_hidden_tag_cannot_be_discovered_by_alternate_claim_endpoint(catalog):
    with db.open_db(catalog.cfg.db_path) as conn:
        grant_editor(conn,catalog)
        ops.add_tags(conn,catalog.images["s01"],["Hidden scope label"],catalog.users["curator"])
    client, _ = client_for(catalog)
    with client:
        for path,body in (("/tags",{"name":"Hidden scope label"}),
                          (f"/images/{catalog.images['n01']}/tags",{"names":["Hidden scope label"]}),
                          (f"/images/{catalog.images['n01']}/tag-claims",{"name":"Hidden scope label","origin":"suggested"})):
            assert client.post("/api/v1"+path,json=body).status_code == 404
    with db.open_db(catalog.cfg.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name='Hidden scope label'").fetchone()[0] == 1


def test_moving_an_approved_source_requires_explicit_move_authorization(catalog,tmp_path):
    source = tmp_path / "move-approved"
    draw_image(source / "move.png",9990)
    original = (source / "move.png").read_bytes()
    with db.open_db(catalog.cfg.db_path) as conn:
        admin,manager = AccessContext(catalog.users["curator"]),AccessContext(catalog.users["reader"])
        conn.execute("UPDATE library_grants SET access='manage' WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(manager.user_id,));conn.commit()
        source_access.register(conn,admin,str(source))
        with pytest.raises(ServiceError):
            import_plans.create_plan(conn,manager,catalog.libraries["north"],str(source),mode="move")
        assert (source / "move.png").read_bytes() == original
        source_access.register(conn,admin,str(source),allow_move=True)
        plan = import_plans.create_plan(conn,manager,catalog.libraries["north"],str(source),mode="move")
        assert import_plans.execute_plan(conn,catalog.cfg,manager,plan["plan_id"])["state"] == "succeeded"
    assert not (source / "move.png").exists()
    assert (catalog.roots / "north" / "move.png").read_bytes() == original


def test_legacy_synchronous_scan_checks_revocation_at_next_file(catalog,monkeypatch):
    from picshelf import indexer
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute("UPDATE library_grants SET access='manage' WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(catalog.users["reader"],));conn.commit()
    before = file_inventory(catalog.roots)
    walk = indexer._iter_image_files
    def revoke_between_files(*args,**kwargs):
        for position,path in enumerate(walk(*args,**kwargs)):
            yield path
            if position == 0:
                with db.open_db(catalog.cfg.db_path) as conn:
                    conn.execute("DELETE FROM library_grants WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(catalog.users["reader"],));conn.commit()
    monkeypatch.setattr(indexer,"_iter_image_files",revoke_between_files)
    client, _ = client_for(catalog)
    with client:
        assert client.post(f"/api/v1/libraries/{catalog.libraries['north']}/scan").status_code == 403
    assert file_inventory(catalog.roots) == before
    with db.open_db(catalog.cfg.db_path) as conn:
        assert conn.execute("SELECT status FROM scans ORDER BY id DESC LIMIT 1").fetchone()[0] == "interrupted"
