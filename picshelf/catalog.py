"""Catalog operations shared by transport adapters and local tools."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import ops, search
from .access import AccessContext
from .errors import ServiceError
from .i18n import t
from .inputs import MAX_IMAGE_LIMIT, integer_in_range
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

    def __post_init__(self):
        # Adapters parse their transport first; the domain never coerces types.
        invalid = []
        for field in ('library_id', 'album_id', 'artist_id'):
            value = getattr(self, field)
            if value is not None and not integer_in_range(value):
                invalid.append(field)
        for field, valid in (
            ('query', isinstance(self.query, str)),
            ('sort', isinstance(self.sort, str) and self.sort in search.SORTS),
            ('limit', integer_in_range(self.limit, 0, MAX_IMAGE_LIMIT)),
            ('offset', integer_in_range(self.offset, 0)),
            ('favorites', type(self.favorites) is bool),
            ('rating_min', self.rating_min is None or integer_in_range(self.rating_min, 0, 10)),
            ('tag', self.tag is None or isinstance(self.tag, str) and len(self.tag) <= 300),
            ('workflow', self.workflow is None or isinstance(self.workflow, str) and self.workflow in ('inbox', 'done')),
        ):
            if not valid:
                invalid.append(field)
        if invalid:
            raise ServiceError('query_invalid', t('查询参数无效：{field}；请检查类型与范围', field=invalid[0]),
                               next_step=t('请检查搜索条件后重试'), objects={'field': invalid[0]})


def image_filter_clauses(spec: ImageQuery) -> tuple[list[str], dict]:
    """Compile source filters consistently for browsing and playback."""
    clauses, params = [], {}
    if spec.workflow is not None:
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
        clauses.append(search.tag_match_sql(":f_tag"))
        params["f_tag"] = match_key(spec.tag)
    if spec.favorites:
        clauses.append("EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)")
    if spec.rating_min is not None:
        clauses.append("COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id AND r.user_id=:uid),0)>=:f_rmin")
        params["f_rmin"] = spec.rating_min
    return clauses, params


def query_images(conn: sqlite3.Connection, access: AccessContext, spec: ImageQuery, *, ids_only=False) -> dict:
    allowed = access.allowed_libraries(conn)
    if spec.album_id is not None:
        access.require_album(conn, spec.album_id)
    clauses, params = image_filter_clauses(spec)
    result = search.search_images(conn, access.user_id, allowed, query=spec.query, sort=spec.sort,
                                  limit=spec.limit, offset=spec.offset,
                                  extra_where=clauses or None, extra_params=params or None, ids_only=ids_only)
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
