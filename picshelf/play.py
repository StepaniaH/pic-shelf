"""Permission-checked playback snapshots, navigation and explicit random modes."""
from __future__ import annotations

import json
import random
import secrets
import sqlite3

from . import catalog, search
from .db import transaction, utcnow
from .errors import ServiceError
from .i18n import t
from .inputs import integer_in_range

MAX_QUEUE = 10000
MAX_SEED = 2**53-1  # Exact in JSON clients using JavaScript numbers.
ORDERS = ('sequential', 'shuffle', 'random', 'shuffle_each_round', 'sample')


def _invalid():
    return ServiceError('invalid_play_rule', t('播放配置无效，请检查模式、数量、间隔和显示选项'),
                        next_step=t('请选择有效播放模式；间隔为 0–86400 秒，数量为非负整数'))


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) - {'source','query','sort','order','interval','limit','loop','size','captions','seed'}:
        raise _invalid()
    value = dict(source={},query='',sort='path',order='sequential',interval=0,limit=0,loop=False,size='fit',captions=True)
    value.update(spec)
    if (value['order'] not in ORDERS or value['sort'] not in tuple(k for k in search.SORTS if k != 'random')
        or value['size'] not in ('fit','fill','original') or type(value['loop']) is not bool
        or type(value['captions']) is not bool or type(value['interval']) is not int or not 0 <= value['interval'] <= 86400
        or type(value['limit']) is not int or not 0 <= value['limit'] <= 1_000_000
        or ('seed' in value and (type(value['seed']) is not int or not 0 <= value['seed'] <= MAX_SEED))):
        raise _invalid()
    src = value['source']
    if not isinstance(src,dict) or set(src) - {'library_id','album_id','artist_id','tag','favorites','rating_min','workflow'}:
        raise _invalid()
    for field in ('library_id','album_id','artist_id'):
        if src.get(field) is not None and (type(src[field]) is not int or not 1 <= src[field] <= 2**63-1): raise _invalid()
    if (('favorites' in src and type(src['favorites']) is not bool)
        or (src.get('rating_min') is not None and (type(src['rating_min']) is not int or not 0 <= src['rating_min'] <= 10))
        or (src.get('tag') is not None and (not isinstance(src['tag'],str) or not 1 <= len(src['tag'].strip()) <= 300))
        or (src.get('workflow') is not None and src['workflow'] not in ('inbox','done'))): raise _invalid()
    parsed = search.parse(value['query'])
    if parsed.errors: raise search.invalid_query(parsed.errors)
    return value


def check_source(conn, access, spec):
    access.user(conn)
    src = spec['source']
    if src.get('library_id') is not None: access.require_library(conn,src['library_id'])
    if src.get('album_id') is not None: access.require_album(conn,src['album_id'])
    if src.get('artist_id') is not None: access.require_entity(conn,'artist',src['artist_id'])


def _source_clauses(spec):
    where, params = catalog.image_filter_clauses(catalog.ImageQuery(**spec['source']))
    return ['i.missing=0', *where], params


def _choose(spec, candidates, cycle=0):
    rng=random.Random(spec.get('seed',0)+cycle)
    if spec['order']=='sample':
        return rng.choices(candidates,k=min(spec['limit'] or len(candidates),MAX_QUEUE)) if candidates else []
    result=list(candidates)
    if spec['order']!='sequential':rng.shuffle(result)
    return result


