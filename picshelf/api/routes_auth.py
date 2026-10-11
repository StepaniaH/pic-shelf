"""认证路由：登录 / 登出 / 当前用户 / 改密。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth
from .deps import COOKIE_NAME, Db, User


def public_user(conn, user) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "is_admin": bool(user["is_admin"]),
        "caps": sorted(auth.caps_for_user(conn, user)),
    }


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginBody, response: Response, request: Request, conn: Db):
    user = auth.get_user_by_username(conn, body.username)
    if (
        user is None
        or user["disabled"]
        or not auth.verify_password(body.password, user["password_hash"])
    ):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = auth.sign_token(request.app.state.secret, int(user["id"]))
    response.set_cookie(
        COOKIE_NAME, token, max_age=auth.TOKEN_TTL_DEFAULT,
        httponly=True, samesite="lax", path="/",
    )
    return {"token": token, "user": public_user(conn, user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(conn: Db, user: User):
    return public_user(conn, user)


@router.post("/password")
def change_password(body: PasswordBody, conn: Db, user: User):
    if not auth.verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (auth.hash_password(body.new_password), user["id"]),
    )
    conn.commit()
    return {"ok": True}
