"""导入路由：托管库导入（预演 / 执行）。

权限：对目标库需要 manage 级访问（管理员天然满足）。
安全：执行必须显式 confirm=true；move 模式由前端二次确认（服务端同价要求）。
"""
from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import StrictBool, BaseModel, ConfigDict

from .. import import_plans, source_access, uploads
from ..access import AccessContext
from .types import Identifier, BodyIdentifier, MAX_INTEGER
from .deps import Db, User, ensure_library_access
from ..i18n import t

router = APIRouter(prefix="/import", tags=["import"])


class UploadBody(BaseModel):
    library_id: BodyIdentifier


class UploadPreview(BaseModel):
    rename: StrictBool = True
    album_id: BodyIdentifier | None = None


@router.get('/sources')
def browse_sources(conn: Db, user: User, library_id: Identifier | None = None, path: str | None = None):
    access = AccessContext(user['id'])
    if library_id is None:
        access.require_capability(conn, 'admin')
    else:
        _target_library(conn, user, library_id)
    return source_access.browse(conn, access, path)


@router.post('/uploads')
def create_upload(body: UploadBody, request: Request, conn: Db, user: User):
    return uploads.create(conn, request.app.state.cfg, AccessContext(user['id']), body.library_id)


@router.put('/uploads/{upload_id}/file')
async def upload_file(upload_id: str, path: str, request: Request, conn: Db, user: User):
    return await uploads.receive(conn, request.app.state.cfg, AccessContext(user['id']), upload_id, path, request)


@router.post('/uploads/{upload_id}/plan')
def preview_upload(upload_id: str, body: UploadPreview, request: Request, conn: Db, user: User):
    return uploads.preview(conn, request.app.state.cfg, AccessContext(user['id']), upload_id, rename=body.rename, album_id=body.album_id)


@router.post('/uploads/{upload_id}/discard')
def discard_upload(upload_id: str, request: Request, conn: Db, user: User):
    return uploads.discard(conn, request.app.state.cfg, AccessContext(user['id']), upload_id)

class ImportBody(BaseModel):
    source_dir: str
    library_id: BodyIdentifier
    mode: Literal["hardlink", "copy", "move"] | None = None
    rename: StrictBool = True
    template: str | None = None
    template_fields: dict | None = None
    album_id: BodyIdentifier | None = None


class ExecuteBody(ImportBody):
    confirm: StrictBool = False


def _target_library(conn, user, library_id: Identifier):
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    if lib is None:
        raise HTTPException(status_code=404, detail=t("库不存在"))
    ensure_library_access(conn, user, library_id, minimum="manage")
    return lib


@router.post("/plan")
def plan_import(body: ImportBody, conn: Db, user: User):
    _target_library(conn, user, body.library_id)
    try:
        return import_plans.create_plan(conn, AccessContext(user["id"]), body.library_id,
                                        body.source_dir, mode=body.mode, rename=body.rename,
                                        template=body.template, template_fields=body.template_fields, album_id=body.album_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/execute")
def execute_import(body: ExecuteBody, request: Request, conn: Db, user: User):
    if not body.confirm:
        raise HTTPException(status_code=400, detail=t("执行需要 confirm=true 明确确认"))
    _target_library(conn, user, body.library_id)
    try:
        plan = import_plans.create_plan(conn, AccessContext(user["id"]), body.library_id,
                                          body.source_dir, mode=body.mode, rename=body.rename,
                                          template=body.template, template_fields=body.template_fields, album_id=body.album_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**import_plans.execute_plan(conn, request.app.state.cfg, AccessContext(user["id"]), plan["plan_id"]),
            "execution_semantics": "immediate_plan"}


class ConfirmPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    retry: StrictBool = False


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, conn: Db, user: User, offset: int = Query(0,ge=0,le=MAX_INTEGER),
             limit: int = Query(300, ge=1, le=1000)):
    return import_plans.get_plan(conn, AccessContext(user["id"]), plan_id, offset=offset, limit=limit)


@router.post("/plans/{plan_id}/execute")
def execute_saved_plan(plan_id: str, body: ConfirmPlan, request: Request, conn: Db, user: User):
    if not body.confirm:
        raise HTTPException(status_code=400, detail=t("执行需要 confirm=true 明确确认"))
    return import_plans.execute_plan(conn, request.app.state.cfg, AccessContext(user["id"]), plan_id, retry=body.retry)


@router.post("/plans/{plan_id}/cancel")
def cancel_saved_plan(plan_id: str, conn: Db, user: User):
    return import_plans.cancel_plan(conn, AccessContext(user["id"]), plan_id)
