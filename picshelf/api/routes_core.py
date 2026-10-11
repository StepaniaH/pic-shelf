"""核心路由：库 / 扫描 / 专辑 / 图片 / 媒体 / 标签 / 艺术家 / 统计 / 查重 / 健康。"""
from __future__ import annotations

import mimetypes
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import __version__, auth, db as dbm, indexer, libsvc, ops, search
from .deps import Db, User, ensure_library_access, image_and_access, require_cap, serialize_image
from ..i18n import t

router = APIRouter(tags=["core"])


# ---------------------------------------------------------------- 健康与统计

@router.get("/health")
def health(conn: Db):
    return {"ok": True, "version": __version__, "schema": dbm.schema_version(conn)}


@router.get("/stats")
def stats(conn: Db, user: User):
    allowed = auth.visible_library_ids(conn, user)
    return libsvc.global_stats(conn, allowed)


# ---------------------------------------------------------------- 库

class LibraryBody(BaseModel):
    name: str
    root_path: str
    description: str | None = None


class LibraryPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("/libraries")
def list_libraries(conn: Db, user: User):
    return libsvc.list_libraries(conn, auth.visible_library_ids(conn, user))


@router.post("/libraries")
def create_library(body: LibraryBody, conn: Db, user: User):
    if not auth.has_cap(conn, user, "manage_library"):
        raise HTTPException(status_code=403, detail=t("缺少权限：manage_library"))
    root = Path(body.root_path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=t("目录不存在：{root}", root=root))
    try:
        lib_id = libsvc.create_library(conn, body.name, str(root), body.description)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=t("库名已存在：{v0}", v0=body.name))
    return libsvc.get_library(conn, lib_id)


@router.get("/libraries/{library_id}")
def get_library(library_id: int, conn: Db, user: User):
    ensure_library_access(conn, user, library_id)
    lib = libsvc.get_library(conn, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail=t("库不存在"))
    last_scan = conn.execute(
        "SELECT * FROM scans WHERE library_id=? ORDER BY id DESC LIMIT 1", (library_id,)
    ).fetchone()
    lib["last_scan"] = dict(last_scan) if last_scan else None
    lib["access"] = auth.library_access(conn, user, library_id)
    return lib


