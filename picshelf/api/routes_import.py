"""导入路由：托管库导入（预演 / 执行）。

权限：对目标库需要 manage 级访问（管理员天然满足）。
安全：执行必须显式 confirm=true；move 模式由前端二次确认（服务端同价要求）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import auth, importer, indexer
from .deps import Db, User, ensure_library_access
from ..i18n import t

router = APIRouter(prefix="/import", tags=["import"])

MAX_ITEMS_IN_RESPONSE = 300


class ImportBody(BaseModel):
    source_dir: str
    library_id: int
    mode: str = "hardlink"
    rename: bool = True


class ExecuteBody(ImportBody):
    confirm: bool = False


def _target_library(conn, user, library_id: int):
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    if lib is None:
        raise HTTPException(status_code=404, detail=t("库不存在"))
    ensure_library_access(conn, user, library_id, minimum="manage")
    return lib


def _trim_plan(plan: dict) -> dict:
    total = len(plan["items"])
    plan["items_truncated"] = total > MAX_ITEMS_IN_RESPONSE
    plan["items"] = plan["items"][:MAX_ITEMS_IN_RESPONSE]
    plan["items_total"] = total
    return plan


@router.post("/plan")
def plan_import(body: ImportBody, conn: Db, user: User):
    lib = _target_library(conn, user, body.library_id)
    try:
        p = importer.plan_import(conn, lib, body.source_dir, mode=body.mode, rename=body.rename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _trim_plan(p)


@router.post("/execute")
def execute_import(body: ExecuteBody, request: Request, conn: Db, user: User):
    if not body.confirm:
        raise HTTPException(status_code=400, detail=t("执行需要 confirm=true 明确确认"))
    lib = _target_library(conn, user, body.library_id)
    try:
        p = importer.plan_import(conn, lib, body.source_dir, mode=body.mode, rename=body.rename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    done = importer.execute_import(p, apply=True)
    scan = None
    if done["hardlink"] or done["copy"] or done["move"]:
        scan = indexer.scan_library(conn, request.app.state.cfg, int(lib["id"]))
    return {"planned": p["stats"], "done": done, "scan": scan}
