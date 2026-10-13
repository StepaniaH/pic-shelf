"""交互路由：评分 / 收藏 / 图片标签 / 艺术家关联 / 评论 / 笔记。"""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, catalog, names, ops
from ..i18n import t
from ..access import AccessContext
from .deps import Db, User, ensure_catalog_access, image_and_access, require_cap

router = APIRouter(tags=["interact"])

TagEditor = Annotated[sqlite3.Row, Depends(require_cap("tag_edit"))]
Rater = Annotated[sqlite3.Row, Depends(require_cap("rate"))]
Commenter = Annotated[sqlite3.Row, Depends(require_cap("comment"))]
Noter = Annotated[sqlite3.Row, Depends(require_cap("note"))]


# ---------------------------------------------------------------- 评分 / 收藏

class RatingBody(BaseModel):
    value: int | None = None


@router.post("/images/{image_id}/rating")
def set_rating(image_id: int, body: RatingBody, conn: Db, user: Rater):
    return catalog.rate_image(conn, AccessContext(user["id"]), image_id, body.value)


class FavoriteBody(BaseModel):
    on: bool | None = None


@router.post("/images/{image_id}/favorite")
def set_favorite(image_id: int, body: FavoriteBody, conn: Db, user: Rater):
    image_and_access(conn, user, image_id)
    return {"favorite": ops.set_favorite(conn, image_id, user["id"], body.on)}


# ---------------------------------------------------------------- 图片标签

class TagsBody(BaseModel):
    names: list[str] = Field(max_length=1000)


@router.post("/images/{image_id}/tags")
def add_tags(image_id: int, body: TagsBody, conn: Db, user: TagEditor):
    image_and_access(conn, user, image_id, "edit")
    for name in body.names:
        existing = names.resolve(conn, "tag", name)
        if existing:
            ensure_catalog_access(conn, user, "tag", existing)
    return {"added": ops.add_tags(conn, image_id, body.names, user["id"])}


@router.delete("/images/{image_id}/tags/{tag}")
def remove_tag(image_id: int, tag: str, conn: Db, user: TagEditor):
    image_and_access(conn, user, image_id, "edit")
    return {"removed": ops.remove_tags(conn, image_id, [tag])}


# ---------------------------------------------------------------- 艺术家关联

class LinkArtistBody(BaseModel):
    artist_id: int
    on: bool = True


@router.post("/images/{image_id}/artists")
def link_artist(image_id: int, body: LinkArtistBody, conn: Db, user: TagEditor):
    image_and_access(conn, user, image_id, "edit")
    ensure_catalog_access(conn, user, "artist", body.artist_id)
    try:
        ops.link_artist(conn, image_id, body.artist_id, body.on)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    detail_artists = [
        dict(r) for r in conn.execute(
            "SELECT a.id, a.name FROM artists a JOIN image_artists ia ON ia.artist_id=a.id "
            "WHERE ia.image_id=?", (image_id,)
        ).fetchall()
    ]
    return {"artists": detail_artists}


# ---------------------------------------------------------------- 评论

class CommentBody(BaseModel):
    body: str = Field(max_length=10000)


@router.get("/images/{image_id}/comments")
def list_comments(image_id: int, conn: Db, user: User):
    image_and_access(conn, user, image_id)
    return ops.list_comments(conn, image_id)


@router.post("/images/{image_id}/comments")
def add_comment(image_id: int, body: CommentBody, conn: Db, user: Commenter):
    image_and_access(conn, user, image_id)
    try:
        return ops.add_comment(conn, image_id, user["id"], body.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, conn: Db, user: Commenter):
    row = conn.execute("SELECT image_id FROM comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("条目不存在或无权访问"))
    image_and_access(conn, user, row["image_id"])
    try:
        ops.delete_comment(conn, comment_id, user["id"], is_admin=bool(user["is_admin"]))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"ok": True}


# ---------------------------------------------------------------- 笔记

class NoteBody(BaseModel):
    body: str = Field(max_length=10000)
    geometry: str | None = Field(default=None, max_length=4096)


@router.get("/images/{image_id}/notes")
def list_notes(image_id: int, conn: Db, user: User):
    image_and_access(conn, user, image_id)
    return ops.list_notes(conn, image_id)


@router.post("/images/{image_id}/notes")
def add_note(image_id: int, body: NoteBody, conn: Db, user: Noter):
    image_and_access(conn, user, image_id)
    try:
        return ops.add_note(conn, image_id, user["id"], body.body, body.geometry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, conn: Db, user: Noter):
    row = conn.execute("SELECT image_id FROM notes WHERE id=?", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("条目不存在或无权访问"))
    image_and_access(conn, user, row["image_id"])
    try:
        ops.delete_note(conn, note_id, user["id"], is_admin=bool(user["is_admin"]))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"ok": True}
