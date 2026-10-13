"""Manual relationships and preferred viewing files, checked against live access."""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .. import works
from ..access import AccessContext
from ..errors import ServiceError
from ..i18n import t
from .deps import Db, User
from .types import BodyIdentifier, Identifier

router = APIRouter(prefix='/works', tags=['works'])


class Member(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_id: BodyIdentifier
    role: Literal['original', 'compressed', 'crop', 'resize', 'revision', 'other']


class Members(BaseModel):
    model_config = ConfigDict(extra='forbid')
    members: list[Member] = Field(min_length=1, max_length=1000)


class Create(Members):
    name: str = Field(min_length=1, max_length=200)
    preferred_image_id: BodyIdentifier | None = None


class Preference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_id: BodyIdentifier


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: StrictBool = False


def _confirm(body):
    if not body.confirm:
        raise ServiceError('confirmation_required', t('执行需要 confirm=true 明确确认'),
                           next_step=t('请先查看作品关联和待匹配引用，再明确确认解除'))


@router.post('')
def create(body: Create, conn: Db, user: User):
    return works.create(conn, AccessContext(user['id']), body.name, [member.model_dump() for member in body.members], body.preferred_image_id)


@router.get('/by-image/{image_id}')
def by_image(image_id: Identifier, conn: Db, user: User):
    return works.by_image(conn, AccessContext(user['id']), image_id)


@router.get('/{work_id}')
def get(work_id: Identifier, conn: Db, user: User):
    return works.get(conn, AccessContext(user['id']), work_id)


@router.get('/{work_id}/pending')
def pending(work_id: Identifier, conn: Db, user: User):
    return works.pending(conn, AccessContext(user['id']), work_id)


@router.delete('/{work_id}/pending/{image_uid}')
def drop_pending(work_id: Identifier, image_uid: str, body: Confirm, conn: Db, user: User):
    _confirm(body)
    return works.drop_pending(conn, AccessContext(user['id']), work_id, image_uid)


@router.post('/{work_id}/members')
def link(work_id: Identifier, body: Members, conn: Db, user: User):
    return works.link(conn, AccessContext(user['id']), work_id, [member.model_dump() for member in body.members])


@router.delete('/{work_id}/members/{image_id}')
def unlink(work_id: Identifier, image_id: Identifier, body: Confirm, conn: Db, user: User):
    _confirm(body)
    return works.unlink(conn, AccessContext(user['id']), work_id, image_id)


@router.put('/{work_id}/preferred')
def prefer(work_id: Identifier, body: Preference, conn: Db, user: User):
    return works.prefer(conn, AccessContext(user['id']), work_id, body.image_id)
