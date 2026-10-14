"""管理路由：用户 / 组 / 库授权。"""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import StrictBool, BaseModel, Field

from .. import auth, db, source_access
from ..access import AccessContext
from .types import Identifier, BodyIdentifier
from .deps import Db, User, ensure_library_access, require_cap
from ..i18n import t

router = APIRouter(tags=["admin"])

Admin = Annotated[sqlite3.Row, Depends(require_cap("admin"))]


class SourceRootBody(BaseModel):
    root_path: str = Field(min_length=1, max_length=4096)
    allow_move: StrictBool = False


@router.get("/import-sources")
def list_sources(conn: Db, admin: Admin):
    return source_access.list_roots(conn, AccessContext(admin["id"]))


@router.post("/import-sources")
def add_source(body: SourceRootBody, conn: Db, admin: Admin):
    return source_access.register(conn, AccessContext(admin["id"]), body.root_path, allow_move=body.allow_move)


@router.delete("/import-sources/{root_id}")
def revoke_source(root_id: Identifier, conn: Db, admin: Admin):
    return source_access.revoke(conn, AccessContext(admin["id"]), root_id)


def _user_dict(conn, row) -> dict:
    groups = [
        {"id": r["id"], "name": r["name"]}
        for r in conn.execute(
            "SELECT g.id, g.name FROM groups g JOIN user_groups ug ON ug.group_id=g.id "
            "WHERE ug.user_id=? ORDER BY g.name",
            (row["id"],),
        ).fetchall()
    ]
    return {
        "id": row["id"], "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]), "disabled": bool(row["disabled"]),
        "created_at": row["created_at"], "groups": groups,
    }


# ---------------------------------------------------------------- 用户

class UserBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=6, max_length=1024)
    is_admin: StrictBool = False
    display_name: str | None = None
    groups: list[str] | None = None


class UserPatch(BaseModel):
    is_admin: StrictBool | None = None
    disabled: StrictBool | None = None
    password: str | None = Field(default=None, min_length=6, max_length=1024)
    display_name: str | None = None


class GroupsBody(BaseModel):
    group_ids: list[BodyIdentifier]


@router.get("/users")
def list_users(conn: Db, admin: Admin):
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_user_dict(conn, r) for r in rows]


@router.post("/users")
def create_user(body: UserBody, conn: Db, admin: Admin):
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail=t("用户名不能为空"))
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail=t("密码至少 6 位"))
    if auth.get_user_by_username(conn, username) is not None:
        raise HTTPException(status_code=409, detail=t("用户名已存在：{username}", username=username))
    try:
        uid = auth.create_user(
            conn, username, body.password, is_admin=body.is_admin,
            display_name=body.display_name,
            group_names=tuple(body.groups) if body.groups is not None else None,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=t("用户名已存在：{username}", username=username))
    return _user_dict(conn, conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


@router.patch("/users/{user_id}")
def patch_user(user_id: Identifier, body: UserPatch, conn: Db, admin: Admin):
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("用户不存在"))
    if user_id == admin["id"]:
        if body.disabled:
            raise HTTPException(status_code=400, detail=t("不能禁用自己的账号"))
        if body.is_admin is False:
            raise HTTPException(status_code=400, detail=t("不能取消自己的管理员身份"))
    sets, params = [], []
    if body.is_admin is not None:
        sets.append("is_admin=?")
        params.append(1 if body.is_admin else 0)
    if body.disabled is not None:
        sets.append("disabled=?")
        params.append(1 if body.disabled else 0)
    if body.display_name is not None:
        sets.append("display_name=?")
        params.append(body.display_name)
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail=t("密码至少 6 位"))
        sets.append("password_hash=?")
        params.append(auth.hash_password(body.password))
    if sets:
        if body.password is not None or body.disabled is not None or body.is_admin is not None:
            sets.append("token_version=token_version+1")
        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return _user_dict(conn, conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


@router.put("/users/{user_id}/groups")
def set_user_groups(user_id: Identifier, body: GroupsBody, conn: Db, admin: Admin):
    row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("用户不存在"))
    conn.execute("DELETE FROM user_groups WHERE user_id=?", (user_id,))
    for gid in body.group_ids:
        conn.execute(
            "INSERT OR IGNORE INTO user_groups(user_id, group_id) VALUES(?,?)",
            (user_id, gid),
        )
    conn.commit()
    return _user_dict(conn, conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


# ---------------------------------------------------------------- 组

def _membership(conn, group_id, user_id, *, remove=False):
    with db.transaction(conn):
        if not conn.execute('SELECT 1 FROM groups WHERE id=?', (group_id,)).fetchone():
            raise HTTPException(status_code=404, detail=t("组不存在"))
        if not conn.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
            raise HTTPException(status_code=404, detail=t("用户不存在"))
        if remove:
            conn.execute('DELETE FROM user_groups WHERE user_id=? AND group_id=?', (user_id, group_id))
        else:
            conn.execute('INSERT OR IGNORE INTO user_groups(user_id,group_id) VALUES(?,?)', (user_id, group_id))
    return {"ok": True}


@router.put('/groups/{group_id}/members/{user_id}')
def add_group_member(group_id: Identifier, user_id: Identifier, conn: Db, admin: Admin):
    return _membership(conn, group_id, user_id)


@router.delete('/groups/{group_id}/members/{user_id}')
def remove_group_member(group_id: Identifier, user_id: Identifier, conn: Db, admin: Admin):
    return _membership(conn, group_id, user_id, remove=True)

class GroupBody(BaseModel):
    name: str
    description: str | None = None
    capabilities: list[str] | None = None


class GroupPatch(BaseModel):
    description: str | None = None
    capabilities: list[str] | None = None


def _group_dict(conn, row) -> dict:
    import json
    try:
        caps = json.loads(row["capabilities"] or "[]")
    except Exception:
        caps = []
    members = [
        {"id": r["id"], "username": r["username"]}
        for r in conn.execute(
            "SELECT u.id, u.username FROM users u JOIN user_groups ug ON ug.user_id=u.id "
            "WHERE ug.group_id=? ORDER BY u.username",
            (row["id"],),
        ).fetchall()
    ]
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "capabilities": caps, "is_system": bool(row["is_system"]),
        "members": members,
    }


