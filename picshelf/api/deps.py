"""依赖：DB 连接、当前用户、权限检查、序列化辅助。"""
from __future__ import annotations

import sqlite3
from typing import Annotated, Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Request

from .. import auth
from ..db import open_db
from ..i18n import t

COOKIE_NAME = "picshelf_session"
RANK = {"view": 1, "edit": 2, "manage": 3}


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
        if not auth.has_cap(conn, user, cap):
            raise HTTPException(status_code=403, detail=t("缺少权限：{cap}", cap=cap))
        return user

    return dep


def ensure_library_access(conn: sqlite3.Connection, user: sqlite3.Row,
                          library_id: int, minimum: str = "view") -> str:
    access = auth.library_access(conn, user, int(library_id))
    if access is None or RANK.get(access, 0) < RANK[minimum]:
        raise HTTPException(status_code=403, detail=t("无权访问该库"))
    return access


def image_and_access(conn: sqlite3.Connection, user: sqlite3.Row,
                     image_id: int, minimum: str = "view") -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM images WHERE id=? AND missing=0", (image_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("图片不存在"))
    ensure_library_access(conn, user, int(row["library_id"]), minimum)
    return row


def serialize_image(d: dict, *, base: str = "/api/v1/media") -> dict:
    iid = d.get("id")
    if iid is not None:
        d.setdefault("thumb_url", f"{base}/thumb/{iid}")
        d.setdefault("preview_url", f"{base}/preview/{iid}")
        d.setdefault("original_url", f"{base}/original/{iid}")
    return d