def _snapshot(conn,user_id,allowed,spec):
    extra,extra_params=_source_clauses(spec)
    capacity=min(spec['limit'] or MAX_QUEUE,MAX_QUEUE) if spec['order']!='sample' else MAX_QUEUE
    if spec['order']=='sequential':
        result=search.search_images(conn,user_id,allowed,query=spec['query'],sort=spec['sort'],limit=capacity,
                                     extra_where=extra,extra_params=extra_params,ids_only=True)
        candidates=[item['id'] for item in result['items']];total=result['total']
    else:
        # Reservoir sampling considers every match while holding at most 10000 IDs.
        where,params=search.build_where(search.parse(spec['query']),user_id,allowed)
        where+=' AND '+' AND '.join(extra);params.update(extra_params)
        rng=random.Random(spec.get('seed',0)); candidates=[];total=0
        for row in conn.execute('SELECT i.id FROM images i WHERE '+where+' ORDER BY i.id',params):
            total+=1
            if len(candidates)<capacity:candidates.append(row[0])
            else:
                slot=rng.randrange(total)
                if slot<capacity:candidates[slot]=row[0]
        candidates.sort()
    items=_choose(spec,candidates)
    truncated=(spec['limit']==0 or spec['limit']>MAX_QUEUE) and (total>MAX_QUEUE or (spec['order']=='sample' and spec['limit']>MAX_QUEUE and total>0))
    candidate_limited=spec['order']=='sample' and total>MAX_QUEUE
    warnings=(['queue_limit'] if truncated else [])+(['candidate_limit'] if candidate_limited else [])
    info={'matched_total':total,'candidate_count':len(candidates),'queue_limit':MAX_QUEUE,
          'requested_limit':spec['limit'],'truncated':truncated,'candidate_limited':candidate_limited,'warnings':warnings}
    return items,candidates,info


def build_queue(conn,user_id,allowed_library_ids,spec):
    spec=validate_spec(spec);spec.setdefault('seed',secrets.randbits(53))
    with transaction(conn):
        return _snapshot(conn,user_id,allowed_library_ids,spec)[0]


def _store_items(conn,session_id,items,candidates):
    conn.execute('DELETE FROM play_items WHERE session_id=?',(session_id,))
    conn.execute('DELETE FROM play_candidates WHERE session_id=?',(session_id,))
    conn.executemany('INSERT INTO play_items VALUES(?,?,?)',[(session_id,p,iid) for p,iid in enumerate(items)])
    conn.executemany('INSERT INTO play_candidates SELECT ?,?,?,library_id FROM images WHERE id=?',
                     [(session_id,p,iid,iid) for p,iid in enumerate(candidates)])


def create_session(conn,user_id,allowed_library_ids,spec,*,access=None,rule_id=None,start_image_id=None):
    spec=validate_spec(spec);spec.setdefault('seed',secrets.randbits(53))
    with transaction(conn):
        if access is not None:
            check_source(conn,access,spec);allowed_library_ids=access.allowed_libraries(conn)
        items,candidates,info=_snapshot(conn,user_id,allowed_library_ids,spec)
        cursor = -1
        if start_image_id is not None:
            if type(start_image_id) is not int or start_image_id not in items:
                raise ServiceError('play_start_unavailable', t('当前图片不在播放范围内'), status=400,
                                   next_step=t('请调整筛选和播放数量，或取消从当前图片开始'))
            cursor = items.index(start_image_id)
        token=secrets.token_urlsafe(12)
        session_id=conn.execute('INSERT INTO play_sessions(token,user_id,spec,order_mode,interval,loop,cursor,created_at,rule_id,snapshot_info) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)',(token,user_id,json.dumps(spec,ensure_ascii=False),spec['order'],spec['interval'],int(spec['loop']),cursor,
                                         utcnow(),rule_id,json.dumps(info))).lastrowid
        _store_items(conn,session_id,items,candidates)
    return _state(conn,get_session_by_token(conn,token))


def create_for_user(conn,access,spec,*,rule_id=None,start_image_id=None):
    return create_session(conn,access.user_id,None,spec,access=access,rule_id=rule_id,start_image_id=start_image_id)


def get_session_by_token(conn,token):
    return conn.execute('SELECT * FROM play_sessions WHERE token=?',(token,)).fetchone()


def session_for(conn,access,token,*,check_scope=True):
    user=access.user(conn);session=get_session_by_token(conn,token)
    if session is None or (session['user_id']!=access.user_id and not (access.local_admin or user['is_admin'])):
        raise ServiceError('play_session_not_found',t('播放会话不存在'),status=404,next_step=t('请重新打开播放规则或创建会话'))
    if check_scope:
        libraries=conn.execute('SELECT DISTINCT library_id FROM play_candidates WHERE session_id=? AND library_id IS NOT NULL '
            'UNION SELECT i.library_id FROM play_items p JOIN images i ON i.id=p.image_id WHERE p.session_id=?',(session['id'],session['id']))
        for row in libraries:
            try:access.require_library(conn,row[0])
            except ServiceError as exc:
                raise ServiceError('play_scope_changed',t('播放队列的库权限已变化'),status=403,
                                   next_step=t('请按当前权限重新创建播放会话')) from exc
    return session


