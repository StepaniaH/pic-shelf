"""Metadata batch previews, execution, cancellation and guarded undo."""
from fastapi import APIRouter,Query,Request
from pydantic import StrictBool, BaseModel,ConfigDict,Field,StrictInt

from .. import batches
from ..access import AccessContext
from ..errors import ServiceError
from ..i18n import t
from .types import BodyIdentifier, MAX_INTEGER
from .deps import Db,User

router = APIRouter(prefix="/batches",tags=["batches"])


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: dict
    image_ids: list[BodyIdentifier] | None = Field(default=None,min_length=1,max_length=10000)
    query: dict | None = None
    request_key: str | None = Field(default=None,min_length=1,max_length=128)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class Execute(Confirm):
    retry: StrictBool = False
    background: StrictBool = False


def _confirm(body):
    if not body.confirm:
        raise ServiceError("confirmation_required",t("执行需要 confirm=true 明确确认"),
                           next_step=t("请先查看批次目标数量和范围，再确认操作"))


@router.post("/preview")
def preview(body: Preview,conn: Db,user: User):
    return batches.preview(conn,AccessContext(user["id"]),**body.model_dump())


@router.get("")
def list_batches(conn: Db,user: User,offset: int=Query(0,ge=0,le=MAX_INTEGER),limit: int=Query(50,ge=1,le=100)):
    return batches.list_batches(conn,AccessContext(user["id"]),offset=offset,limit=limit)


@router.get("/{batch_id}")
def get(batch_id: str,conn: Db,user: User,offset: int=Query(0,ge=0,le=MAX_INTEGER),limit: int=Query(200,ge=1,le=1000),include_items: bool=True):
    return batches.get(conn,AccessContext(user["id"]),batch_id,offset=offset,limit=limit,include_items=include_items)


@router.post("/{batch_id}/execute")
def execute(batch_id: str,body: Execute,request: Request,conn: Db,user: User):
    _confirm(body)
    access = AccessContext(user["id"])
    if body.background:
        value = batches.queue(conn,access,batch_id,retry=body.retry)
        request.app.state.workers.start()
        return value
    return batches.execute(conn,request.app.state.cfg,access,batch_id,retry=body.retry)


@router.post("/{batch_id}/cancel")
def cancel(batch_id: str,conn: Db,user: User):
    return batches.cancel(conn,AccessContext(user["id"]),batch_id)


@router.post("/{batch_id}/undo")
def undo(batch_id: str,body: Confirm,request: Request,conn: Db,user: User):
    _confirm(body)
    return batches.undo(conn,request.app.state.cfg,AccessContext(user["id"]),batch_id)
