"""库管理：注册、列举、统计；以及重复检测（精确 sha256 / 相似 pHash）。"""
from __future__ import annotations

import sqlite3

from . import search
from .auth import grant_default_library
from .db import utcnow
from .i18n import t


def create_library(conn: sqlite3.Connection, name: str, root_path: str,
                   description: str | None = None) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError(t("库名不能为空"))
    cur = conn.execute(
        "INSERT INTO libraries(name, root_path, description, created_at) VALUES(?,?,?,?)",
        (name, str(root_path), description, utcnow()),
    )
    library_id = int(cur.lastrowid)
    conn.commit()
    grant_default_library(conn, library_id)
    return library_id


def get_library(conn: sqlite3.Connection, library_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    return dict(row) if row else None


def find_library(conn: sqlite3.Connection, name_or_id: str) -> dict | None:
    if str(name_or_id).isdigit():
        return get_library(conn, int(name_or_id))
    row = conn.execute(
        "SELECT * FROM libraries WHERE name=?", (name_or_id,)
    ).fetchone()
    return dict(row) if row else None


def list_libraries(conn: sqlite3.Connection, allowed: list[int] | None = None) -> list[dict]:
    sql = (
        "SELECT l.*, "
        "(SELECT COUNT(*) FROM images i WHERE i.library_id=l.id AND i.missing=0) AS image_count, "
        "(SELECT COUNT(*) FROM albums a WHERE a.library_id=l.id) AS album_count "
        "FROM libraries l"
    )
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" WHERE l.id IN ({marks})"
        params += allowed
    sql += " ORDER BY l.id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def global_stats(conn: sqlite3.Connection, allowed: list[int] | None = None) -> dict:
    visible, params = search.visible_images_sql(allowed)
    prefix = f"WITH visible AS ({visible}) "

    def one(sql: str) -> int:
        row = conn.execute(prefix + sql, params).fetchone()
        return int(row[0] or 0)

    library_filter = ""
    if allowed is not None:
        ids = ",".join(str(int(x)) for x in allowed)
        library_filter = f" AND id IN ({ids})" if ids else " AND 0=1"

    return {
        "images": one("SELECT COUNT(*) FROM visible"),
        "size_bytes": one("SELECT SUM(size_bytes) FROM visible"),
        "libraries": one("SELECT COUNT(*) FROM libraries WHERE 1=1" + library_filter),
        "albums": one("SELECT COUNT(*) FROM albums WHERE library_id IN (SELECT id FROM libraries WHERE 1=1" + library_filter + ")"),
        "artists": one("SELECT COUNT(*) FROM artists") if allowed is None else one(
            "SELECT COUNT(DISTINCT artist_id) FROM image_artists WHERE image_id IN (SELECT id FROM visible)"),
        "tags": one("SELECT COUNT(*) FROM tags") if allowed is None else one(
            "SELECT COUNT(DISTINCT tag_id) FROM image_tags WHERE image_id IN (SELECT id FROM visible)"),
        "users": one("SELECT COUNT(*) FROM users WHERE disabled=0") if allowed is None else 0,
        "favorites": one("SELECT COUNT(*) FROM favorites WHERE image_id IN (SELECT id FROM visible)"),
        "ratings": one("SELECT COUNT(*) FROM ratings WHERE image_id IN (SELECT id FROM visible)"),
    }


def exact_dupes(conn: sqlite3.Connection, allowed: list[int] | None = None) -> list[dict]:
    sql = (
        "SELECT sha256, COUNT(*) AS n, SUM(size_bytes) AS bytes, "
        "GROUP_CONCAT(id) AS ids FROM images WHERE missing=0 AND sha256 IS NOT NULL"
    )
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" AND library_id IN ({marks})"
        params += allowed
    sql += " GROUP BY sha256 HAVING COUNT(*) > 1 ORDER BY n DESC"
    out = []
    for r in conn.execute(sql, params).fetchall():
        ids = [int(x) for x in str(r["ids"]).split(",")]
        out.append({"sha256": r["sha256"], "count": int(r["n"]),
                    "size_bytes": int(r["bytes"] or 0), "image_ids": ids})
    return out


def similar_dupes(conn: sqlite3.Connection, allowed: list[int] | None = None,
                  threshold: int = 8) -> list[dict]:
    """按首个代表图分组；每个成员与代表图的距离不超过 threshold。

    每个哈希只解析一次，重复哈希直接命中已选组。最坏仍为 O(n²)。
    """
    if not 0 <= threshold <= 64:
        raise ValueError(t("相似距离必须在 0–64 之间"))
    sql = "SELECT id, phash, filename FROM images WHERE missing=0 AND phash IS NOT NULL"
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" AND library_id IN ({marks})"
        params += allowed
    rows = conn.execute(sql + " ORDER BY id", params).fetchall()
    groups: list[list[dict]] = []
    representatives: list[int] = []
    placed: dict[int, int] = {}
    for r in rows:
        ph = int(r["phash"], 16)
        target = placed.get(ph)
        if target is None:
            for gi, representative in enumerate(representatives):
                if (representative ^ ph).bit_count() <= threshold:
                    target = gi
                    break
        entry = {"id": int(r["id"]), "phash": ph, "filename": r["filename"]}
        if target is None:
            target = len(groups)
            representatives.append(ph)
            groups.append([entry])
        else:
            groups[target].append(entry)
        placed[ph] = target
    return [
        {"count": len(g), "images": [{"id": e["id"], "filename": e["filename"],
                                      "distance": (g[0]["phash"] ^ e["phash"]).bit_count()}
                                     for e in g]}
        for g in groups if len(g) > 1
    ]
