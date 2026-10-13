"""Explicit directory rebinding and recoverable managed-library reorganization."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .. import relocations, reorganize
from ..access import AccessContext
from ..errors import ServiceError
from ..i18n import t
from .deps import Db, User
from .types import BodyIdentifier

router = APIRouter(prefix='/paths', tags=['paths'])


class RootPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    library_id: BodyIdentifier
    target_path: str = Field(min_length=1, max_length=4096)


class OrganizePreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    library_id: BodyIdentifier
    template: str = Field(min_length=1, max_length=1000)


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: StrictBool = False


@router.post('/relocate')
def preview(body: RootPreview, conn: Db, user: User):
    return relocations.preview(conn, AccessContext(user['id']), body.library_id, body.target_path)


@router.get('/relocate/{plan_id}')
def get(plan_id: str, conn: Db, user: User):
    return relocations.get(conn, AccessContext(user['id']), plan_id)


@router.post('/relocate/{plan_id}/execute')
def execute(plan_id: str, body: Confirm, request: Request, conn: Db, user: User):
    if not body.confirm:
        raise ServiceError('confirmation_required', t('执行需要 confirm=true 明确确认'),
                           next_step=t('请先检查库路径迁移预演，再明确确认执行'))
    return relocations.execute(conn, request.app.state.cfg, AccessContext(user['id']), plan_id)


@router.post('/reorganize')
def organize(body: OrganizePreview, request: Request, conn: Db, user: User):
    return reorganize.preview(conn, request.app.state.cfg, AccessContext(user['id']), body.library_id, body.template)
