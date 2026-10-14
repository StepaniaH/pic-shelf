"""Search grammar and personal saved queries, including dynamic smart albums."""
from fastapi import APIRouter, Query
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .. import saved_queries, search
from ..access import AccessContext
from .types import Identifier, MAX_INTEGER
from .deps import Db, User, serialize_image

router = APIRouter(tags=['search'])


class Rule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    query: str = ''
    sort: str = 'newest'
    kind: str = 'query'
    display: dict | None = None


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt
    name: str | None = None
    query: str | None = None
    sort: str | None = None
    kind: str | None = None
    display: dict | None = None


class Condition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = ''
    kind: str
    values: Annotated[list[str], Field(min_length=1, max_length=100)] | Annotated[str, Field(min_length=1, max_length=4096)]
    neg: StrictBool = False


@router.get('/search/fields')
def fields(user: User):
    return {'fields':search.condition_fields(), 'max_length':search.MAX_QUERY_LENGTH}


@router.post('/search/append')
def append(body: Condition, user: User):
    query = search.append_condition(**body.model_dump())
    return {'query':query,'clauses':search.parse(query).clauses}


@router.get('/queries')
def list_queries(conn: Db,user: User,limit: int=Query(100,ge=1,le=100),offset: int=Query(0,ge=0,le=MAX_INTEGER)):
    return saved_queries.list_queries(conn,AccessContext(user['id']),limit=limit,offset=offset)


@router.post('/queries')
def create(body: Rule,conn: Db,user: User):
    return saved_queries.create(conn,AccessContext(user['id']),**body.model_dump())


@router.get('/queries/{query_id}')
def get(query_id: Identifier,conn: Db,user: User):
    return saved_queries.get(conn,AccessContext(user['id']),query_id)


@router.patch('/queries/{query_id}')
def update(query_id: Identifier,body: Edit,conn: Db,user: User):
    return saved_queries.update(conn,AccessContext(user['id']),query_id,**body.model_dump(exclude_unset=True))


@router.get('/queries/{query_id}/images')
def images(query_id: Identifier,conn: Db,user: User,limit: int=Query(60,ge=0,le=10000),offset: int=Query(0,ge=0,le=MAX_INTEGER)):
    result = saved_queries.run(conn,AccessContext(user['id']),query_id,limit=limit,offset=offset)
    result['items'] = [serialize_image(item) for item in result['items']]
    return result


@router.get('/queries/{query_id}/export')
def export(query_id: Identifier,conn: Db,user: User):
    return saved_queries.export(conn,AccessContext(user['id']),query_id)
