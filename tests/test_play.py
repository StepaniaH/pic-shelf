from picshelf import auth, indexer, ops, play
from tests.conftest import image_id_by_name


def _setup(conn, cfg, library, alice):
    indexer.scan_library(conn, cfg, library["id"])
    red = image_id_by_name(conn, "red.png")
    blue = image_id_by_name(conn, "blue.png")
    green = image_id_by_name(conn, "green.jpg")
    yellow = image_id_by_name(conn, "yellow.png")
    ops.set_rating(conn, red, alice["id"], 9)
    ops.set_rating(conn, blue, alice["id"], 8)
    ops.set_rating(conn, green, alice["id"], 5)
    ops.set_rating(conn, yellow, alice["id"], 1)
    ops.set_favorite(conn, blue, alice["id"], True)
    return red, blue, green, yellow


def test_sequential_and_navigation(conn, cfg, library, alice):
    red, blue, green, yellow = _setup(conn, cfg, library, alice)
    allowed = auth.visible_library_ids(conn, alice)
    s = play.create_session(conn, alice["id"], allowed,
                            {"source": {"library_id": library["id"]}, "order": "sequential"})
    assert s["total"] == 4
    sess = play.get_session_by_token(conn, s["token"])

    r = play.step(conn, sess, +1)
    assert r["image_id"] == blue  # 路径序第一张 alpha/blue.png
    assert r["position"] == 1
    sess = play.get_session_by_token(conn, s["token"])
    r = play.step(conn, sess, +1)
    assert r["image_id"] == red
    r = play.goto(conn, sess, 3)
    assert r["image_id"] == yellow
    sess = play.get_session_by_token(conn, s["token"])  # 取最新游标再回退
    r = play.step(conn, sess, -1)
    assert r["image_id"] == green


def test_no_loop_stops_at_end(conn, cfg, library, alice):
    _setup(conn, cfg, library, alice)
    allowed = auth.visible_library_ids(conn, alice)
    s = play.create_session(conn, alice["id"], allowed,
                            {"source": {"library_id": library["id"]}, "order": "sequential"})
    sess = play.get_session_by_token(conn, s["token"])
    for _ in range(4):
        play.step(conn, sess, +1)
        sess = play.get_session_by_token(conn, s["token"])
    before = int(sess["cursor"])
    r = play.step(conn, sess, +1)
    assert r["done"] is True
    assert r["cursor"] == before == 3


def test_loop_wraps(conn, cfg, library, alice):
    _setup(conn, cfg, library, alice)
    allowed = auth.visible_library_ids(conn, alice)
    s = play.create_session(conn, alice["id"], allowed,
                            {"source": {"library_id": library["id"]}, "order": "sequential",
                             "loop": True})
    sess = play.get_session_by_token(conn, s["token"])
    for _ in range(4):
        play.step(conn, sess, +1)
        sess = play.get_session_by_token(conn, s["token"])
    assert int(sess["cursor"]) == 3
    r = play.step(conn, sess, +1)
    assert r["cursor"] == 0
    assert r["done"] is False


def test_limit_and_random(conn, cfg, library, alice):
    _setup(conn, cfg, library, alice)
    allowed = auth.visible_library_ids(conn, alice)
    s = play.create_session(conn, alice["id"], allowed,
                            {"source": {"library_id": library["id"]},
                             "order": "shuffle", "limit": 2})
    assert s["total"] == 2
    r = play.reshuffle(conn, play.get_session_by_token(conn, s["token"]), alice["id"], allowed)
    assert r["total"] == 2
    assert r["cursor"] == -1


def test_source_filters(conn, cfg, library, alice):
    red, blue, green, yellow = _setup(conn, cfg, library, alice)
    allowed = auth.visible_library_ids(conn, alice)

    s = play.create_session(conn, alice["id"], allowed,
                            {"source": {"favorites": True}, "order": "sequential"})
    assert s["total"] == 1
    items = play.queue_items(conn, s["session_id"])
    assert items == [blue]

    s = play.create_session(conn, alice["id"], allowed,
                            {"source": {"rating_min": 8}, "order": "sequential"})
    assert s["total"] == 2
    assert set(play.queue_items(conn, s["session_id"])) == {red, blue}


def test_play_uses_acl(conn, cfg, library, alice):
    _setup(conn, cfg, library, alice)
    uid = auth.create_user(conn, "nogrant", "nogrant-pass-1")
    # 默认组成员对所有库有 view；此处模拟"管理员回收了其成员身份"
    conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
    conn.commit()
    stranger = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    allowed = auth.visible_library_ids(conn, stranger)
    s = play.create_session(conn, stranger["id"], allowed,
                            {"source": {"library_id": library["id"]}, "order": "sequential"})
    assert s["total"] == 0
