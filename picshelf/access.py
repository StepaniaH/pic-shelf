"""Explicit service identity; permissions are resolved at each operation."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import auth
from .errors import ServiceError
from .i18n import t

RANK = {"view": 1, "edit": 2, "manage": 3}


@dataclass(frozen=True)
class AccessContext:
    user_id: int
    local_admin: bool = False

    def user(self, conn: sqlite3.Connection) -> sqlite3.Row:
        user = auth.get_user(conn, self.user_id)
        if user is None:
            raise ServiceError("authentication_required", t("登录已过期，请重新登录"),
                               status=401, next_step=t("请使用有效账户重新登录"))
        return user

    def allowed_libraries(self, conn: sqlite3.Connection) -> list[int] | None:
        user = self.user(conn)
        return None if self.local_admin else auth.visible_library_ids(conn, user)

    def require_capability(self, conn: sqlite3.Connection, capability: str) -> None:
        user = self.user(conn)
        if not self.local_admin and not auth.has_cap(conn, user, capability):
            raise ServiceError("permission_denied", t("缺少权限：{cap}", cap=capability),
                               status=403, next_step=t("请联系管理员检查账户和库授权"))

    def require_library(self, conn: sqlite3.Connection, library_id: int,
                        minimum: str = "view") -> str:
        user = self.user(conn)
        access = "manage" if self.local_admin else auth.library_access(conn, user, library_id)
        if access is None or RANK.get(access, 0) < RANK[minimum]:
            raise ServiceError("permission_denied", t("无权访问该库"), status=403,
                               next_step=t("请联系管理员检查账户和库授权"))
        return access

    def require_image(self, conn: sqlite3.Connection, image_id: int,
                      minimum: str = "view", capability: str | None = None, *, include_missing=False) -> sqlite3.Row:
        self.user(conn)
        if capability:
            self.require_capability(conn, capability)
        row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None or (row["missing"] and not self.local_admin and not include_missing):
            raise ServiceError("image_not_found", t("图片不存在"), status=404,
                               next_step=t("请刷新图片列表，或扫描库后重试"),
                               objects={"image_id": image_id})
        self.require_library(conn, row["library_id"], minimum)
        return row
