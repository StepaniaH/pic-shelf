"""认证与授权：口令哈希（scrypt）、会话令牌（HMAC）、组能力与库级授权。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path

from .db import utcnow
from .i18n import t

TOKEN_TTL_DEFAULT = 30 * 24 * 3600  # 30 天

ALL_CAPS = ("view", "rate", "comment", "note", "tag_edit", "manage_library", "admin")
DEFAULT_MEMBER_CAPS = ("view", "rate", "comment", "note", "tag_edit")


# ---------------------------------------------------------------- 口令

def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    if not password:
        raise ValueError(t("口令不能为空"))
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(hash_hex) // 2,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- 令牌

def load_secret(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover
        pass
    return secret


def sign_token(secret: bytes, user_id: int, ttl: int = TOKEN_TTL_DEFAULT) -> str:
    payload = json.dumps(
        {"uid": user_id, "exp": int(time.time()) + ttl, "n": secrets.token_hex(8)},
        separators=(",", ":"),
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(secret: bytes, token: str) -> int | None:
    try:
        body, sig = token.rsplit(".", 1)
        expect = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + pad))
        if int(data["exp"]) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None


# ---------------------------------------------------------------- 用户与组

def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE id=? AND disabled=0", (user_id,)
    ).fetchone()


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE lower(username)=lower(?)", (username,)
    ).fetchone()


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    is_admin: bool = False,
    display_name: str | None = None,
    group_names: tuple[str, ...] | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users(username, password_hash, display_name, is_admin, created_at) "
        "VALUES(?,?,?,?,?)",
        (username, hash_password(password), display_name, 1 if is_admin else 0, utcnow()),
    )
    user_id = int(cur.lastrowid)
    if group_names is None:
        group_names = ("admins",) if is_admin else ("members",)
    for gname in group_names:
        row = conn.execute("SELECT id FROM groups WHERE name=?", (gname,)).fetchone()
        if row is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO user_groups(user_id, group_id) VALUES(?,?)",
            (user_id, row["id"]),
        )
    conn.commit()
    return user_id


def ensure_default_groups(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO groups(name, description, capabilities, is_system, created_at) "
        "VALUES(?,?,?,1,?) ON CONFLICT(name) DO NOTHING",
        ("admins", t("管理员组"), json.dumps(list(ALL_CAPS)), utcnow()),
    )
    conn.execute(
        "INSERT INTO groups(name, description, capabilities, is_system, created_at) "
        "VALUES(?,?,?,1,?) ON CONFLICT(name) DO NOTHING",
        ("members", t("普通成员组"), json.dumps(list(DEFAULT_MEMBER_CAPS)), utcnow()),
    )
    conn.commit()


def caps_for_user(conn: sqlite3.Connection, user: sqlite3.Row) -> set[str]:
    if user["is_admin"]:
        return set(ALL_CAPS)
    caps: set[str] = set()
    rows = conn.execute(
        "SELECT g.capabilities FROM groups g "
        "JOIN user_groups ug ON ug.group_id=g.id WHERE ug.user_id=?",
        (user["id"],),
    ).fetchall()
    for r in rows:
        try:
            caps.update(json.loads(r["capabilities"] or "[]"))
        except Exception:
            continue
    return caps


def has_cap(conn: sqlite3.Connection, user: sqlite3.Row, cap: str) -> bool:
    return cap in caps_for_user(conn, user)


def library_access(conn: sqlite3.Connection, user: sqlite3.Row, library_id: int) -> str | None:
    """返回 'manage' | 'edit' | 'view' | None（最高级别）。"""
    if user["is_admin"]:
        return "manage"
    rank = {"view": 1, "edit": 2, "manage": 3}
    best: str | None = None
    rows = conn.execute(
        "SELECT lg.access FROM library_grants lg "
        "JOIN user_groups ug ON ug.group_id=lg.group_id "
        "WHERE ug.user_id=? AND lg.library_id=?",
        (user["id"], library_id),
    ).fetchall()
    for r in rows:
        acc = r["access"]
        if acc in rank and (best is None or rank[acc] > rank[best]):
            best = acc
    return best


def visible_library_ids(conn: sqlite3.Connection, user: sqlite3.Row) -> list[int] | None:
    """None 表示不受限（管理员）。"""
    if user["is_admin"]:
        return None
    rows = conn.execute(
        "SELECT DISTINCT lg.library_id FROM library_grants lg "
        "JOIN user_groups ug ON ug.group_id=lg.group_id WHERE ug.user_id=?",
        (user["id"],),
    ).fetchall()
    return [int(r["library_id"]) for r in rows]


def grant_default_library(conn: sqlite3.Connection, library_id: int) -> None:
    for gname, access in (("admins", "manage"), ("members", "view")):
        row = conn.execute("SELECT id FROM groups WHERE name=?", (gname,)).fetchone()
        if row is None:
            continue
        conn.execute(
            "INSERT INTO library_grants(group_id, library_id, access) VALUES(?,?,?) "
            "ON CONFLICT(group_id, library_id) DO NOTHING",
            (row["id"], library_id, access),
        )
    conn.commit()