@router.get("/groups")
def list_groups(conn: Db, admin: Admin):
    rows = conn.execute("SELECT * FROM groups ORDER BY is_system DESC, name").fetchall()
    return [_group_dict(conn, r) for r in rows]


@router.post("/groups")
def create_group(body: GroupBody, conn: Db, admin: Admin):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail=t("组名不能为空"))
    caps = [c for c in (body.capabilities or ["view"]) if c in auth.ALL_CAPS]
    import json
    from ..db import utcnow
    try:
        cur = conn.execute(
            "INSERT INTO groups(name, description, capabilities, is_system, created_at) "
            "VALUES(?,?,?,0,?)",
            (name, body.description, json.dumps(caps), utcnow()),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=t("组名已存在：{name}", name=name))
    conn.commit()
    return _group_dict(conn, conn.execute("SELECT * FROM groups WHERE id=?", (cur.lastrowid,)).fetchone())


@router.patch("/groups/{group_id}")
def patch_group(group_id: Identifier, body: GroupPatch, conn: Db, admin: Admin):
    row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("组不存在"))
    sets, params = [], []
    if body.description is not None:
        sets.append("description=?")
        params.append(body.description)
    if body.capabilities is not None:
        if row["is_system"]:
            raise HTTPException(status_code=400, detail=t("系统组（admins/members）的能力不可修改"))
        import json
        caps = [c for c in body.capabilities if c in auth.ALL_CAPS]
        sets.append("capabilities=?")
        params.append(json.dumps(caps))
    if sets:
        params.append(group_id)
        conn.execute(f"UPDATE groups SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return _group_dict(conn, conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone())


@router.delete("/groups/{group_id}")
def delete_group(group_id: Identifier, conn: Db, admin: Admin):
    row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=t("组不存在"))
    if row["is_system"]:
        raise HTTPException(status_code=400, detail=t("系统组不可删除"))
    conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 库授权

class GrantBody(BaseModel):
    group_id: BodyIdentifier
    access: str | None = None  # None = 移除授权


def _ensure_can_manage_library(conn, user, library_id: Identifier) -> None:
    if user["is_admin"]:
        return
    if not auth.has_cap(conn, user, "manage_library"):
        raise HTTPException(status_code=403, detail=t("缺少权限：manage_library"))
    ensure_library_access(conn, user, library_id, minimum="manage")


@router.get("/libraries/{library_id}/grants")
def list_grants(library_id: Identifier, conn: Db, user: User):
    _ensure_can_manage_library(conn, user, library_id)
    rows = conn.execute(
        "SELECT lg.group_id, lg.access, g.name AS group_name FROM library_grants lg "
        "JOIN groups g ON g.id=lg.group_id WHERE lg.library_id=? ORDER BY g.name",
        (library_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.put("/libraries/{library_id}/grants")
def set_grant(library_id: Identifier, body: GrantBody, conn: Db, user: User):
    _ensure_can_manage_library(conn, user, library_id)
    if body.access is None:
        conn.execute(
            "DELETE FROM library_grants WHERE group_id=? AND library_id=?",
            (body.group_id, library_id),
        )
    else:
        if body.access not in ("view", "edit", "manage"):
            raise HTTPException(status_code=400, detail=t("access 必须是 view / edit / manage"))
        conn.execute(
            "INSERT INTO library_grants(group_id, library_id, access) VALUES(?,?,?) "
            "ON CONFLICT(group_id, library_id) DO UPDATE SET access=excluded.access",
            (body.group_id, library_id, body.access),
        )
    conn.commit()
    return {"ok": True}
