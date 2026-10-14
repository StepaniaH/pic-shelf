import shutil

import pytest
from fastapi.testclient import TestClient

from picshelf import auth, db as dbm, indexer, libsvc
from picshelf.api import create_app
from picshelf.config import Config
from tests.conftest import make_image

ADMIN_PW = "admin-pass-123"


@pytest.fixture()
def env(tmp_path):
    cfg = Config(tmp_path / "data").ensure()
    conn = dbm.open_db(cfg.db_path)
    auth.ensure_default_groups(conn)
    auth.create_user(conn, "admin", ADMIN_PW, is_admin=True)
    root = tmp_path / "library"
    make_image(root / "alpha" / "red.png", (220, 40, 40))
    make_image(root / "alpha" / "blue.png", (40, 40, 220))
    make_image(root / "beta" / "green.jpg", (40, 200, 40))
    make_image(root / "beta" / "nested" / "yellow.png", (220, 200, 40))
    lib_id = libsvc.create_library(conn, "testlib", str(root))
    indexer.scan_library(conn, cfg, lib_id)
    conn.close()

    app = create_app(cfg)
    client = TestClient(app, headers={"Origin": "http://testserver"})
    return {
        "cfg": cfg, "client": client, "app": app,
        "library_id": lib_id, "root": root,
    }


