from picshelf import auth, indexer, ops, search
from tests.conftest import image_id_by_name


def _setup(conn, cfg, library, alice):
    indexer.scan_library(conn, cfg, library["id"])
    red = image_id_by_name(conn, "red.png")
    blue = image_id_by_name(conn, "blue.png")
    green = image_id_by_name(conn, "green.jpg")
    ops.add_tags(conn, red, ["色彩:红色", "主体:小球"], alice["id"])
    ops.add_tags(conn, blue, ["色彩:蓝色"], alice["id"])
    ops.set_rating(conn, red, alice["id"], 9)
    ops.set_favorite(conn, blue, alice["id"], True)
    artist = ops.create_artist(conn, "画师A")
    ops.link_artist(conn, red, artist["id"])
    album = ops.create_album(conn, "精选", library_id=library["id"])
    ops.album_add_images(conn, album["id"], [blue])
    return {"red": red, "blue": blue, "green": green, "album": album, "artist": artist}


def _find(conn, alice, query, **kw):
    allowed = auth.visible_library_ids(conn, alice)
    res = search.search_images(conn, alice["id"], allowed, query=query, **kw)
    names = sorted(i["filename"] for i in res["items"])
    return res, names


def test_basic_queries(conn, cfg, library, alice):
    ids = _setup(conn, cfg, library, alice)

    res, names = _find(conn, alice, "色彩:红色")
    assert names == ["red.png"]
    assert res["total"] == 1

    res, names = _find(conn, alice, "-色彩:蓝色")
    assert "blue.png" not in names and "red.png" in names

    res, names = _find(conn, alice, "artist:画师")
    assert names == ["red.png"]

    res, names = _find(conn, alice, f"album:{ids['album']['id']}")
    assert names == ["blue.png"]

    res, names = _find(conn, alice, "rating:>=8")
    assert names == ["red.png"]

    res, names = _find(conn, alice, "fav:")
    assert names == ["blue.png"]

    res, names = _find(conn, alice, "ext:png")
    assert names == ["blue.png", "red.png", "yellow.png"]

    res, names = _find(conn, alice, "file:green")
    assert names == ["green.jpg"]

    # 逗号 = OR
    res, names = _find(conn, alice, "色彩:红色,色彩:蓝色")
    assert names == ["blue.png", "red.png"]

    # 裸词匹配文件名
    res, names = _find(conn, alice, "yellow")
    assert names == ["yellow.png"]


def test_acl_blocks_other_users(conn, cfg, library, alice):
    _setup(conn, cfg, library, alice)
    uid = auth.create_user(conn, "stranger", "stranger-pass-1")
    # 默认组成员对所有库有 view；此处模拟"管理员回收了其成员身份"
    conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
    conn.commit()
    stranger = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    allowed = auth.visible_library_ids(conn, stranger)
    assert allowed == []
    res = search.search_images(conn, stranger["id"], allowed, query="")
    assert res["total"] == 0


def test_admin_sees_all(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library["id"])
    allowed = auth.visible_library_ids(conn, admin)
    assert allowed is None
    res = search.search_images(conn, admin["id"], allowed, query="")
    assert res["total"] == 4


def test_get_image_detail(conn, cfg, library, alice):
    ids = _setup(conn, cfg, library, alice)
    detail = search.get_image(conn, ids["red"], alice["id"],
                              auth.visible_library_ids(conn, alice))
    assert detail is not None
    assert {t["name"] for t in detail["tags"]} == {"色彩:红色", "主体:小球"}
    assert [a["name"] for a in detail["artists"]] == ["画师A"]
    assert detail["my_rating"] == 9
    assert detail["favorite"] == 0
    blue_detail = search.get_image(conn, ids["blue"], alice["id"],
                                   auth.visible_library_ids(conn, alice))
    assert blue_detail["favorite"] == 1
    assert {al["name"] for al in blue_detail["albums"]} == {"alpha", "精选"}


def test_sort_orders(conn, cfg, library, alice):
    _setup(conn, cfg, library, alice)
    allowed = auth.visible_library_ids(conn, alice)
    res = search.search_images(conn, alice["id"], allowed, sort="rating")
    assert res["items"][0]["filename"] == "red.png"  # 9 分居首
    res = search.search_images(conn, alice["id"], allowed, sort="path")
    assert res["items"][0]["filename"] == "blue.png"
