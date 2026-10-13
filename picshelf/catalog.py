"""Catalog operations shared by transport adapters and local tools."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import ops, search
from .access import AccessContext
from .errors import ServiceError
from .i18n import t
from .names import match_key


@dataclass(frozen=True)
class ImageQuery:
    query: str = ""
    sort: str = "newest"
    limit: int = 50
    offset: int = 0
    library_id: int | None = None
    album_id: int | None = None
    artist_id: int | None = None
    tag: str | None = None
    favorites: bool = False
    rating_min: int | None = None
    workflow: str | None = None


def query_images(conn: sqlite3.Connection, access: AccessContext, spec: ImageQuery) -> dict:
    allowed = access.allowed_libraries(conn)
    if spec.album_id is not None:
        access.require_album(conn, spec.album_id)
    clauses, params = [], {}
    if spec.workflow is not None:
        if spec.workflow not in ("inbox","done"):
            raise ServiceError("query_invalid",t("整理状态必须为 inbox 或 done"),
                               next_step=t("请检查搜索条件后重试"))
        clauses.append("COALESCE((SELECT state FROM image_workflow w WHERE w.image_id=i.id AND w.user_id=:uid),'inbox')=:f_workflow")
        params["f_workflow"] = spec.workflow
    if spec.library_id is not None:
        clauses.append("i.library_id=:f_lib")
        params["f_lib"] = spec.library_id
    for field, table, column in (("album_id", "album_images", "album_id"),
                                 ("artist_id", "image_artists", "artist_id")):
        value = getattr(spec, field)
        if value is not None:
            clauses.append(f"EXISTS(SELECT 1 FROM {table} link WHERE link.image_id=i.id AND link.{column}=:{field})")
            params[field] = value
    if spec.tag:
        clauses.append("EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
                       "WHERE it.image_id=i.id AND (picshelf_name_key(tg.name)=:f_tag OR EXISTS("
                       "SELECT 1 FROM tag_aliases ta WHERE ta.entity_id=tg.id AND ta.match_key=:f_tag)))")
        params["f_tag"] = match_key(spec.tag)
    if spec.favorites:
        clauses.append("EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)")
    if spec.rating_min is not None:
        clauses.append("COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id AND r.user_id=:uid),0)>=:f_rmin")
        params["f_rmin"] = spec.rating_min
    result = search.search_images(conn, access.user_id, allowed, query=spec.query, sort=spec.sort,
                                  limit=spec.limit, offset=spec.offset,
                                  extra_where=clauses or None, extra_params=params or None)
    result["error_codes"] = ["invalid_query" for _ in result["errors"]]
    return result


def rate_image(conn: sqlite3.Connection, access: AccessContext,
               image_id: int, value: int | None) -> dict:
    access.require_image(conn, image_id, capability="rate")
    if value is not None and (type(value) is not int or not 0 <= value <= 10):
        raise ServiceError("invalid_rating", t("评分范围 0–10"),
                           next_step=t("请输入 0–10 的整数，或清除评分"),
                           objects={"image_id": image_id})
    return ops.set_rating(conn, image_id, access.user_id, value)