def _login(client, username="admin", password=ADMIN_PW):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _find_image(client, name):
    r = client.get("/api/v1/images", params={"q": f"file:{name}"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, f"未找到 {name}"
    return items[0]


# ---------------------------------------------------------------- 基础

def test_health_and_unauthorized(env):
    c = env["client"]
    assert c.get("/api/v1/health").json()["ok"] is True
    assert c.get("/api/v1/images").status_code == 401
    assert c.get("/api/v1/auth/me").status_code == 401


def test_login_logout(env):
    c = env["client"]
    assert c.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    data = _login(c)
    assert data["user"]["is_admin"] is True
    assert "admin" in data["user"]["caps"]
    me = c.get("/api/v1/auth/me").json()
    assert me["username"] == "admin"
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------- 图片与检索

def test_images_list_and_search(env):
    c = env["client"]
    _login(c)
    r = c.get("/api/v1/images").json()
    assert r["total"] == 4
    assert r["items"][0]["thumb_url"].startswith("/api/v1/media/thumb/")

    assert c.get("/api/v1/images", params={"q": "ext:png"}).json()["total"] == 3
    assert c.get("/api/v1/images", params={"q": "yellow"}).json()["total"] == 1

    r = c.get("/api/v1/images", params={"sort": "path"}).json()
    assert r["items"][0]["filename"] == "blue.png"


def test_image_filters_accept_url_parameters_and_preserve_bounds(env):
    c = env['client']
    _login(c)
    image = _find_image(c, 'red.png')
    artist = c.post('/api/v1/artists', json={'name': 'Filter artist'}).json()
    assert c.post(f"/api/v1/images/{image['id']}/artists", json={'artist_id': artist['id']}).status_code == 200
    assert c.post(f"/api/v1/images/{image['id']}/favorite", json={'on': True}).status_code == 200
    for filters, expected in [
        ({'library_id': str(env['library_id'])}, 4),
        ({'artist_id': str(artist['id'])}, 1),
        ({'favorites': 'true'}, 1),
        ({'favorites': '1'}, 1),
        ({'favorites': 'false'}, 4),
        ({'library_id': str(env['library_id']), 'artist_id': str(artist['id']), 'favorites': 'true'}, 1),
    ]:
        response = c.get('/api/v1/images', params=filters)
        assert response.status_code == 200, response.text
        assert response.json()['total'] == expected
        if expected == 1:
            assert response.json()['items'][0]['id'] == image['id']
    for field in ('library_id', 'artist_id'):
        for invalid in ('0', '-1', str(2**63), '1.5', 'true'):
            assert c.get('/api/v1/images', params={field: invalid}).status_code == 422
    assert c.get('/api/v1/images', params={'favorites': 'invalid'}).status_code == 422


def test_image_detail_interactions(env):
    c = env["client"]
    _login(c)
    img = _find_image(c, "red.png")
    iid = img["id"]

    detail = c.get(f"/api/v1/images/{iid}").json()
    assert detail["filename"] == "red.png"
    assert detail["comments"] == [] and detail["notes"] == []

    # 评分
    r = c.post(f"/api/v1/images/{iid}/rating", json={"value": 9}).json()
    assert r["my_rating"] == 9 and r["avg_rating"] == 9.0
    assert c.get(f"/api/v1/images/{iid}").json()["my_rating"] == 9
    # 清除评分
    r = c.post(f"/api/v1/images/{iid}/rating", json={"value": None}).json()
    assert r["my_rating"] == -1
    # 收藏
    assert c.post(f"/api/v1/images/{iid}/favorite", json={}).json()["favorite"] is True
    assert c.post(f"/api/v1/images/{iid}/favorite", json={"on": False}).json()["favorite"] is False

    # 评论
    r = c.post(f"/api/v1/images/{iid}/comments", json={"body": "好图"}).json()
    cid = r["id"]
    assert c.get(f"/api/v1/images/{iid}/comments").json()[0]["body"] == "好图"
    assert c.delete(f"/api/v1/comments/{cid}").json()["ok"] is True
    assert c.get(f"/api/v1/images/{iid}/comments").json() == []

    # 笔记
    r = c.post(f"/api/v1/images/{iid}/notes", json={"body": "作画提示"}).json()
    assert c.get(f"/api/v1/images/{iid}/notes").json()[0]["body"] == "作画提示"
    assert c.delete(f"/api/v1/notes/{r['id']}").json()["ok"] is True


def test_tags_flow(env):
    c = env["client"]
    _login(c)
    iid = _find_image(c, "red.png")["id"]
    r = c.post(f"/api/v1/images/{iid}/tags", json={"names": ["色彩:红色", "场景:夜空"]}).json()
    assert len(r["added"]) == 2
    tags = c.get("/api/v1/tags", params={"q": "色彩"}).json()
    assert any(t["name"] == "色彩:红色" for t in tags)
    detail = c.get(f"/api/v1/images/{iid}").json()
    assert {t["name"] for t in detail["tags"]} == {"色彩:红色", "场景:夜空"}
    assert c.delete(f"/api/v1/images/{iid}/tags/色彩:红色").json()["removed"] == 1
    detail = c.get(f"/api/v1/images/{iid}").json()
    assert {t["name"] for t in detail["tags"]} == {"场景:夜空"}
    # 检索
    assert c.get("/api/v1/images", params={"q": "色彩:红色"}).json()["total"] == 0
    assert c.get("/api/v1/images", params={"q": "场景:夜空"}).json()["total"] == 1


def test_artists_flow(env):
    c = env["client"]
    _login(c)
    iid = _find_image(c, "red.png")["id"]
    artist = c.post("/api/v1/artists", json={"name": "画师A", "aliases": "A-sensei"}).json()
    r = c.post(f"/api/v1/images/{iid}/artists", json={"artist_id": artist["id"]}).json()
    assert [a["name"] for a in r["artists"]] == ["画师A"]
    assert c.get("/api/v1/artists", params={"q": "画师"}).json()[0]["image_count"] == 1
    imgs = c.get(f"/api/v1/artists/{artist['id']}/images").json()
    assert imgs["total"] == 1
    assert c.get("/api/v1/images", params={"q": "artist:画师"}).json()["total"] == 1
    # 取消关联
    r = c.post(f"/api/v1/images/{iid}/artists", json={"artist_id": artist["id"], "on": False}).json()
    assert r["artists"] == []


# ---------------------------------------------------------------- 媒体

def test_media_serving(env):
    c = env["client"]
    _login(c)
    iid = _find_image(c, "red.png")["id"]
    r = c.get(f"/api/v1/media/thumb/{iid}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert len(r.content) > 100
    r = c.get(f"/api/v1/media/original/{iid}", params={"download": 1})
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    # 未登录不可访问
    anon = TestClient(env["app"], headers={"Origin": "http://testserver"})
    assert anon.get(f"/api/v1/media/thumb/{iid}").status_code == 401
    # 不存在
    assert c.get("/api/v1/media/thumb/99999").status_code == 404


# ---------------------------------------------------------------- 专辑

def test_albums_flow(env):
    c = env["client"]
    _login(c)
    lib = env["library_id"]
    albums = c.get("/api/v1/albums", params={"library_id": lib}).json()
    names = {a["name"] for a in albums}
    assert {"alpha", "beta", "nested"} <= names

    red = _find_image(c, "red.png")["id"]
    created = c.post("/api/v1/albums", json={"name": "精选", "library_id": lib}).json()
    r = c.post(f"/api/v1/albums/{created['id']}/images", json={"image_ids": [red]}).json()
    assert r["added"] == 1
    r = c.get(f"/api/v1/albums/{created['id']}/images").json()
    assert r["total"] == 1 and r["items"][0]["filename"] == "red.png"

    # 文件夹专辑不可手工增删
    folder = next(a for a in albums if a["name"] == "alpha")
    r = c.post(f"/api/v1/albums/{folder['id']}/images", json={"image_ids": [red]})
    assert r.status_code == 400

    assert c.delete(f"/api/v1/albums/{created['id']}/images/{red}").json()["removed"] == 1

    # 按专辑检索
    c.post(f"/api/v1/albums/{created['id']}/images", json={"image_ids": [red]})
    assert c.get("/api/v1/images", params={"album_id": created["id"]}).json()["total"] == 1


# ---------------------------------------------------------------- 播放

def test_play_flow(env):
    c = env["client"]
    _login(c)
    lib = env["library_id"]

    s = c.post("/api/v1/play/sessions", json={
        "source": {"library_id": lib}, "order": "sequential", "loop": True,
    }).json()
    assert s["total"] == 4 and s["cursor"] == -1
    token = s["token"]

    r = c.post(f"/api/v1/play/sessions/{token}/next").json()
    assert r["position"] == 1 and r["image"]["filename"] == "blue.png"
    r = c.post(f"/api/v1/play/sessions/{token}/next").json()
    assert r["image"]["filename"] == "red.png"
    r = c.post(f"/api/v1/play/sessions/{token}/prev").json()
    assert r["image"]["filename"] == "blue.png"

    st = c.get(f"/api/v1/play/sessions/{token}").json()
    assert st["position"] == 1 and st["image"]["filename"] == "blue.png"
    assert st["loop"] is True

    r = c.post(f"/api/v1/play/sessions/{token}/goto", json={"position": 3}).json()
    assert r["image"]["filename"] == "yellow.png"
    r = c.post(f"/api/v1/play/sessions/{token}/next").json()
    assert r["image"]["filename"] == "blue.png"  # loop 回绕

    items = c.get(f"/api/v1/play/sessions/{token}/items", params={"offset": 0, "limit": 2}).json()
    assert len(items["items"]) == 2

    r = c.post(f"/api/v1/play/sessions/{token}/reshuffle").json()
    assert r["total"] == 4 and r["cursor"] == -1

    assert c.delete(f"/api/v1/play/sessions/{token}").json()["ok"] is True
    assert c.get(f"/api/v1/play/sessions/{token}").status_code == 404


def test_play_validation(env):
    c = env["client"]
    _login(c)
    assert c.post("/api/v1/play/sessions", json={"order": "bogus"}).status_code == 400
    r = c.post("/api/v1/play/sessions", json={"source": {"album_id": 9999}})
    assert r.status_code == 404


# ---------------------------------------------------------------- 扫描 / 查重

def test_scan_and_dupes(env):
    c = env["client"]
    _login(c)
    lib = env["library_id"]
    r = c.post(f"/api/v1/libraries/{lib}/scan").json()
    assert r["added"] == 0 and r["unchanged"] == 4

    shutil.copy(env["root"] / "alpha" / "red.png", env["root"] / "alpha" / "red-copy.png")
    r = c.post(f"/api/v1/libraries/{lib}/scan").json()
    assert r["added"] == 1
    dupes = c.get("/api/v1/dupes").json()
    assert len(dupes) == 1 and dupes[0]["count"] == 2

    scans = c.get("/api/v1/scans").json()
    assert len(scans) >= 2 and scans[0]["status"] == "done"


# ---------------------------------------------------------------- 用户组与 ACL

def test_user_admin_and_acl(env):
    c = env["client"]
    _login(c)
    lib = env["library_id"]

    # 创建普通用户
    user = c.post("/api/v1/users", json={"username": "alice", "password": "alice-pass-1"}).json()
    assert user["groups"][0]["name"] == "members"

    alice = TestClient(env["app"], headers={"Origin": "http://testserver"})
    _login(alice, "alice", "alice-pass-1")
    assert alice.get("/api/v1/images").json()["total"] == 4  # members 默认 view
    iid = _find_image(alice, "red.png")["id"]
    assert alice.post(f"/api/v1/images/{iid}/rating", json={"value": 7}).json()["my_rating"] == 7
    # 普通用户不能扫库 / 管理用户
    assert alice.post(f"/api/v1/libraries/{lib}/scan").status_code == 403
    assert alice.get("/api/v1/users").status_code == 403
    assert alice.post("/api/v1/groups", json={"name": "x"}).status_code == 403

    # 管理员回收 members 的库授权 → alice 不可见
    members_gid = next(g["id"] for g in c.get("/api/v1/groups").json() if g["name"] == "members")
    r = c.put(f"/api/v1/libraries/{lib}/grants", json={"group_id": members_gid, "access": None})
    assert r.json()["ok"] is True
    assert alice.get("/api/v1/images").json()["total"] == 0
    assert alice.get(f"/api/v1/media/thumb/{iid}").status_code == 403

    # 新建组授权 edit，alice 加入后可评可标
    g = c.post("/api/v1/groups", json={
        "name": "viewers-plus", "capabilities": ["view", "rate", "comment", "tag_edit"],
    }).json()
    c.put(f"/api/v1/libraries/{lib}/grants", json={"group_id": g["id"], "access": "edit"})
    c.put(f"/api/v1/users/{user['id']}/groups", json={"group_ids": [g["id"]]})
    assert alice.get("/api/v1/images").json()["total"] == 4
    assert alice.post(f"/api/v1/images/{iid}/tags", json={"names": ["新:标签"]}).status_code == 200
    # 但 edit 够不着"扫库"（需要 manage）
    assert alice.post(f"/api/v1/libraries/{lib}/scan").status_code == 403

    # 禁用手续：重新启用 members 授权 + 禁用 alice
    c.put(f"/api/v1/libraries/{lib}/grants", json={"group_id": members_gid, "access": "view"})
    assert c.patch(f"/api/v1/users/{user['id']}", json={"disabled": True}).json()["disabled"] is True
    assert alice.get("/api/v1/auth/me").status_code == 401
    # 不能禁用自己
    me = c.get("/api/v1/auth/me").json()
    assert c.patch(f"/api/v1/users/{me['id']}", json={"disabled": True}).status_code == 400

    # 系统组能力不可改
    assert c.patch(f"/api/v1/groups/{members_gid}", json={"capabilities": ["view"]}).status_code == 400


def test_stats(env):
    c = env["client"]
    _login(c)
    s = c.get("/api/v1/stats").json()
    assert s["images"] == 4 and s["libraries"] == 1
    assert s["users"] == 1 and s["tags"] == 0


def test_catalog_and_stats_follow_library_grants(env):
    from picshelf import ops
    c = env["client"]
    _login(c)
    conn = dbm.open_db(env["cfg"].db_path)
    uid = auth.create_user(conn, "reader", "reader-pass-123")
    visible_id = conn.execute("SELECT id FROM images LIMIT 1").fetchone()[0]
    hidden_root = env["root"].parent / "private"
    make_image(hidden_root / "private.png", (10, 20, 30))
    hidden_lib = libsvc.create_library(conn, "private", str(hidden_root))
    indexer.scan_library(conn, env["cfg"], hidden_lib)
    conn.execute("DELETE FROM library_grants WHERE library_id=?", (hidden_lib,))
    hidden_id = conn.execute("SELECT id FROM images WHERE library_id=?", (hidden_lib,)).fetchone()[0]
    for iid, name in [(visible_id, "public"), (hidden_id, "private")]:
        ops.add_tags(conn, iid, [name], uid)
        artist = ops.create_artist(conn, name)
        ops.link_artist(conn, iid, artist["id"])
        ops.set_rating(conn, iid, uid, 8)
        ops.set_favorite(conn, iid, uid, True)
    private_artist = artist["id"]
    public_artist = conn.execute("SELECT id FROM artists WHERE name='public'").fetchone()[0]
    ops.link_artist(conn, hidden_id, public_artist)
    album = ops.create_album(conn, "Mixed library album", env["library_id"])
    ops.album_add_images(conn, album["id"], [visible_id, hidden_id])
    ops.update_album(conn, album["id"], cover_image_id=hidden_id)
    private_album = ops.create_album(conn, "Private album", hidden_lib)
    ops.album_add_images(conn, private_album["id"], [visible_id])
    conn.close()
    reader = TestClient(env["app"], headers={"Origin": "http://testserver"})
    _login(reader, "reader", "reader-pass-123")
    stats = reader.get("/api/v1/stats").json()
    assert stats["images"] == 4 and stats["libraries"] == 1
    assert stats["tags"] == stats["artists"] == stats["ratings"] == stats["favorites"] == 1
    assert [x["name"] for x in reader.get("/api/v1/tags").json()] == ["public"]
    assert [x["name"] for x in reader.get("/api/v1/artists").json()] == ["public"]
    assert reader.get(f"/api/v1/artists/{private_artist}").status_code == 404
    assert reader.get(f"/api/v1/artists/{private_artist}/images").status_code == 404
    assert reader.patch(f"/api/v1/artists/{private_artist}", json={"note": "changed"}).status_code == 403
    assert reader.post("/api/v1/artists", json={"name": "private"}).status_code == 404
    assert reader.post(f"/api/v1/images/{visible_id}/artists", json={"artist_id": private_artist}).status_code == 403
    assert reader.get(f"/api/v1/artists/{public_artist}").json()["image_count"] == 1
    assert reader.post("/api/v1/artists", json={"name": "public"}).json()["image_count"] == 1
    assert reader.patch(f"/api/v1/artists/{public_artist}", json={"note": "updated"}).status_code == 403
    assert reader.post("/api/v1/artists", json={"name": "new artist"}).json()["image_count"] == 0
    album_detail = reader.get(f"/api/v1/albums/{album['id']}").json()
    assert album_detail["image_count"] == 1 and album_detail["cover_image_id"] is None
    listed = next(a for a in reader.get("/api/v1/albums").json() if a["id"] == album["id"])
    assert listed["image_count"] == 1 and listed["cover_image_id"] is None
    album_images = reader.get(f"/api/v1/albums/{album['id']}/images").json()
    assert album_images["total"] == album_images["album"]["image_count"] == 1
    assert c.get(f"/api/v1/albums/{album['id']}").json()["image_count"] == 2
    detail = reader.get(f"/api/v1/images/{visible_id}").json()
    assert private_album["id"] not in {a["id"] for a in detail["albums"]}
    conn = dbm.open_db(env["cfg"].db_path)
    conn.execute("DELETE FROM library_grants WHERE library_id=?", (env["library_id"],))
    conn.commit()
    conn.close()
    assert all(v == 0 for v in reader.get("/api/v1/stats").json().values())
    assert reader.get("/api/v1/tags").json() == []


def test_media_rejects_symlink_replaced_after_scan(env):
    c = env["client"]
    _login(c)
    iid = _find_image(c, "red.png")["id"]
    outside = env["root"].parent / "outside.png"
    make_image(outside, (10, 20, 30))
    src = env["root"] / "alpha/red.png"
    src.unlink()
    src.symlink_to(outside)
    assert c.get(f"/api/v1/media/original/{iid}").status_code == 404


@pytest.mark.parametrize("endpoint,body", [
    ("/tags", {"name": "  "}),
    ("/libraries", {"name": "  "}),
])
def test_empty_entity_names_return_client_error(env, endpoint, body):
    c = env["client"]
    _login(c)
    if endpoint == "/libraries":
        body = {**body, "root_path": str(env["root"])}
    assert c.post("/api/v1" + endpoint, json=body).status_code == 400
