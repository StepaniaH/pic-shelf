"""库管理：注册、列举、统计；以及重复检测（精确 sha256 / 相似 pHash）。"""
from __future__ import annotations

import sqlite3

from . import hashing
from .auth import grant_default_library
from .db import utcnow


def create_library(conn: sqlite3.Connection, name: str, root_path: str,
                   description: str | None = None) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("库名不能为空")
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
    where = ""
    params: list = []
    if allowed is not None:
        if not allowed:
            return {"images": 0, "libraries": 0, "albums": 0, "artists": 0,
                    "tags": 0, "users": 0, "favorites": 0, "ratings": 0, "size_bytes": 0}
        marks = ",".join("?" * len(allowed))
        where = f" AND library_id IN ({marks})"
        params = list(allowed)

    def one(sql: str, extra: list | None = None) -> int:
        row = conn.execute(sql, (extra or []) + params).fetchone()
        return int(row[0] or 0)

    return {
        "images": one("SELECT COUNT(*) FROM images WHERE missing=0" + where),
        "size_bytes": one("SELECT COALESCE(SUM(size_bytes),0) FROM images WHERE missing=0" + where),
        "libraries": one("SELECT COUNT(*) FROM libraries WHERE 1=1" + (
            "" if allowed is None else
            (" AND id IN (" + ",".join(str(int(x)) for x in allowed) + ")")
        )),
        "albums": one("SELECT COUNT(*) FROM albums WHERE library_id IS NOT NULL" + where),
        "artists": one("SELECT COUNT(*) FROM artists"),
        "tags": one("SELECT COUNT(*) FROM tags"),
        "users": one("SELECT COUNT(*) FROM users WHERE disabled=0"),
        "favorites": one("SELECT COUNT(*) FROM favorites"),
        "ratings": one("SELECT COUNT(*) FROM ratings"),
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
    """pHash 汉明距离 ≤ threshold 视为相似。O(n²)，适合中等库。"""
    sql = "SELECT id, phash, filename FROM images WHERE missing=0 AND phash IS NOT NULL"
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" AND library_id IN ({marks})"
        params += allowed
    rows = conn.execute(sql, params).fetchall()
    groups: list[list[dict]] = []
    placed: dict[int, int] = {}
    for r in rows:
        ph = r["phash"]
        target = None
        for gi, group in enumerate(groups):
            if hashing.hamming(group[0]["phash"], ph) <= threshold:
                target = gi
                break
        entry = {"id": int(r["id"]), "phash": ph, "filename": r["filename"]}
        if target is None:
            groups.append([entry])
        else:
            groups[target].append(entry)
            placed[int(r["id"])] = target
    return [
        {"count": len(g), "images": [{"id": e["id"], "filename": e["filename"],
                                      "distance": hashing.hamming(g[0]["phash"], e["phash"])}
                                     for e in g]}
        for g in groups if len(g) > 1
    ]