@router.patch("/libraries/{library_id}")
def patch_library(library_id: int, body: LibraryPatch, conn: Db, user: User):
    ensure_library_access(conn, user, library_id, minimum="manage")
    sets, params = [], []
    if body.name:
        sets.append("name=?")
        params.append(body.name)
    if body.description is not None:
        sets.append("description=?")
        params.append(body.description)
    if sets:
        params.append(library_id)
        conn.execute(f"UPDATE libraries SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return libsvc.get_library(conn, library_id)


@router.post("/libraries/{library_id}/scan")
def scan_library(library_id: int, request: Request, conn: Db, user: User):
    ensure_library_access(conn, user, library_id, minimum="manage")
    cfg = request.app.state.cfg
    try:
        return indexer.scan_library(conn, cfg, library_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/scans")
def list_scans(conn: Db, user: User, library_id: int | None = None, limit: int = 20):
    allowed = auth.visible_library_ids(conn, user)
    sql = "SELECT * FROM scans WHERE 1=1"
    params: list = []
    if library_id is not None:
        ensure_library_access(conn, user, library_id)
        sql += " AND library_id=?"
        params.append(library_id)
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" AND library_id IN ({marks})"
        params += allowed
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------- 专辑

class AlbumBody(BaseModel):
    name: str
    library_id: int
    category: str | None = None
    description: str | None = None


class AlbumPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    cover_image_id: int | None = None


class IdList(BaseModel):
    image_ids: list[int]


@router.get("/albums")
def list_albums(conn: Db, user: User, library_id: int | None = None, kind: str | None = None):
    allowed = auth.visible_library_ids(conn, user)
    if library_id is not None:
        ensure_library_access(conn, user, library_id)
    return ops.list_albums(conn, library_id=library_id, allowed_library_ids=allowed, kind=kind)


@router.post("/albums")
def create_album(body: AlbumBody, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    ensure_library_access(conn, user, body.library_id, minimum="edit")
    return ops.create_album(
        conn, body.name, library_id=body.library_id,
        category=body.category, description=body.description,
    )


@router.get("/albums/{album_id}")
def get_album(album_id: int, conn: Db, user: User):
    album = ops.get_album(conn, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail=t("专辑不存在"))
    if album["library_id"] is not None:
        ensure_library_access(conn, user, album["library_id"])
    return album


@router.patch("/albums/{album_id}")
def patch_album(album_id: int, body: AlbumPatch, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    album = ops.get_album(conn, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail=t("专辑不存在"))
    if album["library_id"] is not None:
        ensure_library_access(conn, user, album["library_id"], minimum="edit")
    return ops.update_album(conn, album_id, name=body.name, category=body.category,
                            description=body.description, cover_image_id=body.cover_image_id)


@router.get("/albums/{album_id}/images")
def album_images(album_id: int, conn: Db, user: User,
                 sort: str = "path", limit: int = 60, offset: int = 0):
    album = ops.get_album(conn, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail=t("专辑不存在"))
    if album["library_id"] is not None:
        ensure_library_access(conn, user, album["library_id"])
    allowed = auth.visible_library_ids(conn, user)
    res = search.search_images(
        conn, user["id"], allowed, sort=sort, limit=limit, offset=offset,
        extra_where=["EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id AND ai.album_id=:alb)"],
        extra_params={"alb": album_id},
    )
    res["album"] = album
    res["items"] = [serialize_image(i) for i in res["items"]]
    return res


@router.post("/albums/{album_id}/images")
def album_add(album_id: int, body: IdList, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    album = ops.get_album(conn, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail=t("专辑不存在"))
    if album["library_id"] is not None:
        ensure_library_access(conn, user, album["library_id"], minimum="edit")
    for iid in body.image_ids:
        image_and_access(conn, user, iid)
    try:
        n = ops.album_add_images(conn, album_id, body.image_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"added": n}


@router.delete("/albums/{album_id}/images/{image_id}")
def album_remove(album_id: int, image_id: int, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    album = ops.get_album(conn, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail=t("专辑不存在"))
    if album["library_id"] is not None:
        ensure_library_access(conn, user, album["library_id"], minimum="edit")
    try:
        n = ops.album_remove_images(conn, album_id, [image_id])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"removed": n}


# ---------------------------------------------------------------- 图片

@router.get("/images")
def list_images(
    conn: Db, user: User,
    q: str = "", sort: str = "newest", limit: int = 60, offset: int = 0,
    library_id: int | None = None, album_id: int | None = None,
    artist_id: int | None = None, tag: str | None = None,
    favorites: bool = False, rating_min: int | None = None,
):
    allowed = auth.visible_library_ids(conn, user)
    if library_id is not None:
        ensure_library_access(conn, user, library_id)
    extra_where: list[str] = []
    extra_params: dict = {}
    if library_id is not None:
        extra_where.append("i.library_id = :f_lib")
        extra_params["f_lib"] = library_id
    if album_id is not None:
        extra_where.append(
            "EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id AND ai.album_id=:f_alb)"
        )
        extra_params["f_alb"] = album_id
    if artist_id is not None:
        extra_where.append(
            "EXISTS(SELECT 1 FROM image_artists ia WHERE ia.image_id=i.id AND ia.artist_id=:f_art)"
        )
        extra_params["f_art"] = artist_id
    if tag:
        extra_where.append(
            "EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
            "WHERE it.image_id=i.id AND lower(tg.name)=lower(:f_tag))"
        )
        extra_params["f_tag"] = tag
    if favorites:
        extra_where.append(
            "EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)"
        )
    if rating_min is not None:
        extra_where.append(
            "COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id "
            "AND r.user_id=:uid),0) >= :f_rmin"
        )
        extra_params["f_rmin"] = rating_min
    res = search.search_images(
        conn, user["id"], allowed, query=q, sort=sort, limit=limit, offset=offset,
        extra_where=extra_where or None, extra_params=extra_params or None,
    )
    res["items"] = [serialize_image(i) for i in res["items"]]
    return res


@router.get("/images/{image_id}")
def image_detail(image_id: int, conn: Db, user: User):
    image_and_access(conn, user, image_id)
    detail = search.get_image(conn, image_id, user["id"], auth.visible_library_ids(conn, user))
    if detail is None:
        raise HTTPException(status_code=404, detail=t("图片不存在"))
    detail["comments"] = ops.list_comments(conn, image_id)
    detail["notes"] = ops.list_notes(conn, image_id)
    return serialize_image(detail)


# ---------------------------------------------------------------- 媒体文件

def _serve_file(cfg, path: Path, media_type: str, etag: str, download_name: str | None = None):
    if not path.is_file():
        raise HTTPException(status_code=404, detail=t("文件不存在"))
    headers = {"Cache-Control": "private, max-age=3600", "ETag": f'"{etag}"'}
    kwargs = {}
    if download_name:
        kwargs["filename"] = download_name
        headers["Cache-Control"] = "private, max-age=86400"
    return FileResponse(str(path), media_type=media_type, headers=headers, **kwargs)


@router.get("/media/{kind}/{image_id}")
def media(kind: str, image_id: int, request: Request, conn: Db, user: User, download: int = 0):
    if kind not in ("thumb", "preview", "original"):
        raise HTTPException(status_code=404, detail=t("未知媒体类型"))
    row = image_and_access(conn, user, image_id)
    cfg = request.app.state.cfg
    if kind in ("thumb", "preview"):
        rel = row["thumb_rel"] if kind == "thumb" else row["preview_rel"]
        if rel:
            path = cfg.media_dir / rel
            return _serve_file(cfg, path, "image/webp", row["sha256"] or str(image_id))
        kind = "original"  # 缩略图缺失时回退原图
    lib = conn.execute("SELECT root_path FROM libraries WHERE id=?", (row["library_id"],)).fetchone()
    path = Path(lib["root_path"]) / row["rel_path"]
    media_type = mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"
    return _serve_file(cfg, path, media_type, row["sha256"] or str(image_id),
                       download_name=row["filename"] if download else None)


# ---------------------------------------------------------------- 标签

class TagBody(BaseModel):
    name: str
    category: str | None = None


class TagPatch(BaseModel):
    name: str


@router.get("/tags")
def list_tags(conn: Db, user: User, q: str | None = None, limit: int = 50):
    return ops.list_tags(conn, q=q, limit=limit)


@router.post("/tags")
def create_tag(body: TagBody, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    tag_id = ops.ensure_tag(conn, body.name, body.category)
    conn.commit()
    return {"id": tag_id, "name": body.name.strip()}


@router.patch("/tags/{tag_id}")
def rename_tag(tag_id: int, body: TagPatch, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    try:
        return ops.rename_tag(conn, tag_id, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------- 艺术家

class ArtistBody(BaseModel):
    name: str
    aliases: str | None = None
    links: list[str] | None = None
    note: str | None = None


class ArtistPatch(BaseModel):
    name: str | None = None
    aliases: str | None = None
    links: list[str] | None = None
    note: str | None = None


@router.get("/artists")
def list_artists(conn: Db, user: User, q: str | None = None, limit: int = 100):
    return ops.list_artists(conn, q=q, limit=limit)


@router.post("/artists")
def create_artist(body: ArtistBody, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    try:
        return ops.create_artist(conn, body.name, aliases=body.aliases,
                                 links=body.links, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/artists/{artist_id}")
def get_artist(artist_id: int, conn: Db, user: User):
    try:
        return ops.get_artist(conn, artist_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/artists/{artist_id}")
def patch_artist(artist_id: int, body: ArtistPatch, conn: Db, user: User):
    if not auth.has_cap(conn, user, "tag_edit"):
        raise HTTPException(status_code=403, detail=t("缺少权限：tag_edit"))
    return ops.update_artist(conn, artist_id, name=body.name, aliases=body.aliases,
                             links=body.links, note=body.note)


@router.get("/artists/{artist_id}/images")
def artist_images(artist_id: int, conn: Db, user: User,
                  sort: str = "newest", limit: int = 60, offset: int = 0):
    try:
        ops.get_artist(conn, artist_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    allowed = auth.visible_library_ids(conn, user)
    res = search.search_images(
        conn, user["id"], allowed, sort=sort, limit=limit, offset=offset,
        extra_where=["EXISTS(SELECT 1 FROM image_artists ia WHERE ia.image_id=i.id AND ia.artist_id=:art)"],
        extra_params={"art": artist_id},
    )
    res["items"] = [serialize_image(i) for i in res["items"]]
    return res


# ---------------------------------------------------------------- 查重

@router.get("/dupes")
def dupes(conn: Db, user: User, mode: str = "exact", threshold: int = 8):
    allowed = auth.visible_library_ids(conn, user)
    if mode == "similar":
        return libsvc.similar_dupes(conn, allowed, threshold=threshold)
    return libsvc.exact_dupes(conn, allowed)
