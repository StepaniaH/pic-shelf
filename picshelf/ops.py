"""共享操作层：评分、收藏、标签、艺术家、评论、笔记、专辑。

API 路由与 CLI 均调用这里的函数，保证行为一致、便于单测。
调用方负责权限检查（API 层做；CLI 视为本地管理员权限）。
"""
from __future__ import annotations

import json
import sqlite3

from .db import transaction, utcnow
from .i18n import t
from .errors import ServiceError
from .search import visible_images_sql
from . import names as entity_names


def _get_image(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if row is None:
        raise ValueError(t("图片 #{image_id} 不存在", image_id=image_id))
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
            raise ValueError(t("评分范围 0–10"))
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
        raise ValueError(t("标签不能为空"))
    resolved = entity_names.resolve(conn, "tag", name)
    if resolved is not None:
        return resolved
    name = entity_names.display(name)
    ns, _ = split_namespace(name)
    cur = conn.execute(
        "INSERT INTO tags(name, namespace, category, created_at) VALUES(?,?,?,?)",
        (name, ns, category, utcnow()),
    )
    return int(cur.lastrowid)


def add_tags(conn: sqlite3.Connection, image_id: int, names: list[str], user_id: int) -> list[dict]:
    from .metadata import record_manual_tag
    _get_image(conn, image_id)
    validated = []
    for raw in names:
        name = raw.strip()
        if name:
            entity_names.display(name)
            validated.append(name)
    out = []
    with transaction(conn):
        for name in validated:
            tag_id = ensure_tag(conn, name)
            conn.execute(
                "INSERT OR IGNORE INTO image_tags(image_id, tag_id, added_by, added_at) VALUES(?,?,?,?)",
                (image_id, tag_id, user_id, utcnow()),
            )
            record_manual_tag(conn, image_id, tag_id, user_id, utcnow())
            display_name = conn.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()[0]
            out.append({"id": tag_id, "name": display_name})
    return out


def remove_tags(conn: sqlite3.Connection, image_id: int, names: list[str]) -> int:
    _get_image(conn, image_id)
    n = 0
    for raw in names:
        name = raw.strip()
        target = entity_names.resolve(conn, "tag", name)
        if target is None and name.isdecimal():
            target = int(name)
        for row in conn.execute(
            "SELECT tag_id AS id FROM image_tags WHERE image_id=? AND tag_id=?",
            (image_id, target),
        ).fetchall():
            n += conn.execute(
                "DELETE FROM image_tags WHERE image_id=? AND tag_id=?",
                (image_id, row["id"]),
            ).rowcount
            conn.execute("UPDATE tag_claims SET retired_at=? WHERE image_id=? AND tag_id=? AND retired_at IS NULL",
                         (utcnow(), image_id, row["id"]))
    conn.commit()
    return n


def list_tags(conn: sqlite3.Connection, q: str | None = None, limit: int = 50,
              allowed_library_ids: list[int] | None = None, *, offset: int = 0) -> list[dict]:
    visible, params = visible_images_sql(allowed_library_ids)
    sql = (
        f"WITH visible AS ({visible}) "
        "SELECT t.id, t.name, t.namespace, COUNT(i.id) AS count "
        "FROM tags t LEFT JOIN image_tags it ON it.tag_id=t.id "
        "LEFT JOIN visible i ON i.id=it.image_id "
    )
    if q:
        sql += "WHERE picshelf_name_key(t.name) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM tag_aliases a WHERE a.entity_id=t.id AND a.match_key LIKE ? ESCAPE '\\') "
        params += [entity_names.contains_pattern(q)] * 2
    sql += "GROUP BY t.id "
    if allowed_library_ids is not None:
        sql += "HAVING COUNT(i.id)>0 "
    sql += "ORDER BY count DESC, t.name, t.id LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 500)), max(0, offset)])
    result = [{**dict(row), "name_aliases": []} for row in conn.execute(sql, params)]
    if result:
        by_id = {item["id"]: item for item in result}
        marks = ",".join("?" for _ in by_id)
        for row in conn.execute(f"SELECT entity_id,name FROM tag_aliases WHERE entity_id IN ({marks}) ORDER BY id", list(by_id)):
            by_id[row["entity_id"]]["name_aliases"].append(row["name"])
    return result


def rename_tag(conn: sqlite3.Connection, tag_id: int, new_name: str) -> dict:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError(t("新名称不能为空"))
    new_name = entity_names.display(new_name)
    with transaction(conn):
        current = conn.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()
        if current is None:
            raise ServiceError("entity_not_found", t("标签或艺术家不存在"), status=404,
                               next_step=t("请刷新目录并重新选择条目"))
        if current["name"] != new_name and any(iid != tag_id for iid in entity_names.find(conn, "tag", new_name)):
            raise ValueError(t("标签已存在：{new_name}", new_name=new_name))
        ns, _ = split_namespace(new_name)
        conn.execute("UPDATE tags SET name=?, namespace=? WHERE id=?", (new_name, ns, tag_id))
    return {"id": tag_id, "name": new_name}


# ---------------------------------------------------------------- 艺术家

def create_artist(conn: sqlite3.Connection, name: str, *, aliases: str | None = None,
                  links: list[str] | None = None, note: str | None = None) -> dict:
    name = name.strip()
    if not name:
        raise ValueError(t("艺术家名不能为空"))
    resolved = entity_names.resolve(conn, "artist", name)
    if resolved is not None:
        return get_artist(conn, resolved)
    name = entity_names.display(name)
    cur = conn.execute(
        "INSERT INTO artists(name, aliases, links, note, created_at) VALUES(?,?,?,?,?)",
        (name, aliases, json.dumps(links or [], ensure_ascii=False), note, utcnow()),
    )
    conn.commit()
    return get_artist(conn, int(cur.lastrowid))


def update_artist(conn: sqlite3.Connection, artist_id: int, **fields) -> dict:
    if fields.get("name") is not None:
        fields["name"] = fields["name"].strip()
        if not fields["name"]:
            raise ValueError(t("艺术家名不能为空"))
        fields["name"] = entity_names.display(fields["name"])
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


def get_artist(conn: sqlite3.Connection, artist_id: int,
               allowed_library_ids: list[int] | None = None, *, include_unlinked: bool = False) -> dict:
    row = conn.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()
    if row is None:
        raise ValueError(t("艺术家 #{artist_id} 不存在", artist_id=artist_id))
    d = dict(row)
    d["name_aliases"] = entity_names.aliases(conn, "artist", artist_id)
    try:
        d["links"] = json.loads(d.get("links") or "[]")
    except Exception:
        d["links"] = []
    visible, params = visible_images_sql(allowed_library_ids)
    d["image_count"] = int(
        conn.execute(
            f"WITH visible AS ({visible}) SELECT COUNT(*) AS n FROM image_artists "
            "WHERE artist_id=? AND image_id IN (SELECT id FROM visible)", [*params, artist_id]
        ).fetchone()["n"]
    )
    if allowed_library_ids is not None and not d["image_count"]:
        linked = conn.execute("SELECT 1 FROM image_artists WHERE artist_id=? LIMIT 1", (artist_id,)).fetchone()
        if not include_unlinked or linked:
            raise ValueError(t("艺术家 #{artist_id} 不存在", artist_id=artist_id))
    return d


def list_artists(conn: sqlite3.Connection, q: str | None = None, limit: int = 100,
                 allowed_library_ids: list[int] | None = None, *, offset: int = 0) -> list[dict]:
    visible, params = visible_images_sql(allowed_library_ids)
    sql = (
        f"WITH visible AS ({visible}) "
        "SELECT a.id, a.name, a.aliases, COUNT(i.id) AS image_count "
        "FROM artists a LEFT JOIN image_artists ia ON ia.artist_id=a.id "
        "LEFT JOIN visible i ON i.id=ia.image_id "
    )
    if q:
        sql += "WHERE picshelf_name_key(a.name) LIKE ? ESCAPE '\\' OR picshelf_name_key(a.aliases) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM artist_aliases aa WHERE aa.entity_id=a.id AND aa.match_key LIKE ? ESCAPE '\\') "
        params += [entity_names.contains_pattern(q)] * 3
    sql += "GROUP BY a.id "
    if allowed_library_ids is not None:
        sql += "HAVING COUNT(i.id)>0 "
    sql += "ORDER BY image_count DESC, a.name, a.id LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 500)), max(0, offset)])
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
        raise ValueError(t("评论内容不能为空"))
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
        raise ValueError(t("评论 #{comment_id} 不存在", comment_id=comment_id))
    if row["user_id"] != user_id and not is_admin:
        raise PermissionError(t("只能删除自己的评论"))
    conn.execute("UPDATE comments SET deleted=1 WHERE id=?", (comment_id,))
    conn.commit()


def add_note(conn: sqlite3.Connection, image_id: int, user_id: int, body: str,
             geometry: str | None = None) -> dict:
    _get_image(conn, image_id)
    body = (body or "").strip()
    if not body:
        raise ValueError(t("笔记内容不能为空"))
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
        raise ValueError(t("笔记 #{note_id} 不存在", note_id=note_id))
    if row["user_id"] != user_id and not is_admin:
        raise PermissionError(t("只能删除自己的笔记"))
    conn.execute("UPDATE notes SET deleted=1 WHERE id=?", (note_id,))
    conn.commit()


# ---------------------------------------------------------------- 专辑

def create_album(conn: sqlite3.Connection, name: str, library_id: int | None = None,
                 category: str | None = None, description: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError(t("专辑名不能为空"))
    name = entity_names.display(name)
    cur = conn.execute(
        "INSERT INTO albums(library_id, name, kind, rel_dir, category, description, created_at) "
        "VALUES(?,?, 'manual', NULL, ?, ?, ?)",
        (library_id, name, category, description, utcnow()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM albums WHERE id=?", (cur.lastrowid,)).fetchone())


def update_album(conn: sqlite3.Connection, album_id: int, **fields) -> dict:
    if fields.get("name") is not None:
        fields["name"] = entity_names.display(fields["name"])
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
        raise ValueError(t("专辑 #{album_id} 不存在", album_id=album_id))
    return dict(row)


def album_add_images(conn: sqlite3.Connection, album_id: int, image_ids: list[int]) -> int:
    row = conn.execute("SELECT kind FROM albums WHERE id=?", (album_id,)).fetchone()
    if row is None:
        raise ValueError(t("专辑 #{album_id} 不存在", album_id=album_id))
    if row["kind"] == "folder":
        raise ValueError(t("文件夹专辑自动维护，不能手工增删；请创建手工专辑"))
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
        raise ValueError(t("专辑 #{album_id} 不存在", album_id=album_id))
    if row["kind"] == "folder":
        raise ValueError(t("文件夹专辑自动维护，不能手工增删"))
    n = 0
    for iid in image_ids:
        n += conn.execute(
            "DELETE FROM album_images WHERE album_id=? AND image_id=?",
            (album_id, int(iid)),
        ).rowcount
    conn.commit()
    return n


def get_album(conn: sqlite3.Connection, album_id: int,
              allowed_library_ids: list[int] | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    visible, params = visible_images_sql(allowed_library_ids)
    d["image_count"] = int(
        conn.execute(
            f"WITH visible AS ({visible}) SELECT COUNT(*) AS n FROM album_images ai "
            "JOIN visible i ON i.id=ai.image_id WHERE ai.album_id=?", [*params, album_id]
        ).fetchone()["n"]
    )
    if d["cover_image_id"] is not None and not conn.execute(
        f"WITH visible AS ({visible}) SELECT 1 FROM visible WHERE id=? AND EXISTS(SELECT 1 FROM album_images WHERE album_id=? AND image_id=visible.id)", [*params, d["cover_image_id"], album_id],
    ).fetchone():
        d["cover_image_id"] = None
    if d.get("library_id"):
        lib = conn.execute(
            "SELECT name FROM libraries WHERE id=?", (d["library_id"],)
        ).fetchone()
        d["library_name"] = lib["name"] if lib else None
    else:
        d["library_name"] = None
    return d


def list_albums(conn: sqlite3.Connection, *, library_id: int | None = None,
                allowed_library_ids: list[int] | None = None, kind: str | None = None,
                q: str | None = None, limit: int | None = None, offset: int = 0,
                for_edit: bool = False) -> list[dict]:
    # Page album identities before reading membership counts and visible covers.
    params = {}; clauses = []; scope = "i.missing=0"
    if library_id is not None:
        clauses.append("al.library_id=:library"); params["library"] = library_id
    if allowed_library_ids is not None:
        if not allowed_library_ids: return []
        params.update({f"lib{n}": lid for n,lid in enumerate(allowed_library_ids)})
        marks = ','.join(f":lib{n}" for n in range(len(allowed_library_ids)))
        scope += f" AND i.library_id IN ({marks})"
        own = f"al.library_id IN ({marks})"
        if for_edit:
            clauses.append(own + f" AND NOT EXISTS(SELECT 1 FROM album_images ai JOIN images i ON i.id=ai.image_id "
                           f"WHERE ai.album_id=al.id AND i.library_id NOT IN ({marks}))")
        else:
            clauses.append(f"({own} OR (al.library_id IS NULL AND EXISTS(SELECT 1 FROM album_images ai "
                           f"JOIN images i ON i.id=ai.image_id WHERE ai.album_id=al.id AND {scope})))")
    if kind:
        clauses.append("al.kind=:kind"); params['kind'] = kind
    if q:
        clauses.append("picshelf_name_key(al.name) LIKE :name ESCAPE '\\'")
        params['name'] = entity_names.contains_pattern(q)
    where = ' AND '.join(clauses) or '1=1'
    params.update(limit=-1 if limit is None else max(1,min(500,limit)), offset=max(0,offset))
    sql = f"""WITH page AS (
      SELECT al.* FROM albums al WHERE {where}
      ORDER BY al.library_id,al.kind DESC,al.name,al.id LIMIT :limit OFFSET :offset
    ) SELECT al.*,l.name AS library_name,
      (SELECT COUNT(*) FROM album_images ai JOIN images i ON i.id=ai.image_id WHERE ai.album_id=al.id AND {scope}) AS image_count,
      (SELECT i.id FROM album_images ai JOIN images i ON i.id=ai.image_id
       WHERE ai.album_id=al.id AND i.id=al.cover_image_id AND {scope}) AS visible_cover_id,
      (SELECT i.id FROM album_images ai JOIN images i ON i.id=ai.image_id
       WHERE ai.album_id=al.id AND {scope} ORDER BY ai.position,ai.image_id LIMIT 1) AS first_image_id
      FROM page al LEFT JOIN libraries l ON l.id=al.library_id
      ORDER BY al.library_id,al.kind DESC,al.name,al.id"""
    result = []
    for row in conn.execute(sql,params):
        album = dict(row)
        album['cover_image_id'] = album.pop('visible_cover_id')
        first = album.pop('first_image_id')
        album['preview_image_id'] = album['cover_image_id'] or first
        result.append(album)
    return result
