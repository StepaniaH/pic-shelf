"""共享操作层：评分、收藏、标签、艺术家、评论、笔记、专辑。

API 路由与 CLI 均调用这里的函数，保证行为一致、便于单测。
调用方负责权限检查（API 层做；CLI 视为本地管理员权限）。
"""
from __future__ import annotations

import json
import sqlite3

from .db import utcnow


def _get_image(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if row is None:
        raise ValueError(f"图片 #{image_id} 不存在")
    return row


# ---------------------------------------------------------------- 评分

def set_rating(conn: sqlite3.Connection, image_id: int, user_id: int, value: int | None) -> dict:
    _get_image(conn, image_id)
    if value is None:
        conn.execute(
            "DELETE FROM ratings WHERE image_id=? AND user_id=?", (image_id, user_id)
        )
    else:
        value = int(value)
        if not 0 <= value <= 10:
            raise ValueError("评分范围 0–10")
        conn.execute(
            "INSERT INTO ratings(image_id, user_id, value, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(image_id, user_id) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (image_id, user_id, value, utcnow()),
        )
    conn.commit()
    return rating_summary(conn, image_id, user_id)


def rating_summary(conn: sqlite3.Connection, image_id: int, user_id: int) -> dict:
    row = conn.execute(
        "SELECT (SELECT value FROM ratings WHERE image_id=? AND user_id=?) AS mine, "
        "(SELECT ROUND(AVG(value),2) FROM ratings WHERE image_id=?) AS avg, "
        "(SELECT COUNT(*) FROM ratings WHERE image_id=?) AS count",
        (image_id, user_id, image_id, image_id),
    ).fetchone()
    return {"my_rating": row["mine"] if row["mine"] is not None else -1,
            "avg_rating": row["avg"], "rating_count": int(row["count"])}


# ---------------------------------------------------------------- 收藏

def set_favorite(conn: sqlite3.Connection, image_id: int, user_id: int, on: bool | None = None) -> bool:
    _get_image(conn, image_id)
    exists = conn.execute(
        "SELECT 1 FROM favorites WHERE image_id=? AND user_id=?", (image_id, user_id)
    ).fetchone()
    want = (not exists) if on is None else bool(on)
    if want and not exists:
        conn.execute(
            "INSERT INTO favorites(image_id, user_id, created_at) VALUES(?,?,?)",
            (image_id, user_id, utcnow()),
        )
    elif not want and exists:
        conn.execute(
            "DELETE FROM favorites WHERE image_id=? AND user_id=?", (image_id, user_id)
        )
    conn.commit()
    return want


# ---------------------------------------------------------------- 标签

def split_namespace(name: str) -> tuple[str | None, str]:
    if ":" in name:
        ns, rest = name.split(":", 1)
        ns, rest = ns.strip(), rest.strip()
        return (ns or None), (rest or name.strip())
    return None, name.strip()


def ensure_tag(conn: sqlite3.Connection, name: str, category: str | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("标签不能为空")
    ns, _ = split_namespace(name)
    row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO tags(name, namespace, category, created_at) VALUES(?,?,?,?)",
        (name, ns, category, utcnow()),
    )
    return int(cur.lastrowid)


def add_tags(conn: sqlite3.Connection, image_id: int, names: list[str], user_id: int) -> list[dict]:
    _get_image(conn, image_id)
    out = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        tag_id = ensure_tag(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO image_tags(image_id, tag_id, added_by, added_at) VALUES(?,?,?,?)",
            (image_id, tag_id, user_id, utcnow()),
        )
        out.append({"id": tag_id, "name": name})
    conn.commit()
    return out


def remove_tags(conn: sqlite3.Connection, image_id: int, names: list[str]) -> int:
    _get_image(conn, image_id)
    n = 0
    for raw in names:
        name = raw.strip()
        for row in conn.execute(
            "SELECT t.id FROM tags t JOIN image_tags it ON it.tag_id=t.id "
            "WHERE it.image_id=? AND (t.name=? OR CAST(t.id AS TEXT)=?)",
            (image_id, name, name),
        ).fetchall():
            n += conn.execute(
                "DELETE FROM image_tags WHERE image_id=? AND tag_id=?",
                (image_id, row["id"]),
            ).rowcount
    conn.commit()
    return n


def list_tags(conn: sqlite3.Connection, q: str | None = None, limit: int = 50) -> list[dict]:
    sql = (
        "SELECT t.id, t.name, t.namespace, COUNT(it.image_id) AS count "
        "FROM tags t LEFT JOIN image_tags it ON it.tag_id=t.id "
    )
    params: list = []
    if q:
        sql += "WHERE t.name LIKE ? "
        params.append(f"%{q}%")
    sql += "GROUP BY t.id ORDER BY count DESC, t.name LIMIT ?"
    params.append(max(1, min(limit, 500)))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def rename_tag(conn: sqlite3.Connection, tag_id: int, new_name: str) -> dict:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("新名称不能为空")
    exists = conn.execute("SELECT id FROM tags WHERE name=? AND id!=?", (new_name, tag_id)).fetchone()
    if exists:
        raise ValueError(f"标签已存在：{new_name}")
    ns, _ = split_namespace(new_name)
    conn.execute("UPDATE tags SET name=?, namespace=? WHERE id=?", (new_name, ns, tag_id))
    conn.commit()
    return {"id": tag_id, "name": new_name}


# ---------------------------------------------------------------- 艺术家

def create_artist(conn: sqlite3.Connection, name: str, *, aliases: str | None = None,
                  links: list[str] | None = None, note: str | None = None) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("艺术家名不能为空")
    row = conn.execute("SELECT id FROM artists WHERE lower(name)=lower(?)", (name,)).fetchone()
    if row:
        return get_artist(conn, int(row["id"]))
    cur = conn.execute(
        "INSERT INTO artists(name, aliases, links, note, created_at) VALUES(?,?,?,?,?)",
        (name, aliases, json.dumps(links or [], ensure_ascii=False), note, utcnow()),
    )
    conn.commit()
    return get_artist(conn, int(cur.lastrowid))


def update_artist(conn: sqlite3.Connection, artist_id: int, **fields) -> dict:
    allowed = {"name", "aliases", "links", "note"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "links" and isinstance(v, list):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k}=?")
        params.append(v)
    if sets:
        params.append(artist_id)
        conn.execute(f"UPDATE artists SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return get_artist(conn, artist_id)


def get_artist(conn: sqlite3.Connection, artist_id: int) -> dict:
    row = conn.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()
    if row is None:
        raise ValueError(f"艺术家 #{artist_id} 不存在")
    d = dict(row)
    try:
        d["links"] = json.loads(d.get("links") or "[]")
    except Exception:
        d["links"] = []
    d["image_count"] = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM image_artists WHERE artist_id=?", (artist_id,)
        ).fetchone()["n"]
    )
    return d


