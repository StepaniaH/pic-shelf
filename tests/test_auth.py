from picshelf import auth


def test_password_roundtrip():
    h = auth.hash_password("hunter22")
    assert h.startswith("scrypt$")
    assert auth.verify_password("hunter22", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("x", "garbage")
    assert not auth.verify_password("", "scrypt$1$2$3$aa$bb")


def test_token_roundtrip(tmp_path):
    secret = auth.load_secret(tmp_path / "secret.key")
    assert (tmp_path / "secret.key").exists()
    token = auth.sign_token(secret, 7)
    assert auth.verify_token(secret, token) == 7
    assert auth.verify_token(secret, token + "x") is None
    assert auth.verify_token(b"other-secret", token) is None
    assert auth.verify_token(secret, "bogus") is None
    expired = auth.sign_token(secret, 7, ttl=-10)
    assert auth.verify_token(secret, expired) is None


def test_default_groups_idempotent(conn):
    auth.ensure_default_groups(conn)
    auth.ensure_default_groups(conn)
    rows = conn.execute("SELECT name FROM groups ORDER BY name").fetchall()
    assert [r["name"] for r in rows] == ["admins", "members"]


def test_caps_for_user(conn, admin, alice):
    admin_caps = auth.caps_for_user(conn, admin)
    assert {"admin", "manage_library", "view"} <= admin_caps
    alice_caps = auth.caps_for_user(conn, alice)
    assert "view" in alice_caps and "rate" in alice_caps
    assert "admin" not in alice_caps
    assert auth.has_cap(conn, alice, "view")
    assert not auth.has_cap(conn, alice, "manage_library")


def test_library_access(conn, admin, alice, library):
    assert auth.library_access(conn, alice, library["id"]) == "view"
    assert auth.library_access(conn, admin, library["id"]) == "manage"

    # 新建组授予 edit，alice 加入后升级为 edit
    conn.execute(
        "INSERT INTO groups(name, capabilities, created_at) VALUES('editors', '[\"view\",\"edit\"]', '2026-01-01')"
    )
    gid = conn.execute("SELECT id FROM groups WHERE name='editors'").fetchone()["id"]
    conn.execute("INSERT INTO user_groups(user_id, group_id) VALUES(?,?)", (alice["id"], gid))
    conn.execute(
        "INSERT INTO library_grants(group_id, library_id, access) VALUES(?,?, 'edit')",
        (gid, library["id"]),
    )
    assert auth.library_access(conn, alice, library["id"]) == "edit"
    assert auth.visible_library_ids(conn, alice) == [library["id"]]
    assert auth.visible_library_ids(conn, admin) is None


def test_disabled_user_filtered(conn):
    uid = auth.create_user(conn, "bob", "bob-pass-123")
    conn.execute("UPDATE users SET disabled=1 WHERE id=?", (uid,))
    conn.commit()
    assert auth.get_user(conn, uid) is None