def _item_count(conn,session_id):
    return conn.execute('SELECT COUNT(*) FROM play_items WHERE session_id=?',(session_id,)).fetchone()[0]


def _cursor_item(conn,session_id,cursor):
    row=conn.execute('SELECT image_id FROM play_items WHERE session_id=? AND position=?',(session_id,cursor)).fetchone()
    return row[0] if row else None


def _state(conn,session):
    total=_item_count(conn,session['id']);cursor=session['cursor'];iid=_cursor_item(conn,session['id'],cursor)
    row=conn.execute('SELECT missing FROM images WHERE id=?',(iid,)).fetchone() if iid is not None else None
    info={'matched_total':None,'candidate_count':total,'queue_limit':MAX_QUEUE,'requested_limit':None,
          'truncated':None,'candidate_limited':None,'warnings':['legacy_scope_unknown']}
    info.update(json.loads(session['snapshot_info']))
    return {**info,'session_id':session['id'],'token':session['token'],
            'spec':json.loads(session['spec']),'total':total,'cursor':cursor,'position':cursor+1 if cursor>=0 else 0,
            'image_id':iid,'unavailable':'image_unavailable' if iid is not None and (row is None or row['missing']) else None,
            'loop':bool(session['loop']),'interval':session['interval'],'order':session['order_mode'],
            'paused':bool(session['paused']),'version':session['version'],'cycle':session['cycle'],'rule_id':session['rule_id']}


def session_state(conn,access,token):
    with transaction(conn):
        return _state(conn,session_for(conn,access,token))


def current(conn,session,user_id,allowed_library_ids):
    return _state(conn,get_session_by_token(conn,session['token']))


def _advance(conn,session,delta):
    if type(delta) is not int or not -MAX_QUEUE<=delta<=MAX_QUEUE:raise _invalid()
    with transaction(conn):
        session=get_session_by_token(conn,session['token']);total=_item_count(conn,session['id'])
        cursor=session['cursor'];target=cursor+delta;done=False
        if total==0:
            target=-1;done=True
        elif session['loop']:
            if delta>0 and target>=total and session['order_mode'] in ('shuffle_each_round','sample'):
                cycle=session['cycle']+target//total
                candidates=[r[0] for r in conn.execute('SELECT image_id FROM play_candidates WHERE session_id=? ORDER BY position',(session['id'],))]
                items=_choose(json.loads(session['spec']),candidates,cycle)
                conn.execute('DELETE FROM play_items WHERE session_id=?',(session['id'],))
                conn.executemany('INSERT INTO play_items VALUES(?,?,?)',[(session['id'],p,iid) for p,iid in enumerate(items)])
                conn.execute('UPDATE play_sessions SET cycle=? WHERE id=?',(cycle,session['id']))
            target%=total
        else:
            bounded=max(0,min(target,total-1));done=bounded==cursor and target!=bounded;target=bounded
        conn.execute('UPDATE play_sessions SET cursor=?,version=version+1,paused=CASE WHEN ? THEN 1 ELSE paused END WHERE id=?',
                     (target,done,session['id']))
        result=_state(conn,get_session_by_token(conn,session['token']));result['done']=done
        return result


def step(conn,session,delta):
    return _advance(conn,session,delta)


def goto(conn,session,position):
    if type(position) is not int or not 0<=position<MAX_QUEUE:raise _invalid()
    with transaction(conn):
        total=_item_count(conn,session['id']);cursor=min(position,total-1)
        conn.execute('UPDATE play_sessions SET cursor=?,version=version+1 WHERE id=?',(cursor,session['id']))
        result=_state(conn,get_session_by_token(conn,session['token']));result['done']=not total
        return result


