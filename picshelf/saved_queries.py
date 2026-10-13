"""Owned search rules; smart albums evaluate rules instead of storing members."""
from __future__ import annotations

import json

from . import db, search
from .errors import ServiceError
from .i18n import t


def _validate(rule):
    errors = search.parse(rule['query']).errors
    if not isinstance(rule['name'], str) or not 1 <= len(rule['name'].strip()) <= 200:
        errors.append(t("查询名称须为 1–200 个字符"))
    if rule['kind'] not in ('query', 'smart') or not isinstance(rule['sort'], str) or rule['sort'] not in search.SORTS:
        errors.append(t("查询类型或排序无效，请使用列表中的选项"))
    display = rule['display']
    if (not isinstance(display, dict) or set(display) - {'density','show_filename'}
        or display.get('density', 'comfortable') not in ('comfortable','compact')
        or type(display.get('show_filename', True)) is not bool):
        errors.append(t("显示偏好无效，请选择图片密度和文件名显示方式"))
    return errors


def _owned(conn, access, query_id):
    access.user(conn)
    row = conn.execute('SELECT * FROM saved_queries WHERE id=? AND owner_id=?',
                       (query_id, access.user_id)).fetchone()
    if row is None:
        raise ServiceError('saved_query_not_found',t("保存的查询不存在"),status=404,
                           next_step=t("请返回自己的查询列表后重试"))
    value = dict(row)
    try: value['display'] = json.loads(value['display'])
    except (TypeError, ValueError): value['display'] = None
    return value


def get(conn, access, query_id):
    value = _owned(conn,access,query_id)
    value['errors'] = _validate(value)
    value['error_codes'] = ['invalid_query'] * len(value['errors'])
    return value


def create(conn, access, *, name, query='', sort='newest', kind='query', display=None):
    access.user(conn)
    value = dict(name=name,query=query,sort=sort,kind=kind,
                 display=display if display is not None else {'density':'comfortable','show_filename':True})
    errors = _validate(value)
    if errors: raise search.invalid_query(errors)
    now = db.utcnow()
    with conn:
        query_id = conn.execute('INSERT INTO saved_queries(owner_id,name,kind,query,sort,display,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?)',(access.user_id,name.strip(),kind,query,sort,json.dumps(value['display']),now,now)).lastrowid
    return get(conn,access,query_id)


def update(conn, access, query_id, *, version, **changes):
    if type(version) is not int or version < 1 or not changes or set(changes) - {'name','query','sort','kind','display'}:
        raise search.invalid_query([t("更新字段或版本无效，请重新打开查询编辑")])
    conn.execute('BEGIN IMMEDIATE')
    try:
        value = _owned(conn,access,query_id)
        if value['version'] != version:
            raise ServiceError('saved_query_changed',t("查询已被更新，请重新载入后编辑"),status=409,
                               next_step=t("请保留当前输入，重新打开最新查询后重试"))
        value.update(changes)
        errors = _validate(value)
        if errors: raise search.invalid_query(errors)
        conn.execute('UPDATE saved_queries SET name=?,kind=?,query=?,sort=?,display=?,updated_at=?,version=version+1 WHERE id=?',
                     (value['name'].strip(),value['kind'],value['query'],value['sort'],json.dumps(value['display']),db.utcnow(),query_id))
        conn.commit()
    except BaseException:
        conn.rollback(); raise
    return get(conn,access,query_id)


def list_queries(conn, access, *, limit=100, offset=0):
    access.user(conn)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 2**63-1:
        raise search.invalid_query([t("分页参数无效；数量须为 0–10000，偏移须为非负整数")])
    rows = conn.execute('SELECT id FROM saved_queries WHERE owner_id=? ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',
                        (access.user_id,limit+1,offset)).fetchall()
    return {'items':[get(conn,access,row[0]) for row in rows[:limit]],
            'next_offset':offset+limit if len(rows)>limit else None}


def run(conn, access, query_id, *, limit=60, offset=0, now=None):
    value = get(conn,access,query_id)
    if value['errors']: raise search.invalid_query(value['errors'])
    result = search.search_images(conn,access.user_id,access.allowed_libraries(conn),query=value['query'],
                                  sort=value['sort'],limit=limit,offset=offset,now=now)
    if result['errors']: raise search.invalid_query(result['errors'])
    result['rule'] = value
    return result


def export(conn, access, query_id):
    value = get(conn,access,query_id)
    return {'format':1,'type':'picshelf.saved-query',
            'rule':{k:value[k] for k in ('name','kind','query','sort','display')},
            'errors':value['errors']}
