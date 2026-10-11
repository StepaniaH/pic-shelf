"""播放会话：把过滤条件物化为有序队列；提供 next / prev / goto / reshuffle。

设计：服务端只负责「库感知队列」（跨专辑/按艺术家/tag/评分/收藏 + 顺序/乱序/定量/循环），
实际渲染交给执行层（Web 幻灯片 / mpv / 终端渲染）。
"""
from __future__ import annotations

import json
import secrets
import sqlite3

from . import search
from .db import utcnow

MAX_QUEUE = 10000


def _source_clauses(spec: dict) -> tuple[list[str], dict]:
    src = spec.get("source") or {}
    where: list[str] = []
    params: dict = {}
    if src.get("library_id"):
        where.append("i.library_id = :src_library")
        params["src_library"] = int(src["library_id"])
    if src.get("album_id"):
        where.append(
            "EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id "
            "AND ai.album_id = :src_album)"
        )
        params["src_album"] = int(src["album_id"])
    if src.get("artist_id"):
        where.append(
            "EXISTS(SELECT 1 FROM image_artists ia WHERE ia.image_id=i.id "
            "AND ia.artist_id = :src_artist)"
        )
        params["src_artist"] = int(src["artist_id"])
    if src.get("tag"):
        where.append(
            "EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
            "WHERE it.image_id=i.id AND lower(tg.name)=lower(:src_tag))"
        )
        params["src_tag"] = src["tag"]
    if src.get("favorites"):
        where.append(
            "EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)"
        )
    if src.get("rating_min") is not None:
        where.append(
            "COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id "
            "AND r.user_id=:uid),0) >= :src_rating_min"
        )
        params["src_rating_min"] = float(src["rating_min"])
    return where, params


def build_queue(
    conn: sqlite3.Connection,
    user_id: int,
    allowed_library_ids: list[int] | None,
    spec: dict,
) -> list[int]:
    order = spec.get("order", "sequential")
    sort = {"sequential": "path", "shuffle": "random", "random": "random"}.get(order, "path")
    limit = int(spec.get("limit") or 0)
    limit = min(limit, MAX_QUEUE) if limit > 0 else MAX_QUEUE
    where, params = _source_clauses(spec)
    res = search.search_images(
        conn, user_id, allowed_library_ids,
        query=spec.get("query") or "",
        sort=sort, limit=limit, offset=0,
        extra_where=where or None, extra_params=params or None,
    )
    return [int(it["id"]) for it in res["items"]]


def create_session(
    conn: sqlite3.Connection,
    user_id: int,
    allowed_library_ids: list[int] | None,
    spec: dict,
) -> dict:
    items = build_queue(conn, user_id, allowed_library_ids, spec)
    token = secrets.token_urlsafe(12)
    cur = conn.execute(
        "INSERT INTO play_sessions(token, user_id, spec, order_mode, interval, loop, cursor, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            token, user_id, json.dumps(spec, ensure_ascii=False),
            spec.get("order", "sequential"),
            int(spec.get("interval") or 0),
            1 if spec.get("loop") else 0,
            -1, utcnow(),
        ),
    )
    session_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO play_items(session_id, position, image_id) VALUES(?,?,?)",
        [(session_id, pos, iid) for pos, iid in enumerate(items)],
    )
    conn.commit()
    return {
        "session_id": session_id,
        "token": token,
        "total": len(items),
        "order": spec.get("order", "sequential"),
        "interval": int(spec.get("interval") or 0),
        "loop": bool(spec.get("loop")),
        "cursor": -1,
    }


def get_session_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM play_sessions WHERE token=?", (token,)).fetchone()


def _item_count(conn: sqlite3.Connection, session_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM play_items WHERE session_id=?", (session_id,)
        ).fetchone()["n"]
    )


def _cursor_item(conn: sqlite3.Connection, session_id: int, cursor: int) -> int | None:
    row = conn.execute(
        "SELECT image_id FROM play_items WHERE session_id=? AND position=?",
        (session_id, cursor),
    ).fetchone()
    return int(row["image_id"]) if row else None


def current(
    conn: sqlite3.Connection,
    session: sqlite3.Row,
    user_id: int,
    allowed_library_ids: list[int] | None,
) -> dict:
    total = _item_count(conn, session["id"])
    return {"total": total, "cursor": int(session["cursor"]), "loop": bool(session["loop"])}


def step(
    conn: sqlite3.Connection,
    session: sqlite3.Row,
    delta: int,
) -> dict:
    total = _item_count(conn, int(session["id"]))
    if total == 0:
        return {"done": True, "cursor": -1, "total": 0, "position": None}
    cursor = int(session["cursor"])
    loop = bool(session["loop"])
    new_cursor = cursor + delta
    done = False
    if new_cursor < 0 or new_cursor >= total:
        if loop:
            new_cursor %= total
        else:
            new_cursor = max(0, min(new_cursor, total - 1))
            done = True
    conn.execute(
        "UPDATE play_sessions SET cursor=? WHERE id=?", (new_cursor, session["id"])
    )
    conn.commit()
    return {
        "done": done and new_cursor == cursor,
        "cursor": new_cursor,
        "total": total,
        "position": new_cursor + 1,
        "image_id": _cursor_item(conn, int(session["id"]), new_cursor),
    }


def goto(conn: sqlite3.Connection, session: sqlite3.Row, position: int) -> dict:
    total = _item_count(conn, int(session["id"]))
    if total == 0:
        return {"done": True, "cursor": -1, "total": 0, "image_id": None}
    cursor = max(0, min(int(position), total - 1))
    conn.execute("UPDATE play_sessions SET cursor=? WHERE id=?", (cursor, session["id"]))
    conn.commit()
    return {
        "cursor": cursor, "total": total, "position": cursor + 1,
        "image_id": _cursor_item(conn, int(session["id"]), cursor),
    }


def reshuffle(
    conn: sqlite3.Connection,
    session: sqlite3.Row,
    user_id: int,
    allowed_library_ids: list[int] | None,
) -> dict:
    spec = json.loads(session["spec"])
    items = build_queue(conn, user_id, allowed_library_ids, spec)
    conn.execute("DELETE FROM play_items WHERE session_id=?", (session["id"],))
    conn.executemany(
        "INSERT INTO play_items(session_id, position, image_id) VALUES(?,?,?)",
        [(session["id"], pos, iid) for pos, iid in enumerate(items)],
    )
    conn.execute("UPDATE play_sessions SET cursor=-1 WHERE id=?", (session["id"],))
    conn.commit()
    return {"total": len(items), "cursor": -1}


def queue_items(conn: sqlite3.Connection, session_id: int, offset: int = 0, limit: int = 50) -> list[int]:
    rows = conn.execute(
        "SELECT image_id FROM play_items WHERE session_id=? ORDER BY position LIMIT ? OFFSET ?",
        (session_id, max(0, limit), max(0, offset)),
    ).fetchall()
    return [int(r["image_id"]) for r in rows]
