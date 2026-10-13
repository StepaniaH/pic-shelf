"""Playback rules and resumable, permission-checked snapshot navigation."""
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .. import auth, play, play_rules, search
from ..access import AccessContext
from .deps import Db, User, serialize_image

router=APIRouter(tags=['play'])


class PlaySource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    library_id: StrictInt | None = None
    album_id: StrictInt | None = None
    artist_id: StrictInt | None = None
    tag: str | None = None
    favorites: StrictBool = False
    rating_min: StrictInt | None = None
    workflow: str | None = None


class PlayBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source: PlaySource = Field(default_factory=PlaySource)
    query: str = ''
    sort: str = 'path'
    order: str = 'sequential'
    interval: StrictInt = Field(default=0,ge=0,le=86400)
    limit: StrictInt = Field(default=0,ge=0,le=1000000)
    loop: StrictBool = False
    size: str = 'fit'
    captions: StrictBool = True
    seed: StrictInt | None = Field(default=None,ge=0,le=play.MAX_SEED)


class RequestKey(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key: str | None = Field(default=None,min_length=1,max_length=128)
    expected_version: StrictInt | None = Field(default=None,ge=0)


class StepBody(RequestKey):
    step: StrictInt = Field(default=1,ge=1,le=10000)


class GotoBody(RequestKey):
    position: StrictInt = Field(default=0,ge=0,le=9999)


class PauseBody(RequestKey):
    paused: StrictBool


class RuleBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str
    spec: dict


class RuleEdit(RuleBody):
    version: StrictInt


def _session_or_404(conn,token,user,*,check_scope=True):
    return play.session_for(conn,AccessContext(user['id']),token,check_scope=check_scope)


def _attach_image(conn,result,user):
    result=dict(result);result['image']=None
    if result.get('image_id') is not None:
        detail=search.get_image(conn,result['image_id'],user['id'],auth.visible_library_ids(conn,user))
        if detail:result['image']=serialize_image(detail)
        else:
            result['image_id']=None;result['unavailable']='image_unavailable'
    return result


def _state(conn,session,user):
    return _attach_image(conn,play.session_state(conn,AccessContext(user['id']),session['token']),user)


@router.post('/play/sessions')
def create_session(body: PlayBody,conn: Db,user: User):
    result=play.create_for_user(conn,AccessContext(user['id']),body.model_dump(exclude_none=True))
    return _attach_image(conn,result,user)


@router.get('/play/sessions/{token}')
def get_session(token: str,conn: Db,user: User):
    return _attach_image(conn,play.session_state(conn,AccessContext(user['id']),token),user)


def _navigate(conn,user,token,action,body):
    return _attach_image(conn,play.navigate(conn,AccessContext(user['id']),token,action,**body.model_dump()),user)


@router.post('/play/sessions/{token}/next')
def next_item(token: str,conn: Db,user: User,body: StepBody | None = None):
    return _navigate(conn,user,token,'next',body or StepBody())


@router.post('/play/sessions/{token}/prev')
def prev_item(token: str,conn: Db,user: User,body: RequestKey | None = None):
    return _navigate(conn,user,token,'prev',body or RequestKey())


@router.post('/play/sessions/{token}/goto')
def goto_item(token: str,body: GotoBody,conn: Db,user: User):
    return _navigate(conn,user,token,'goto',body)


@router.post('/play/sessions/{token}/pause')
def pause(token: str,body: PauseBody,conn: Db,user: User):
    return _navigate(conn,user,token,'pause',body)


@router.post('/play/sessions/{token}/reshuffle')
def reshuffle(token: str,conn: Db,user: User,body: RequestKey | None = None):
    return _navigate(conn,user,token,'reshuffle',body or RequestKey())


@router.get('/play/sessions/{token}/items')
def session_items(token: str,conn: Db,user: User,offset: int=Query(0,ge=0),limit: int=Query(20,ge=0,le=100)):
    session=_session_or_404(conn,token,user)
    ids=play.queue_items(conn,session['id'],offset=offset,limit=limit)
    allowed=auth.visible_library_ids(conn,user)
    items=[serialize_image(detail) for iid in ids if (detail:=search.get_image(conn,iid,user['id'],allowed))]
    return {'items':items,'offset':offset}


@router.delete('/play/sessions/{token}')
def delete_session(token: str,conn: Db,user: User):
    session=_session_or_404(conn,token,user,check_scope=False)
    conn.execute('DELETE FROM play_sessions WHERE id=?',(session['id'],));conn.commit()
    return {'ok':True}


@router.get('/play/rules')
def list_rules(conn: Db,user: User,offset: int=Query(0,ge=0),limit: int=Query(100,ge=1,le=100)):
    return play_rules.list_rules(conn,AccessContext(user['id']),offset=offset,limit=limit)


@router.post('/play/rules')
def create_rule(body: RuleBody,conn: Db,user: User):
    return play_rules.create(conn,AccessContext(user['id']),**body.model_dump())


@router.get('/play/rules/{rule_id}')
def get_rule(rule_id: int,conn: Db,user: User):
    return play_rules.get(conn,AccessContext(user['id']),rule_id)


@router.patch('/play/rules/{rule_id}')
def edit_rule(rule_id: int,body: RuleEdit,conn: Db,user: User):
    return play_rules.update(conn,AccessContext(user['id']),rule_id,**body.model_dump())


@router.post('/play/rules/{rule_id}/start')
def start_rule(rule_id: int,conn: Db,user: User):
    return _attach_image(conn,play_rules.start(conn,AccessContext(user['id']),rule_id),user)


@router.get('/play/rules/{rule_id}/export')
def export_rule(rule_id: int,conn: Db,user: User):
    return play_rules.export(conn,AccessContext(user['id']),rule_id)
