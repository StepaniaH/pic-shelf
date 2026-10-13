"""User-scoped interchange and fixed, explicitly confirmed import plans."""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from .. import manifest
from ..access import AccessContext
from ..errors import ServiceError
from ..i18n import t
from .deps import Db, User
from .types import Identifier

router = APIRouter(prefix='/manifests', tags=['manifests'])


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manifest: dict
    mappings: dict


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: StrictBool = False


@router.get('/export')
def export(conn: Db, user: User, library_id: list[Identifier] | None = Query(None)):
    return manifest.export(conn, AccessContext(user['id']), library_id)


@router.post('/preview')
def preview(body: Preview, conn: Db, user: User):
    return manifest.preview(conn, AccessContext(user['id']), body.manifest, body.mappings)


@router.get('/plans/{plan_id}')
def plan(plan_id: str, conn: Db, user: User):
    return manifest.get_plan(conn, AccessContext(user['id']), plan_id)


@router.post('/plans/{plan_id}/execute')
def execute(plan_id: str, body: Confirm, request: Request, conn: Db, user: User):
    if not body.confirm:
        raise ServiceError('confirmation_required', t('执行需要 confirm=true 明确确认'),
                           next_step=t('请先检查元数据导入预演，再明确确认执行'))
    return manifest.execute(conn, request.app.state.cfg, AccessContext(user['id']), plan_id)
