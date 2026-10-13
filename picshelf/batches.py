"""Fixed metadata selections, chunked transactions and conflict-aware undo."""
from dataclasses import asdict
from contextlib import closing
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import uuid

from . import auth,catalog,db,metadata,names,ops,search
from .access import AccessContext
from .config import Config
from .coordination import operation_lock,worker_slot
from .errors import ServiceError
from .i18n import t

CHUNK_SIZE = 100
MAX_ITEMS = 10000
KINDS = {"tags_add","tags_remove","artist_add","artist_remove","album_add","album_remove","rating","favorite","workflow"}


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def _error(code,detail,status=409):
    return ServiceError(code,t(detail),status=status,
                        next_step=t("请查看批次明细，核对当前权限和后续修改后重新预演"))


def _config(conn):
    path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    return Config(Path(path).parent)


def _action(conn,access,value):
    if not isinstance(value,dict) or value.get("kind") not in KINDS:
        raise _error("batch_invalid","批量操作类型或参数无效",400)
    kind = value["kind"]
    key = "names" if kind.startswith("tags_") else "artist_id" if kind.startswith("artist_") else "album_id" if kind.startswith("album_") else "value"
    if set(value) != {"kind",key}:
        raise _error("batch_invalid","批量操作类型或参数无效",400)
    target = value[key]
    if key == "names":
        if not isinstance(target,list) or not 1 <= len(target) <= 100 or any(not isinstance(n,str) for n in target):
            raise _error("batch_invalid","批量操作类型或参数无效",400)
        target = list(dict.fromkeys(names.display(n) for n in target))
        for name in target:
            identity = names.resolve(conn,"tag",name)
            if identity:
                access.require_entity(conn,"tag",identity)
    elif key == "artist_id":
        if type(target) is not int or target < 1:
            raise _error("batch_invalid","批量操作类型或参数无效",400)
        access.require_entity(conn,"artist",target)
    elif key == "album_id":
        if type(target) is not int or target < 1:
            raise _error("batch_invalid","批量操作类型或参数无效",400)
        album = access.require_album(conn,target,"edit")
        if album["kind"] != "manual":
            raise _error("batch_invalid","批量关联只能使用手工专辑",400)
    elif kind == "rating":
        if target is not None and (type(target) is not int or not 0 <= target <= 10):
            raise _error("batch_invalid","评分范围 0–10",400)
    elif kind == "favorite":
        if type(target) is not bool:
            raise _error("batch_invalid","批量操作类型或参数无效",400)
    elif target not in ("inbox","done"):
        raise _error("batch_invalid","批量操作类型或参数无效",400)
    return {"kind":kind,key:target}


def _permission(conn,access,iid,action):
    kind = action["kind"]
    personal = kind in ("rating","favorite","workflow")
    access.require_image(conn,iid,"view" if personal else "edit",
                         "rate" if kind in ("rating","favorite") else None if kind == "workflow" else "tag_edit",
                         include_missing=True)


def _field(action,owner):
    kind = action["kind"]
    if kind in ("rating","favorite","workflow"):
        return f"{kind}:{owner}"
    if kind.startswith("tags_"):return "tags"
    if kind.startswith("artist_"):return "artists"
    return f"album:{action['album_id']}"


def _version(conn,iid,action,owner):
    row = conn.execute("SELECT version FROM metadata_versions WHERE image_id=? AND field_key=?",(iid,_field(action,owner))).fetchone()
    return row[0] if row else 0


def _rows(conn,table,where,params,order):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order}",params)]


def _value(conn,iid,action,owner):
    kind = action["kind"]
    if kind in ("rating","favorite","workflow"):
        table = {"rating":"ratings","favorite":"favorites","workflow":"image_workflow"}[kind]
        return _rows(conn,table,"image_id=? AND user_id=?",(iid,owner),"image_id")
    if kind.startswith("tags_"):
        return {"links":_rows(conn,"image_tags","image_id=?",(iid,),"tag_id"),
                "claims":_rows(conn,"tag_claims","image_id=? AND retired_at IS NULL",(iid,),"id")}
    if kind.startswith("artist_"):
        return _rows(conn,"image_artists","image_id=?",(iid,),"artist_id")
    return _rows(conn,"album_images","image_id=? AND album_id=?",(iid,action["album_id"]),"image_id")


