"""依赖：DB 连接、当前用户、权限检查、序列化辅助。"""
from __future__ import annotations

import sqlite3
from typing import Annotated, Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Request

from .. import auth, search
from ..access import AccessContext
from ..db import open_db
from ..i18n import t

COOKIE_NAME = "picshelf_session"


def db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = open_db(request.app.state.cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(db)]


def current_user(
    request: Request,
    conn: Db,
    authorization: Annotated[str | None, Header()] = None,
    picshelf_session: Annotated[str | None, Cookie()] = None,
) -> sqlite3.Row:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif picshelf_session:
        token = picshelf_session
    if not token:
        raise HTTPException(status_code=401, detail=t("未登录"))
    uid = auth.verify_token(request.app.state.secret, token)
    user = auth.get_user(conn, uid) if uid is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail=t("登录已过期，请重新登录"))
    return user


User = Annotated[sqlite3.Row, Depends(current_user)]


def require_cap(cap: str):
    def dep(user: User, conn: Db) -> sqlite3.Row:
        AccessContext(user["id"]).require_capability(conn, cap)
        return user

    return dep


def ensure_library_access(conn: sqlite3.Connection, user: sqlite3.Row,
                          library_id: int, minimum: str = "view") -> str:
    return AccessContext(user["id"]).require_library(conn, int(library_id), minimum)


def image_and_access(conn: sqlite3.Connection, user: sqlite3.Row,
                     image_id: int, minimum: str = "view") -> sqlite3.Row:
    return AccessContext(user["id"]).require_image(conn, image_id, minimum)


def ensure_catalog_access(conn: sqlite3.Connection, user: sqlite3.Row,
                          kind: str, entity_id: int) -> None:
    """保护全局目录中仅关联到不可见库的条目；未关联条目仍可编辑。"""
    table, links, column = {
        "artist": ("artists", "image_artists", "artist_id"),
        "tag": ("tags", "image_tags", "tag_id"),
    }[kind]
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)).fetchone():
        raise HTTPException(status_code=404, detail=t("条目不存在或无权访问"))
    allowed = auth.visible_library_ids(conn, user)
    if allowed is None:
        return
    if not conn.execute(f"SELECT 1 FROM {links} WHERE {column}=?", (entity_id,)).fetchone():
        return
    visible, params = search.visible_images_sql(allowed)
    if not conn.execute(
        f"WITH visible AS ({visible}) SELECT 1 FROM {links} "
        f"WHERE {column}=? AND image_id IN (SELECT id FROM visible)", [*params, entity_id],
    ).fetchone():
        raise HTTPException(status_code=404, detail=t("条目不存在或无权访问"))


def serialize_image(d: dict, *, base: str = "/api/v1/media") -> dict:
    iid = d.get("id")
    if iid is not None:
        original_version = f"?v={d['sha256'][:16]}" if d.get("sha256") else ""
        version = original_version + f"-d{d.get('derivative_version', 0)}" if original_version else ""
        d.setdefault("thumb_url", f"{base}/thumb/{iid}{version}")
        d.setdefault("preview_url", f"{base}/preview/{iid}{version}")
        d.setdefault("original_url", f"{base}/original/{iid}{original_version}")
    return d