def list_artists(conn: sqlite3.Connection, q: str | None = None, limit: int = 100) -> list[dict]:
    sql = (
        "SELECT a.id, a.name, a.aliases, COUNT(ia.image_id) AS image_count "
        "FROM artists a LEFT JOIN image_artists ia ON ia.artist_id=a.id "
    )
    params: list = []
    if q:
        sql += "WHERE a.name LIKE ? OR COALESCE(a.aliases,'') LIKE ? "
        params += [f"%{q}%", f"%{q}%"]
    sql += "GROUP BY a.id ORDER BY image_count DESC, a.name LIMIT ?"
    params.append(max(1, min(limit, 500)))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def link_artist(conn: sqlite3.Connection, image_id: int, artist_id: int, on: bool = True) -> None:
    _get_image(conn, image_id)
    if on:
        conn.execute(
            "INSERT OR IGNORE INTO image_artists(image_id, artist_id) VALUES(?,?)",
            (image_id, artist_id),
        )
    else:
        conn.execute(
            "DELETE FROM image_artists WHERE image_id=? AND artist_id=?",
            (image_id, artist_id),
        )
    conn.commit()


# ---------------------------------------------------------------- 评论 / 笔记

def add_comment(conn: sqlite3.Connection, image_id: int, user_id: int, body: str) -> dict:
    _get_image(conn, image_id)
    body = (body or "").strip()
    if not body:
        raise ValueError("评论内容不能为空")
    cur = conn.execute(
        "INSERT INTO comments(image_id, user_id, body, created_at) VALUES(?,?,?,?)",
        (image_id, user_id, body, utcnow()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM comments WHERE id=?", (cur.lastrowid,)).fetchone())


def list_comments(conn: sqlite3.Connection, image_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT c.*, u.username FROM comments c JOIN users u ON u.id=c.user_id "
        "WHERE c.image_id=? AND c.deleted=0 ORDER BY c.id",
        (image_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_comment(conn: sqlite3.Connection, comment_id: int, user_id: int, *, is_admin: bool = False) -> None:
    row = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        raise ValueError(f"评论 #{comment_id} 不存在")
    if row["user_id"] != user_id and not is_admin:
        raise PermissionError("只能删除自己的评论")
    conn.execute("UPDATE comments SET deleted=1 WHERE id=?", (comment_id,))
    conn.commit()


def add_note(conn: sqlite3.Connection, image_id: int, user_id: int, body: str,
             geometry: str | None = None) -> dict:
    _get_image(conn, image_id)
    body = (body or "").strip()
    if not body:
        raise ValueError("笔记内容不能为空")
    cur = conn.execute(
        "INSERT INTO notes(image_id, user_id, body, geometry, created_at) VALUES(?,?,?,?,?)",
        (image_id, user_id, body, geometry, utcnow()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM notes WHERE id=?", (cur.lastrowid,)).fetchone())


def list_notes(conn: sqlite3.Connection, image_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT n.*, u.username FROM notes n JOIN users u ON u.id=n.user_id "
        "WHERE n.image_id=? AND n.deleted=0 ORDER BY n.id",
        (image_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_note(conn: sqlite3.Connection, note_id: int, user_id: int, *, is_admin: bool = False) -> None:
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if row is None:
        raise ValueError(f"笔记 #{note_id} 不存在")
    if row["user_id"] != user_id and not is_admin:
        raise PermissionError("只能删除自己的笔记")
    conn.execute("UPDATE notes SET deleted=1 WHERE id=?", (note_id,))
    conn.commit()


# ---------------------------------------------------------------- 专辑

def create_album(conn: sqlite3.Connection, name: str, library_id: int | None = None,
                 category: str | None = None, description: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("专辑名不能为空")
    cur = conn.execute(
        "INSERT INTO albums(library_id, name, kind, rel_dir, category, description, created_at) "
        "VALUES(?,?, 'manual', NULL, ?, ?, ?)",
        (library_id, name, category, description, utcnow()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM albums WHERE id=?", (cur.lastrowid,)).fetchone())


def update_album(conn: sqlite3.Connection, album_id: int, **fields) -> dict:
    allowed = {"name", "category", "description", "cover_image_id"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        sets.append(f"{k}=?")
        params.append(v)
    if sets:
        params.append(album_id)
        conn.execute(f"UPDATE albums SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    row = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if row is None:
        raise ValueError(f"专辑 #{album_id} 不存在")
    return dict(row)


def album_add_images(conn: sqlite3.Connection, album_id: int, image_ids: list[int]) -> int:
    row = conn.execute("SELECT kind FROM albums WHERE id=?", (album_id,)).fetchone()
    if row is None:
        raise ValueError(f"专辑 #{album_id} 不存在")
    if row["kind"] == "folder":
        raise ValueError("文件夹专辑自动维护，不能手工增删；请创建手工专辑")
    n = 0
    for iid in image_ids:
        n += conn.execute(
            "INSERT OR IGNORE INTO album_images(album_id, image_id, position, added_at) "
            "VALUES(?,?,0,?)",
            (album_id, int(iid), utcnow()),
        ).rowcount
    conn.commit()
    return n


def album_remove_images(conn: sqlite3.Connection, album_id: int, image_ids: list[int]) -> int:
    row = conn.execute("SELECT kind FROM albums WHERE id=?", (album_id,)).fetchone()
    if row is None:
        raise ValueError(f"专辑 #{album_id} 不存在")
    if row["kind"] == "folder":
        raise ValueError("文件夹专辑自动维护，不能手工增删")
    n = 0
    for iid in image_ids:
        n += conn.execute(
            "DELETE FROM album_images WHERE album_id=? AND image_id=?",
            (album_id, int(iid)),
        ).rowcount
    conn.commit()
    return n


def get_album(conn: sqlite3.Connection, album_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["image_count"] = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM album_images WHERE album_id=?", (album_id,)
        ).fetchone()["n"]
    )
    if d.get("library_id"):
        lib = conn.execute(
            "SELECT name FROM libraries WHERE id=?", (d["library_id"],)
        ).fetchone()
        d["library_name"] = lib["name"] if lib else None
    else:
        d["library_name"] = None
    return d


def list_albums(conn: sqlite3.Connection, *, library_id: int | None = None,
                allowed_library_ids: list[int] | None = None,
                kind: str | None = None) -> list[dict]:
    sql = (
        "SELECT al.*, (SELECT COUNT(*) FROM album_images ai WHERE ai.album_id=al.id) AS image_count, "
        "(SELECT name FROM libraries l WHERE l.id=al.library_id) AS library_name "
        "FROM albums al WHERE 1=1"
    )
    params: list = []
    if library_id is not None:
        sql += " AND al.library_id=?"
        params.append(library_id)
    if allowed_library_ids is not None:
        if not allowed_library_ids:
            return []
        marks = ",".join("?" * len(allowed_library_ids))
        sql += f" AND al.library_id IN ({marks})"
        params += allowed_library_ids
    if kind:
        sql += " AND al.kind=?"
        params.append(kind)
    sql += " ORDER BY al.library_id, al.kind DESC, al.name"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
