"""认证与授权：口令哈希（scrypt）、会话令牌（HMAC）、组能力与库级授权。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import tempfile
from functools import lru_cache
from pathlib import Path

from .db import utcnow
from .i18n import t
from .errors import ServiceError

TOKEN_TTL_DEFAULT = 30 * 24 * 3600  # 30 天

ALL_CAPS = ("view", "rate", "comment", "note", "tag_edit", "manage_library", "admin")
DEFAULT_MEMBER_CAPS = ("view", "rate", "comment", "note", "tag_edit")


# ---------------------------------------------------------------- 口令

def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    if not password:
        raise ValueError(t("口令不能为空"))
    if len(password) > 1024:
        raise ValueError(t("口令不能超过 1024 个字符"))
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        if len(password) > 1024 or len(stored) > 512:
            return False
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if (scheme != "scrypt" or not 2 <= int(n) <= 2**16 or not 1 <= int(r) <= 8
                or not 1 <= int(p) <= 4 or not 16 <= len(salt_hex) <= 64 or len(hash_hex) != 64):
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(hash_hex) // 2, maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- 令牌

def load_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not path.exists():
            # Publish a fully written owner-only file without replacing a key
            # installed by another starting process.
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".secret-") as candidate:
                candidate.write(secrets.token_bytes(32))
                candidate.flush()
                os.fsync(candidate.fileno())
                try:
                    os.link(candidate.name, path)
                except FileExistsError:
                    pass
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            secret = stream.read(33)
        if len(secret) != 32:
            raise ValueError
        return secret
    except (OSError, ValueError) as exc:
        raise ServiceError("session_key_invalid", t("会话密钥不可读或格式无效"),
                           next_step=t("请检查数据目录权限，并从备份恢复原有密钥；不要用空文件替代")) from exc


def sign_token(secret: bytes, user_id: int, ttl: int = TOKEN_TTL_DEFAULT, *, version: int = 0) -> str:
    payload = json.dumps(
        {"uid": user_id, "exp": int(time.time()) + ttl, "n": secrets.token_hex(8), "ver": version},
        separators=(",", ":"),
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _token_data(secret: bytes, token: str) -> dict | None:
    try:
        if len(token) > 4096:
            return None
        body, sig = token.rsplit(".", 1)
        expect = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + pad))
        if (type(data.get("uid")) is not int or data["uid"] < 1 or type(data.get("exp")) is not int or
                type(data.get("ver")) is not int or data["ver"] < 0 or data["exp"] <= time.time()):
            return None
        return data
    except Exception:
        return None


def verify_token(secret: bytes, token: str, conn: sqlite3.Connection | None = None) -> int | None:
    data = _token_data(secret, token)
    if data is None:
        return None
    if conn is not None:
        user = get_user(conn, data["uid"])
        digest = hashlib.sha256(token.encode()).hexdigest()
        if (user is None or user["token_version"] != data["ver"] or
                conn.execute("SELECT 1 FROM revoked_tokens WHERE digest=?", (digest,)).fetchone()):
            return None
    return data["uid"]


def revoke_token(conn, secret, token):
    data = _token_data(secret, token)
    if data is None:
        return
    with conn:
        conn.execute("INSERT OR IGNORE INTO revoked_tokens VALUES(?,?)", (hashlib.sha256(token.encode()).hexdigest(), data["exp"]))
        conn.execute("DELETE FROM revoked_tokens WHERE digest IN (SELECT digest FROM revoked_tokens WHERE expires_at<? LIMIT 500)", (int(time.time()),))


def revoke_sessions(conn, user_id):
    conn.execute("UPDATE users SET token_version=token_version+1 WHERE id=?", (user_id,))
    conn.commit()


def set_password(conn, user_id, password):
    encoded = hash_password(password)
    conn.execute("UPDATE users SET password_hash=?,token_version=token_version+1 WHERE id=?", (encoded, user_id))
    conn.commit()


@lru_cache(maxsize=1)
def _dummy_hash():
    return hash_password(secrets.token_urlsafe(24))


def login_buckets(secret, username, client):
    # Store keyed identifiers rather than raw usernames or client addresses.
    return [hmac.new(secret, value.encode(), hashlib.sha256).hexdigest() for value in
            ("user:" + username.strip().casefold(), "client:" + client)]


def check_login_limit(conn, buckets):
    now = int(time.time())
    for bucket, limit in zip(buckets, (5, 30)):
        row = conn.execute("SELECT * FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
        if row and now < row["window_start"] + 60 and row["failures"] >= limit:
            retry = max(1, row["window_start"] + 60 - now)
            error = ServiceError("login_limited", t("登录尝试过多，请稍后重试"), status=429, retryable=True,
                                 next_step=t("请等待一分钟后重试，或联系管理员检查账户"))
            error.retry_after = retry
            raise error


def record_login_failure(conn, buckets):
    now = int(time.time())
    with conn:
        for bucket in buckets:
            conn.execute("INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET "
                         "failures=CASE WHEN window_start<=? THEN 1 ELSE failures+1 END,"
                         "window_start=CASE WHEN window_start<=? THEN excluded.window_start ELSE window_start END",
                         (bucket, now, now-60, now-60))
        conn.execute("DELETE FROM login_attempts WHERE bucket IN (SELECT bucket FROM login_attempts WHERE window_start<? LIMIT 500)", (now-60,))


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
    if not has_cap(conn, user, "view"):
        return None
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


def visible_library_ids(conn: sqlite3.Connection, user: sqlite3.Row, *, minimum: str = "view") -> list[int] | None:
    """None 表示不受限（管理员）。"""
    levels = {"view": ("view", "edit", "manage"), "edit": ("edit", "manage"), "manage": ("manage",)}[minimum]
    if user["is_admin"]:
        return None
    if not has_cap(conn, user, "view"):
        return []
    rows = conn.execute(
        "SELECT DISTINCT lg.library_id FROM library_grants lg "
        "JOIN user_groups ug ON ug.group_id=lg.group_id WHERE ug.user_id=? "
        f"AND lg.access IN ({','.join('?' for _ in levels)})",
        (user["id"], *levels),
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
