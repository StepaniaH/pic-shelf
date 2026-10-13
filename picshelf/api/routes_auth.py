"""认证路由：登录 / 登出 / 当前用户 / 改密。"""
from __future__ import annotations
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..errors import ServiceError
from .deps import COOKIE_NAME, Db, User
from ..i18n import t


def public_user(conn, user) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "is_admin": bool(user["is_admin"]),
        "caps": sorted(auth.caps_for_user(conn, user)),
    }


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(max_length=1024)


class PasswordBody(BaseModel):
    old_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=6, max_length=1024)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginBody, response: Response, request: Request, conn: Db):
    buckets = auth.login_buckets(request.app.state.secret, body.username,
                                 request.client.host if request.client else "unknown")
    auth.check_login_limit(conn, buckets)
    if not request.app.state.login_slots.acquire(blocking=False):
        raise ServiceError("login_busy", t("登录服务繁忙，请稍后重试"), status=429, retryable=True,
                           next_step=t("请等待一分钟后重试，或联系管理员检查账户"))
    try:
        user = auth.get_user_by_username(conn, body.username)
        valid = auth.verify_password(body.password, user["password_hash"] if user else auth._dummy_hash())
        if not valid or user is None or user["disabled"]:
            auth.record_login_failure(conn, buckets)
            raise HTTPException(status_code=401, detail=t("用户名或密码错误"))
        conn.execute("DELETE FROM login_attempts WHERE bucket=?", (buckets[0],))
        conn.commit()
        token = auth.sign_token(request.app.state.secret, int(user["id"]), version=user["token_version"])
    finally:
        request.app.state.login_slots.release()
    secure = request.url.scheme == "https" or urlsplit(request.app.state.cfg.public_origin or "").scheme == "https"
    response.set_cookie(
        COOKIE_NAME, token, max_age=auth.TOKEN_TTL_DEFAULT,
        httponly=True, samesite="lax", path="/", secure=secure,
    )
    return {"token": token, "user": public_user(conn, user)}


@router.post("/logout")
def logout(response: Response, request: Request, conn: Db):
    authorization = request.headers.get("authorization", "")
    token = (authorization.split(" ", 1)[1].strip() if authorization.lower().startswith("bearer ")
             else request.cookies.get(COOKIE_NAME, ""))
    auth.revoke_token(conn, request.app.state.secret, token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/revoke-sessions")
def revoke_sessions(response: Response, conn: Db, user: User):
    auth.revoke_sessions(conn, user["id"])
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(conn: Db, user: User):
    return public_user(conn, user)


@router.post("/password")
def change_password(body: PasswordBody, conn: Db, user: User):
    if not auth.verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail=t("原密码不正确"))
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail=t("新密码至少 6 位"))
    auth.set_password(conn, user["id"], body.new_password)
    return {"ok": True}