def _insert(conn,table,row):
    keys = list(row)
    conn.execute(f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",[row[key] for key in keys])


def _resolve(conn,access,action):
    if not action["kind"].startswith("tags_"):
        return action
    ids = []
    for name in action["names"]:
        identity = names.resolve(conn,"tag",name)
        if identity:
            access.require_entity(conn,"tag",identity)
        elif action["kind"] == "tags_add":
            identity = ops.ensure_tag(conn,name)
        if identity is not None:ids.append(identity)
    return {**action,"tag_ids":ids,"targets":_targets(conn,action)}


def _targets(conn,action):
    kind = action["kind"]
    if kind.startswith("tags_"):
        return [{"name":name,"id":names.resolve(conn,"tag",name)} for name in action["names"]]
    if kind.startswith("artist_"):
        row = conn.execute("SELECT id,name FROM artists WHERE id=?",(action["artist_id"],)).fetchone()
        return dict(row) if row else None
    if kind.startswith("album_"):
        row = conn.execute("SELECT id,name,kind,library_id FROM albums WHERE id=?",(action["album_id"],)).fetchone()
        return dict(row) if row else None
    return None


def _write(conn,iid,action,owner):
    kind,when = action["kind"],db.utcnow()
    current = _value(conn,iid,action,owner)
    if kind in ("rating","favorite","workflow"):
        value = action["value"]
        present = current[0] if current else None
        actual = (present["value"] if present else None) if kind == "rating" else bool(present) if kind == "favorite" else present["state"] if present else "inbox"
        if actual == value:return
        table = {"rating":"ratings","favorite":"favorites","workflow":"image_workflow"}[kind]
        conn.execute(f"DELETE FROM {table} WHERE image_id=? AND user_id=?",(iid,owner))
        if kind == "rating" and value is not None:
            conn.execute("INSERT INTO ratings VALUES(?,?,?,?)",(iid,owner,value,when))
        elif kind == "favorite" and value:
            conn.execute("INSERT INTO favorites VALUES(?,?,?)",(iid,owner,when))
        elif kind == "workflow":
            conn.execute("INSERT INTO image_workflow VALUES(?,?,?,?)",(iid,owner,value,when))
    elif kind.startswith("tags_"):
        existing = {row["tag_id"] for row in current["links"]}
        for identity in action["tag_ids"]:
            if kind == "tags_add" and identity not in existing:
                conn.execute("INSERT INTO image_tags VALUES(?,?,?,?)",(iid,identity,owner,when))
                metadata.record_manual_tag(conn,iid,identity,owner,when)
            elif kind == "tags_remove" and identity in existing:
                conn.execute("DELETE FROM image_tags WHERE image_id=? AND tag_id=?",(iid,identity))
                conn.execute("UPDATE tag_claims SET retired_at=? WHERE image_id=? AND tag_id=? AND retired_at IS NULL",(when,iid,identity))
    elif kind.startswith("artist_"):
        if kind == "artist_add":conn.execute("INSERT OR IGNORE INTO image_artists VALUES(?,?)",(iid,action["artist_id"]))
        else:conn.execute("DELETE FROM image_artists WHERE image_id=? AND artist_id=?",(iid,action["artist_id"]))
    elif kind == "album_add":
        conn.execute("INSERT OR IGNORE INTO album_images(album_id,image_id,position,added_at) VALUES(?,?,0,?)",(action["album_id"],iid,when))
    else:conn.execute("DELETE FROM album_images WHERE image_id=? AND album_id=?",(iid,action["album_id"]))


def _restore(conn,iid,action,owner,before):
    kind = action["kind"]
    if kind.startswith("tags_"):
        conn.execute("DELETE FROM image_tags WHERE image_id=?",(iid,))
        conn.execute("UPDATE tag_claims SET retired_at=? WHERE image_id=? AND retired_at IS NULL",(db.utcnow(),iid))
        for row in before["links"]:_insert(conn,"image_tags",row)
        for row in before["claims"]:
            columns = [key for key in row if key != "id"]
            conn.execute(f"UPDATE tag_claims SET {','.join(key+'=?' for key in columns)} WHERE id=?",[*[row[key] for key in columns],row["id"]])
        return
    if kind.startswith("artist_"):
        table,where,params = "image_artists","image_id=?",(iid,)
    elif kind.startswith("album_"):
        table,where,params = "album_images","image_id=? AND album_id=?",(iid,action["album_id"])
    else:
        table = {"rating":"ratings","favorite":"favorites","workflow":"image_workflow"}[kind]
        where,params = "image_id=? AND user_id=?",(iid,owner)
    conn.execute(f"DELETE FROM {table} WHERE {where}",params)
    for row in before:_insert(conn,table,row)


def _authorized(conn,access,batch_id,*,editing=False):
    user = access.user(conn)
    row = conn.execute("SELECT * FROM metadata_batches WHERE id=?",(batch_id,)).fetchone()
    if row is None or (row["created_by"] != access.user_id and not access.local_admin and not auth.has_cap(conn,user,"admin")):
        raise _error("batch_not_found","批次不存在或无权访问",404)
    action = json.loads(row["action"])
    if editing:_action(conn,access,action)
    kind = action["kind"]
    personal = kind in ("rating","favorite","workflow")
    if editing and kind != "workflow":
        access.require_capability(conn,"rate" if personal else "tag_edit")
    for library in conn.execute("SELECT DISTINCT i.library_id FROM metadata_batch_items b JOIN images i ON i.id=b.image_id WHERE b.batch_id=?",(batch_id,)):
        access.require_library(conn,library[0],"edit" if editing and not personal else "view")
    return row


def _reconcile(conn,batch_id):
    row = conn.execute("SELECT state FROM metadata_batches WHERE id=?",(batch_id,)).fetchone()
    if row and row[0] in ("running","cancelling","undoing"):
        try:
            with operation_lock(_config(conn),"batch:"+batch_id,reentrant=False):
                with conn:
                    conn.execute("UPDATE metadata_batches SET state='interrupted',updated_at=? WHERE id=?",(db.utcnow(),batch_id))
        except ServiceError as exc:
            if exc.code != "operation_busy":raise


def get(conn,access,batch_id,*,offset=0,limit=200):
    _authorized(conn,access,batch_id)
    _reconcile(conn,batch_id)
    row = _authorized(conn,access,batch_id)
    counts = {state:0 for state in ("pending","succeeded","unchanged","conflict","undone","undo_conflict")}
    counts.update({r[0]:r[1] for r in conn.execute("SELECT state,COUNT(*) FROM metadata_batch_items WHERE batch_id=? GROUP BY state",(batch_id,))})
    offset,limit = max(0,offset),min(1000,max(1,limit))
    items = [{**dict(item),"before_value":json.loads(item["before_value"]),
              "after_value":json.loads(item["after_value"]) if item["after_value"] else None} for item in
             conn.execute("SELECT b.*,i.filename,i.rel_path,l.name AS library_name FROM metadata_batch_items b "
                          "JOIN images i ON i.id=b.image_id JOIN libraries l ON l.id=i.library_id "
                          "WHERE b.batch_id=? ORDER BY b.position LIMIT ? OFFSET ?",(batch_id,limit,offset))]
    return {"id":batch_id,"created_by":row["created_by"],"created_at":row["created_at"],"expires_at":row["expires_at"],"state":row["state"],
            "phase":row["phase"],"error_code":row["error_code"],"action":json.loads(row["action"]),"selection":json.loads(row["selection"]),
            "target_snapshot":json.loads(row["target_snapshot"]),
            "total":sum(counts.values()),"counts":counts,"items":items,"offset":offset,"limit":limit}


def list_batches(conn,access,*,offset=0,limit=50):
    user = access.user(conn)
    broad = access.local_admin or auth.has_cap(conn,user,"admin")
    limit = min(100,max(1,limit))
    rows = conn.execute("SELECT id FROM metadata_batches WHERE (? OR created_by=?) ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (int(broad),access.user_id,limit+1,max(0,offset))).fetchall()
    items = []
    for row in rows[:limit]:
        try:
            value = get(conn,access,row[0],limit=1)
            value.pop("items")
            items.append(value)
        except ServiceError as exc:
            if exc.status not in (403,404):raise
    return {"items":items,"offset":offset,"next_offset":offset+limit if len(rows)>limit else None}


def preview(conn,access,action,*,image_ids=None,query=None,request_key=None):
    request = _json({"action":action,"image_ids":image_ids,"query":query})
    request_key = request_key or uuid.uuid4().hex
    if not isinstance(request_key,str) or not 1 <= len(request_key) <= 128:
        raise _error("batch_invalid","批量操作类型或参数无效",400)
    with operation_lock(_config(conn),"catalog",shared=True),conn:
        conn.execute("BEGIN IMMEDIATE")
        access.user(conn)
        existing = conn.execute("SELECT * FROM metadata_batches WHERE created_by=? AND request_key=?",(access.user_id,request_key)).fetchone()
        if existing:
            if existing["request"] != request:raise _error("batch_request_conflict","同一请求标识不能用于不同批次")
            batch_id = existing["id"]
        else:
            action = _action(conn,access,action)
            if (image_ids is None) == (query is None):raise _error("batch_invalid","请选择明确图片或固定查询范围",400)
            if query is not None:
                try:
                    if (not isinstance(query,dict) or any(key not in catalog.ImageQuery.__dataclass_fields__ or key in ("limit","offset") for key in query)
                            or any(key in query and query[key] is not None and (type(query[key]) is not int or query[key]<1) for key in ("library_id","album_id","artist_id"))
                            or ("favorites" in query and type(query["favorites"]) is not bool)
                            or not isinstance(query.get("query",""),str) or len(query.get("query",""))>search.MAX_QUERY_LENGTH
                            or query.get("sort","newest") not in search.SORTS
                            or (query.get("tag") is not None and (not isinstance(query["tag"],str) or len(query["tag"])>300))
                            or (query.get("rating_min") is not None and (type(query["rating_min"]) is not int or not 0<=query["rating_min"]<=10))):
                        raise ValueError
                    spec = catalog.ImageQuery(**query)
                    spec = catalog.ImageQuery(**{**asdict(spec),"limit":MAX_ITEMS,"offset":0})
                    result = catalog.query_images(conn,access,spec)
                except (TypeError,ValueError) as exc:raise _error("batch_invalid","查询范围无效，请先修正搜索",400) from exc
                if result["errors"]:raise _error("batch_invalid","查询范围无效，请先修正搜索",400)
                if result["total"] > MAX_ITEMS:raise _error("batch_too_large","每批最多处理 10000 张图片，请缩小范围",400)
                image_ids = [item["id"] for item in result["items"]]
                selection = {"mode":"query","query":query}
            else:selection = {"mode":"explicit"}
            if (not isinstance(image_ids,list) or not 1 <= len(image_ids) <= MAX_ITEMS or
                    any(type(iid) is not int or iid < 1 for iid in image_ids)):
                raise _error("batch_invalid","每批需明确选择 1 至 10000 张图片",400)
            ids = list(dict.fromkeys(image_ids))
            for iid in ids:_permission(conn,access,iid,action)
            batch_id,now = uuid.uuid4().hex,datetime.now(timezone.utc)
            conn.execute("INSERT INTO metadata_batches(id,created_by,created_at,expires_at,request_key,request,action,selection,target_snapshot,local_admin,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (batch_id,access.user_id,now.isoformat(),(now+timedelta(hours=24)).isoformat(),request_key,request,_json(action),_json(selection),_json(_targets(conn,action)),int(access.local_admin),now.isoformat()))
            for position,iid in enumerate(ids):
                conn.execute("INSERT INTO metadata_batch_items(batch_id,position,image_id,before_value,before_version) VALUES(?,?,?,?,?)",
                             (batch_id,position,iid,_json(_value(conn,iid,action,access.user_id)),_version(conn,iid,action,access.user_id)))
    return get(conn,access,batch_id)


def _unchanged(conn,item,action,owner,side):
    return (_version(conn,item["image_id"],action,owner) == item[side+"_version"] and
            _json(_value(conn,item["image_id"],action,owner)) == item[side+"_value"])


def cancel(conn,access,batch_id):
    row = _authorized(conn,access,batch_id)
    if row["state"] not in ("succeeded","undone","undo_conflict"):
        with conn:
            conn.execute("UPDATE metadata_batches SET cancel_requested=1,state=CASE WHEN state IN ('running','undoing') THEN 'cancelling' ELSE 'cancelled' END,updated_at=? WHERE id=?",(db.utcnow(),batch_id))
    return get(conn,access,batch_id)


def execute(conn,cfg,access,batch_id,*,retry=False,progress=None,should_stop=lambda:False):
    _authorized(conn,access,batch_id,editing=True)
    with operation_lock(cfg,"catalog",shared=True),operation_lock(cfg,"batch:"+batch_id):
        row = _authorized(conn,access,batch_id,editing=True)
        if row["phase"] == "undo":return get(conn,access,batch_id)
        if row["state"] in ("succeeded","undone","undo_conflict") or (row["cancel_requested"] and not retry):
            return get(conn,access,batch_id)
        action,owner = json.loads(row["action"]),row["created_by"]
        pending = conn.execute("SELECT * FROM metadata_batch_items WHERE batch_id=? AND state='pending' ORDER BY position",(batch_id,)).fetchall()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if row["executed_by"] is None and datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                raise _error("batch_expired","批次预演已过期，请重新预演")
            # All known input/scope conflicts fail before any image is changed.
            for item in pending:
                if not _unchanged(conn,item,action,owner,"before"):
                    raise _error("batch_changed","预演后的元数据已变化，批次未开始")
            expected_targets = json.loads(row["resolved"]).get("targets") if row["resolved"] and action["kind"].startswith("tags_") else json.loads(row["target_snapshot"])
            if _targets(conn,action) != expected_targets:
                raise _error("batch_target_changed","批次的标签、艺术家或专辑目标已变化")
            conn.execute("UPDATE metadata_batches SET state='running',cancel_requested=0,error_code=NULL,executed_by=?,updated_at=? WHERE id=?",(access.user_id,db.utcnow(),batch_id))
        try:
            for start in range(0,len(pending),CHUNK_SIZE):
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = _authorized(conn,access,batch_id,editing=True)
                    if row["cancel_requested"] or should_stop():
                        conn.execute("UPDATE metadata_batches SET state=?,updated_at=? WHERE id=?",("cancelled" if row["cancel_requested"] else "interrupted",db.utcnow(),batch_id))
                        break
                    expected_targets = json.loads(row["resolved"]).get("targets") if row["resolved"] and action["kind"].startswith("tags_") else json.loads(row["target_snapshot"])
                    if _targets(conn,action) != expected_targets:
                        raise _error("batch_target_changed","批次的标签、艺术家或专辑目标已变化")
                    if len(pending) <= CHUNK_SIZE and any(not _unchanged(conn,item,action,owner,"before") for item in pending):
                        raise _error("batch_changed","预演后的元数据已变化，批次未开始")
                    resolved = json.loads(row["resolved"]) if row["resolved"] else _resolve(conn,access,action)
                    conn.execute("UPDATE metadata_batches SET resolved=? WHERE id=?",(_json(resolved),batch_id))
                    for item in pending[start:start+CHUNK_SIZE]:
                        if not _unchanged(conn,item,action,owner,"before"):
                            conn.execute("UPDATE metadata_batch_items SET state='conflict',error_code='batch_changed' WHERE batch_id=? AND position=?",(batch_id,item["position"]))
                            continue
                        _write(conn,item["image_id"],resolved,owner)
                        after = _json(_value(conn,item["image_id"],action,owner))
                        version = _version(conn,item["image_id"],action,owner)
                        state = "unchanged" if after == item["before_value"] else "succeeded"
                        conn.execute("UPDATE metadata_batch_items SET state=?,after_value=?,after_version=? WHERE batch_id=? AND position=?",(state,after,version,batch_id,item["position"]))
                if progress:progress(get(conn,access,batch_id))
            else:
                with conn:
                    conflict = conn.execute("SELECT 1 FROM metadata_batch_items WHERE batch_id=? AND state='conflict'",(batch_id,)).fetchone()
                    conn.execute("UPDATE metadata_batches SET state=?,updated_at=? WHERE id=?",("partial" if conflict else "succeeded",db.utcnow(),batch_id))
        except BaseException as exc:
            with conn:
                conn.execute("UPDATE metadata_batches SET state='interrupted',error_code=?,updated_at=? WHERE id=?",(getattr(exc,"code","batch_interrupted"),db.utcnow(),batch_id))
            raise
    return get(conn,access,batch_id)


def undo(conn,cfg,access,batch_id,*,progress=None):
    _authorized(conn,access,batch_id,editing=True)
    with operation_lock(cfg,"catalog",shared=True),operation_lock(cfg,"batch:"+batch_id):
        row = _authorized(conn,access,batch_id,editing=True)
        if row["state"] in ("preview","queued"):
            raise _error("batch_not_applied","批次尚未执行，不能撤销")
        with conn:
            conn.execute("UPDATE metadata_batches SET state='undoing',phase='undo',cancel_requested=0,updated_at=? WHERE id=?",(db.utcnow(),batch_id))
        action,owner = json.loads(row["action"]),row["created_by"]
        items = conn.execute("SELECT * FROM metadata_batch_items WHERE batch_id=? AND state='succeeded' ORDER BY position",(batch_id,)).fetchall()
        for start in range(0,len(items),CHUNK_SIZE):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = _authorized(conn,access,batch_id,editing=True)
                if row["cancel_requested"]:
                    conn.execute("UPDATE metadata_batches SET state='cancelled',updated_at=? WHERE id=?",(db.utcnow(),batch_id))
                    break
                for item in items[start:start+CHUNK_SIZE]:
                    state = "undo_conflict"
                    if _unchanged(conn,item,action,owner,"after"):
                        _restore(conn,item["image_id"],action,owner,json.loads(item["before_value"]))
                        state = "undone"
                    conn.execute("UPDATE metadata_batch_items SET state=?,error_code=? WHERE batch_id=? AND position=?",(state,"batch_changed" if state == "undo_conflict" else None,batch_id,item["position"]))
            if progress:progress(get(conn,access,batch_id))
        else:
            with conn:
                conflict = conn.execute("SELECT 1 FROM metadata_batch_items WHERE batch_id=? AND state='undo_conflict'",(batch_id,)).fetchone()
                conn.execute("UPDATE metadata_batches SET state=?,cancel_requested=1,updated_at=? WHERE id=?",("undo_conflict" if conflict else "undone",db.utcnow(),batch_id))
    return get(conn,access,batch_id)


def queue(conn,access,batch_id,*,retry=False):
    row = _authorized(conn,access,batch_id,editing=True)
    if row["phase"] == "undo":return get(conn,access,batch_id)
    if row["state"] in ("succeeded","undone","undo_conflict","running","cancelling") or (row["cancel_requested"] and not retry):
        return get(conn,access,batch_id)
    with conn:
        conn.execute("UPDATE metadata_batches SET state='queued',cancel_requested=0,updated_at=? WHERE id=?",(db.utcnow(),batch_id))
    return get(conn,access,batch_id)


def run_next(cfg,*,should_stop=lambda:False):
    with closing(db.open_db(cfg.db_path)) as conn:
        candidates = conn.execute("SELECT id,created_by,local_admin FROM metadata_batches WHERE state='queued' ORDER BY created_at LIMIT 20").fetchall()
        for row in candidates:
            try:
                with worker_slot(cfg),operation_lock(cfg,"batch:"+row["id"]):
                    current = conn.execute("SELECT state FROM metadata_batches WHERE id=?",(row["id"],)).fetchone()
                    if current[0] != "queued":continue
                    try:execute(conn,cfg,AccessContext(row["created_by"],local_admin=bool(row["local_admin"])),row["id"],should_stop=should_stop)
                    except (ServiceError,ValueError,OSError) as exc:
                        with conn:
                            conn.execute("UPDATE metadata_batches SET state='interrupted',error_code=?,updated_at=? WHERE id=?",(getattr(exc,"code","batch_interrupted"),db.utcnow(),row["id"]))
                    return True
            except ServiceError as exc:
                if exc.code not in ("operation_busy","workers_busy"):raise
    return False
