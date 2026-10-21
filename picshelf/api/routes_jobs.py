"""Additive asynchronous endpoints; existing synchronous routes stay compatible."""
from fastapi import APIRouter, Query, Request
from pydantic import StrictBool, BaseModel, ConfigDict, Field

from .. import import_plans, jobs
from ..access import AccessContext
from ..errors import ServiceError
from ..i18n import t
from .types import Identifier, MAX_INTEGER
from .deps import Db, User

router = APIRouter(tags=["jobs"])


class SubmitJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=128)
    full: StrictBool = False
    outdated_only: StrictBool = False


class SubmitImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=128)
    confirm: StrictBool = False


@router.post("/libraries/{library_id}/scan-jobs", status_code=202)
def submit_scan(library_id: Identifier, body: SubmitJob, request: Request, conn: Db, user: User):
    value = jobs.submit(conn, AccessContext(user["id"]), "scan", library_id,
                         request_key=body.request_key, full=body.full, outdated_only=body.outdated_only)
    request.app.state.workers.start()
    return value


@router.post("/libraries/{library_id}/rebuild-jobs", status_code=202)
def submit_rebuild(library_id: Identifier, body: SubmitJob, request: Request, conn: Db, user: User):
    value = jobs.submit(conn, AccessContext(user["id"]), "rebuild", library_id,
                         request_key=body.request_key, outdated_only=body.outdated_only)
    request.app.state.workers.start()
    return value


@router.post("/import/plans/{plan_id}/jobs", status_code=202)
def submit_import(plan_id: str, body: SubmitImport, request: Request, conn: Db, user: User):
    if not body.confirm:
        raise ServiceError("confirmation_required", t("执行需要 confirm=true 明确确认"),
                           next_step=t("请先查看预演结果，再确认执行"))
    access = AccessContext(user["id"])
    plan = import_plans.get_plan(conn, access, plan_id)
    value = jobs.submit(conn, access, "import", plan["library"]["id"], request_key=body.request_key, plan_id=plan_id)
    request.app.state.workers.start()
    return value


@router.get("/jobs")
def list_jobs(conn: Db, user: User, offset: int = Query(0,ge=0,le=MAX_INTEGER), limit: int = Query(50, ge=1, le=100),
              include_items: bool = True):
    return jobs.list_jobs(conn, AccessContext(user["id"]), offset=offset, limit=limit, include_items=include_items)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, conn: Db, user: User, offset: int = Query(0,ge=0,le=MAX_INTEGER), limit: int = Query(100, ge=1, le=1000),
            include_items: bool = True):
    return jobs.get_job(conn, AccessContext(user["id"]), job_id, offset=offset, limit=limit, include_items=include_items)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, conn: Db, user: User):
    return jobs.cancel(conn, AccessContext(user["id"]), job_id)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, request: Request, conn: Db, user: User):
    value = jobs.retry(conn, AccessContext(user["id"]), job_id)
    request.app.state.workers.start()
    return value
