"""播放路由：会话创建与导航（队列由服务端生成，含 ACL）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import auth, ops, play, search
from .deps import Db, User, ensure_library_access, serialize_image
from ..i18n import t

router = APIRouter(tags=["play"])


class PlaySource(BaseModel):
    library_id: int | None = None
    album_id: int | None = None
    artist_id: int | None = None
    tag: str | None = None
    favorites: bool = False
    rating_min: int | None = None


class PlayBody(BaseModel):
    source: PlaySource = PlaySource()
    query: str = ""
    order: str = "sequential"
    interval: int = 0
    limit: int = 0
    loop: bool = False


class StepBody(BaseModel):
    step: int = 1


class GotoBody(BaseModel):
    position: int = 0


def _validate_source(conn, user, src: PlaySource, order: str) -> None:
    if order not in ("sequential", "shuffle", "random"):
        raise HTTPException(status_code=400, detail=t("order 必须是 sequential / shuffle / random"))
    if src.library_id is not None:
        ensure_library_access(conn, user, src.library_id)
    if src.album_id is not None:
        album = ops.get_album(conn, src.album_id)
        if album is None:
            raise HTTPException(status_code=404, detail=t("专辑不存在"))
        if album["library_id"] is not None:
            ensure_library_access(conn, user, album["library_id"])
    if src.artist_id is not None:
        try:
            ops.get_artist(conn, src.artist_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


def _session_or_404(conn, token: str, user) -> "play.sqlite3.Row":
    session = play.get_session_by_token(conn, token)
    if session is None or (int(session["user_id"]) != int(user["id"]) and not user["is_admin"]):
        raise HTTPException(status_code=404, detail=t("播放会话不存在"))
    return session


def _state(conn, session, user) -> dict:
    allowed = auth.visible_library_ids(conn, user)
    base = play.current(conn, session, int(user["id"]), allowed)
    out = {
        "token": session["token"],
        "total": base["total"],
        "cursor": base["cursor"],
        "position": base["cursor"] + 1 if base["cursor"] >= 0 else 0,
        "loop": bool(session["loop"]),
        "interval": int(session["interval"]),
        "order": session["order_mode"],
        "image": None,
    }
    if base["cursor"] >= 0:
        iid = play._cursor_item(conn, int(session["id"]), int(session["cursor"]))
        if iid is not None:
            detail = search.get_image(conn, iid, int(user["id"]), allowed)
            if detail:
                out["image"] = serialize_image(detail)
    return out


def _attach_image(conn, result: dict, user) -> dict:
    allowed = auth.visible_library_ids(conn, user)
    iid = result.get("image_id")
    result["image"] = None
    if iid is not None:
        detail = search.get_image(conn, int(iid), int(user["id"]), allowed)
        if detail:
            result["image"] = serialize_image(detail)
    return result


@router.post("/play/sessions")
def create_session(body: PlayBody, conn: Db, user: User):
    _validate_source(conn, user, body.source, body.order)
    spec = {
        "source": body.source.model_dump(exclude_none=True),
        "query": body.query,
        "order": body.order,
        "interval": max(0, int(body.interval)),
        "limit": max(0, int(body.limit)),
        "loop": bool(body.loop),
    }
    allowed = auth.visible_library_ids(conn, user)
    session = play.create_session(conn, int(user["id"]), allowed, spec)
    row = play.get_session_by_token(conn, session["token"])
    return _state(conn, row, user)


@router.get("/play/sessions/{token}")
def get_session(token: str, conn: Db, user: User):
    session = _session_or_404(conn, token, user)
    return _state(conn, session, user)


@router.post("/play/sessions/{token}/next")
def next_item(token: str, conn: Db, user: User, body: StepBody | None = None):
    session = _session_or_404(conn, token, user)
    step = body.step if body else 1
    result = play.step(conn, session, step)
    return _attach_image(conn, result, user)


@router.post("/play/sessions/{token}/prev")
def prev_item(token: str, conn: Db, user: User):
    session = _session_or_404(conn, token, user)
    result = play.step(conn, session, -1)
    return _attach_image(conn, result, user)


@router.post("/play/sessions/{token}/goto")
def goto_item(token: str, body: GotoBody, conn: Db, user: User):
    session = _session_or_404(conn, token, user)
    result = play.goto(conn, session, body.position)
    return _attach_image(conn, result, user)


@router.post("/play/sessions/{token}/reshuffle")
def reshuffle(token: str, conn: Db, user: User):
    session = _session_or_404(conn, token, user)
    allowed = auth.visible_library_ids(conn, user)
    play.reshuffle(conn, session, int(user["id"]), allowed)
    session = play.get_session_by_token(conn, token)
    return _state(conn, session, user)


@router.get("/play/sessions/{token}/items")
def session_items(token: str, conn: Db, user: User, offset: int = 0, limit: int = 20):
    session = _session_or_404(conn, token, user)
    ids = play.queue_items(conn, int(session["id"]), offset=offset, limit=min(limit, 100))
    allowed = auth.visible_library_ids(conn, user)
    items = []
    for iid in ids:
        detail = search.get_image(conn, iid, int(user["id"]), allowed)
        if detail:
            items.append(serialize_image(detail))
    return {"items": items, "offset": offset}


@router.delete("/play/sessions/{token}")
def delete_session(token: str, conn: Db, user: User):
    session = _session_or_404(conn, token, user)
    conn.execute("DELETE FROM play_sessions WHERE id=?", (session["id"],))
    conn.commit()
    return {"ok": True}
