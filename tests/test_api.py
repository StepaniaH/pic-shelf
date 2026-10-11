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
    client = TestClient(app)
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
    anon = TestClient(env["app"])
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

    alice = TestClient(env["app"])
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
