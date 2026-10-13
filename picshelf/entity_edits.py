"""Reviewable, bounded alias and association-merge plans for catalog managers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from . import auth, names
from .db import utcnow
from .errors import ServiceError
from .i18n import t


def _conflict():
    return ServiceError("metadata_conflict", t("预演后的名称或关联已变化，未执行合并"), status=409,
                        next_step=t("请重新查看当前关联并生成新的预演"))


def _manager(conn, access):
    user = access.user(conn)
    if not (access.local_admin or auth.has_cap(conn, user, "admin")):
        raise ServiceError("permission_denied", t("别名和关联合并需要管理员权限"), status=403,
                           next_step=t("请联系管理员核对共享名称和关联的影响范围"))


def _entity(conn, kind, entity_id):
    if kind not in ("tag", "artist"):
        raise ValueError(t("名称类型必须是 tag 或 artist"))
    row = conn.execute(f"SELECT * FROM {kind}s WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        raise ServiceError("entity_not_found", t("标签或艺术家不存在"), status=404,
                           next_step=t("请刷新目录并重新选择条目"))
    return {**dict(row), "structured_aliases": [dict(r) for r in conn.execute(
        f"SELECT * FROM {kind}_aliases WHERE entity_id=? ORDER BY id", (entity_id,))]}


def _alias_snapshot(conn, kind, entity_id, alias):
    table, column = ("image_tags", "tag_id") if kind == "tag" else ("image_artists", "artist_id")
    entity = _entity(conn, kind, entity_id)
    return {"action": "alias", "kind": kind, "entity_id": entity_id, "alias": alias,
            "entity": entity, "matches": names.find(conn, kind, alias),
            "aliases": [dict(r) for r in conn.execute(f"SELECT * FROM {kind}_aliases WHERE entity_id=? ORDER BY id", (entity_id,))],
            "image_ids": [r[0] for r in conn.execute(f"SELECT image_id FROM {table} WHERE {column}=? ORDER BY image_id", (entity_id,))]}


def _merge_snapshot(conn, kind, source_id, target_id, image_ids):
    source, target = _entity(conn, kind, source_id), _entity(conn, kind, target_id)
    if source_id == target_id or not image_ids or len(image_ids) > 1000:
        raise ServiceError("metadata_invalid", t("合并需要不同的来源和目标，并明确选择 1 至 1000 张图片"),
                           next_step=t("请重新查看当前关联并生成新的预演"))
    if any(type(iid) is not int or iid < 1 for iid in image_ids):
        raise ServiceError("metadata_invalid", t("图片 ID 必须是正整数"),
                           next_step=t("请刷新目录并重新选择条目"))
    ids = sorted(set(image_ids))
    table, column = ("image_tags", "tag_id") if kind == "tag" else ("image_artists", "artist_id")
    marks = ",".join("?" * len(ids))
    links = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {column} IN (?,?) AND image_id IN ({marks}) ORDER BY image_id,{column}",
                                          [source_id, target_id, *ids])]
    if {r["image_id"] for r in links if r[column] == source_id} != set(ids):
        raise _conflict()
    claims = [dict(r) for r in conn.execute(f"SELECT * FROM tag_claims WHERE tag_id IN (?,?) AND image_id IN ({marks}) ORDER BY id",
                                           [source_id, target_id, *ids])] if kind == "tag" else []
    return {"action": "merge", "kind": kind, "source_id": source_id, "target_id": target_id,
            "image_ids": ids, "source": source, "target": target, "links": links, "claims": claims}


def _save(conn, access, snapshot):
    plan_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    with conn:
        conn.execute("INSERT INTO entity_edit_plans(id,created_by,created_at,expires_at,snapshot) VALUES(?,?,?,?,?)",
                     (plan_id, access.user_id, now.isoformat(), (now + timedelta(hours=24)).isoformat(), json.dumps(snapshot)))
    return {**snapshot, "id": plan_id, "count": len(snapshot["image_ids"]), "expires_at": (now + timedelta(hours=24)).isoformat()}


def preview_alias(conn, access, kind, entity_id, alias):
    _manager(conn, access)
    alias = names.display(alias)
    return _save(conn, access, _alias_snapshot(conn, kind, entity_id, alias))


def preview_merge(conn, access, kind, source_id, target_id, image_ids):
    _manager(conn, access)
    snapshot = _merge_snapshot(conn, kind, source_id, target_id, image_ids)
    for iid in snapshot["image_ids"]:
        access.require_image(conn, iid, minimum="edit", capability="tag_edit", include_missing=True)
    return _save(conn, access, snapshot)


def get_plan(conn, access, plan_id):
    _manager(conn, access)
    row = conn.execute("SELECT * FROM entity_edit_plans WHERE id=? AND created_by=?", (plan_id, access.user_id)).fetchone()
    if row is None:
        raise ServiceError("metadata_plan_not_found", t("元数据预演不存在或无权访问"), status=404,
                           next_step=t("请使用创建预演的账户重新检查"))
    return row


def execute(conn, access, plan_id):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_plan(conn, access, plan_id)
        snapshot = json.loads(row["snapshot"])
        for iid in snapshot["image_ids"]:
            access.require_image(conn, iid, minimum="edit", capability="tag_edit", include_missing=True)
        if row["result"]:
            return json.loads(row["result"])
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            raise ServiceError("metadata_plan_expired", t("元数据预演已过期"), status=409,
                               next_step=t("请重新查看当前关联并生成新的预演"))
        kind = snapshot["kind"]
        if snapshot["action"] == "alias":
            current = _alias_snapshot(conn, kind, snapshot["entity_id"], snapshot["alias"])
        else:
            current = _merge_snapshot(conn, kind, snapshot["source_id"], snapshot["target_id"], snapshot["image_ids"])
        if current != snapshot:
            raise _conflict()
        now = utcnow()
        if snapshot["action"] == "alias":
            # A conflicting alias would make future matching ambiguous.
            if set(snapshot["matches"]) - {snapshot["entity_id"]}:
                raise ServiceError("name_ambiguous", t("该别名已匹配其他条目，未添加"), status=409,
                                   next_step=t("请选择独立的别名，或先核对需要合并的关联"))
            conn.execute(f"INSERT OR IGNORE INTO {kind}_aliases(entity_id,name,match_key,created_by,created_at) VALUES(?,?,?,?,?)",
                         (snapshot["entity_id"], snapshot["alias"], names.match_key(snapshot["alias"]), access.user_id, now))
        else:
            source, target = snapshot["source_id"], snapshot["target_id"]
            table, column = ("image_tags", "tag_id") if kind == "tag" else ("image_artists", "artist_id")
            for iid in snapshot["image_ids"]:
                if kind == "tag":
                    conn.execute("INSERT OR IGNORE INTO image_tags SELECT image_id,?,added_by,added_at FROM image_tags WHERE image_id=? AND tag_id=?", (target, iid, source))
                    conn.execute("INSERT OR IGNORE INTO tag_claims(image_id,tag_id,origin,confirmed,source_ref,created_by,created_at,confirmed_by,confirmed_at) "
                                 "SELECT image_id,?,origin,confirmed,source_ref,created_by,created_at,confirmed_by,confirmed_at FROM tag_claims "
                                 "WHERE image_id=? AND tag_id=? AND retired_at IS NULL", (target, iid, source))
                    conn.execute("UPDATE tag_claims SET retired_at=? WHERE image_id=? AND tag_id=? AND retired_at IS NULL", (now, iid, source))
                else:
                    conn.execute("INSERT OR IGNORE INTO image_artists VALUES(?,?)", (iid, target))
                conn.execute(f"DELETE FROM {table} WHERE image_id=? AND {column}=?", (iid, source))
        result = {"plan_id": plan_id, "action": snapshot["action"], "kind": kind,
                  "image_ids": snapshot["image_ids"], "count": len(snapshot["image_ids"]), "state": "succeeded"}
        conn.execute("UPDATE entity_edit_plans SET result=? WHERE id=?", (json.dumps(result), plan_id))
    return result
