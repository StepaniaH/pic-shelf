"""Personal reusable playback configuration, separate from active sessions."""
import json

from . import db, play
from .errors import ServiceError
from .i18n import t


def _name(name):
    if not isinstance(name,str) or not 1<=len(name.strip())<=200:
        raise ServiceError('invalid_play_rule',t('规则名称须为 1–200 个字符'),next_step=t('请输入有效名称后重试'))
    return name.strip()


def get(conn,access,rule_id):
    access.user(conn)
    row=conn.execute('SELECT * FROM play_rules WHERE id=? AND owner_id=?',(rule_id,access.user_id)).fetchone()
    if row is None:
        raise ServiceError('play_rule_not_found',t('播放规则不存在'),status=404,next_step=t('请返回自己的播放规则列表后重试'))
    value=dict(row)
    try:
        value['spec']=json.loads(value['spec']);play.validate_spec(value['spec']);value['errors']=[]
    except (ValueError,TypeError,ServiceError):
        value['errors']=[t('播放配置无效，请检查模式、数量、间隔和显示选项')]
    return value


def create(conn,access,*,name,spec):
    name=_name(name);spec=play.validate_spec(spec)
    with play.transaction(conn):
        play.check_source(conn,access,spec)
        rule_id=conn.execute('INSERT INTO play_rules(owner_id,name,spec,created_at,updated_at) VALUES(?,?,?,?,?)',
                             (access.user_id,name,json.dumps(spec,ensure_ascii=False),db.utcnow(),db.utcnow())).lastrowid
    return get(conn,access,rule_id)


def update(conn,access,rule_id,*,version,name,spec):
    name=_name(name);spec=play.validate_spec(spec)
    with play.transaction(conn):
        value=get(conn,access,rule_id)
        if type(version) is not int or value['version']!=version:
            raise ServiceError('play_rule_changed',t('播放规则已变化，请重新读取后编辑'),status=409,
                               next_step=t('请保留输入并重新读取最新播放规则'))
        play.check_source(conn,access,spec)
        conn.execute('UPDATE play_rules SET name=?,spec=?,version=version+1,updated_at=? WHERE id=?',
                     (name,json.dumps(spec,ensure_ascii=False),db.utcnow(),rule_id))
    return get(conn,access,rule_id)


def list_rules(conn,access,*,offset=0,limit=100):
    access.user(conn)
    if type(offset) is not int or not 0<=offset<=2**63-1 or type(limit) is not int or not 1<=limit<=100:raise play._invalid()
    rows=conn.execute('SELECT id FROM play_rules WHERE owner_id=? ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',
                      (access.user_id,limit+1,offset)).fetchall()
    return {'items':[get(conn,access,r[0]) for r in rows[:limit]],'next_offset':offset+limit if len(rows)>limit else None}


def start(conn,access,rule_id):
    with play.transaction(conn):
        rule=get(conn,access,rule_id)
        return play.create_for_user(conn,access,rule['spec'],rule_id=rule_id)


def export(conn,access,rule_id):
    rule=get(conn,access,rule_id)
    return {'format':1,'type':'picshelf.play-rule','rule':{k:rule[k] for k in ('name','spec')},'errors':rule['errors']}