def reshuffle(conn,session,user_id,allowed_library_ids):
    spec=validate_spec(json.loads(session['spec']));spec['seed']=secrets.randbits(53)
    with transaction(conn):
        items,candidates,info=_snapshot(conn,user_id,allowed_library_ids,spec)
        _store_items(conn,session['id'],items,candidates)
        conn.execute('UPDATE play_sessions SET cursor=-1,cycle=0,version=version+1,spec=?,snapshot_info=? WHERE id=?',
                     (json.dumps(spec,ensure_ascii=False),json.dumps(info),session['id']))
    return _state(conn,get_session_by_token(conn,session['token']))


def navigate(conn,access,token,action,*,position=None,step=1,paused=None,request_key=None,expected_version=None):
    if action not in ('next','prev','goto','pause','reshuffle') or (request_key is not None and
            (not isinstance(request_key,str) or not 1<=len(request_key)<=128)) or (expected_version is not None and
            (type(expected_version) is not int or expected_version<0)):raise _invalid()
    request=json.dumps(dict(action=action,position=position,step=step,paused=paused,expected_version=expected_version),sort_keys=True)
    with transaction(conn):
        session=session_for(conn,access,token)
        previous=conn.execute('SELECT request,result FROM play_requests WHERE session_id=? AND request_key=?',(session['id'],request_key)).fetchone() if request_key else None
        if previous:
            if previous['request']!=request:
                raise ServiceError('play_request_conflict',t('同一播放请求标识不能用于不同操作'),status=409,
                                   next_step=t('请重新读取会话状态后使用新的请求标识'))
            recorded=json.loads(previous['result'])
            if recorded.get('image_id') is not None:
                access.require_image(conn,recorded['image_id'],include_missing=True)
            return recorded
        if expected_version is not None and expected_version!=session['version']:
            raise ServiceError('play_state_changed',t('播放状态已被其他操作更新'),status=409,
                               next_step=t('请重新读取会话状态后继续'))
        if action=='next':
            if type(step) is not int or not 1<=step<=MAX_QUEUE:raise _invalid()
            result=_advance(conn,session,step)
        elif action=='prev':result=_advance(conn,session,-1)
        elif action=='goto':result=goto(conn,session,position)
        elif action=='reshuffle':
            spec=validate_spec(json.loads(session['spec']));check_source(conn,access,spec)
            result=reshuffle(conn,session,session['user_id'],access.allowed_libraries(conn))
        else:
            if type(paused) is not bool:raise _invalid()
            conn.execute('UPDATE play_sessions SET paused=?,version=version+1 WHERE id=?',(int(paused),session['id']))
            result=_state(conn,get_session_by_token(conn,token))
        if request_key:
            conn.execute('INSERT INTO play_requests VALUES(?,?,?,?)',(session['id'],request_key,request,json.dumps(result)))
        return result


def queue_items(conn,session_id,offset=0,limit=50):
    rows=conn.execute('SELECT image_id FROM play_items WHERE session_id=? ORDER BY position LIMIT ? OFFSET ?',
                      (session_id,max(0,limit),max(0,offset))).fetchall()
    return [r[0] for r in rows]


def queue_rows(conn, access, token, *, offset=0, limit=500):
    """Read a bounded table page without fetching each image's relationships."""
    if not integer_in_range(offset, 0) or not integer_in_range(limit, 1, 500):
        raise _invalid()
    with transaction(conn):
        session = session_for(conn, access, token)
        rows = conn.execute('''
            SELECT p.position,p.image_id AS id,
                   CASE WHEN i.missing=0 THEN i.filename END AS filename,
                   CASE WHEN i.missing=0 THEN i.width END AS width,
                   CASE WHEN i.missing=0 THEN i.height END AS height,
                   CASE WHEN i.missing=0 THEN COALESCE(r.value,-1) ELSE -1 END AS my_rating,
                   CASE WHEN i.missing=0 THEN COALESCE(f.user_id IS NOT NULL,0) ELSE 0 END AS favorite
            FROM play_items p LEFT JOIN images i ON i.id=p.image_id
            LEFT JOIN ratings r ON r.image_id=i.id AND r.user_id=?
            LEFT JOIN favorites f ON f.image_id=i.id AND f.user_id=?
            WHERE p.session_id=? ORDER BY p.position LIMIT ? OFFSET ?
        ''', (access.user_id, access.user_id, session['id'], limit, offset)).fetchall()
    return [dict(row) for row in rows]
