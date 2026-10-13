"""Image provenance, personal organization state and tag evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

from . import names
from .db import utcnow
from .errors import ServiceError
from .i18n import t


def _invalid(detail):
    return ServiceError("metadata_invalid", detail, next_step=t("请检查字段长度、来源地址和带时区的时间后重试"))


def timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError) as exc:
        raise _invalid(t("时间必须包含明确时区，例如 2026-10-10T12:00:00+08:00")) from exc


def _text(value, limit):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise _invalid(t("元数据字段过长或包含无效字符"))
    return value.strip() or None


def get(conn, access, image_id: int) -> dict:
    row = access.require_image(conn, image_id, include_missing=True)
    state = conn.execute("SELECT state,updated_at FROM image_workflow WHERE image_id=? AND user_id=?",
                         (image_id, access.user_id)).fetchone()
    library = conn.execute("SELECT name FROM libraries WHERE id=?", (row["library_id"],)).fetchone()
    return {
        "schema": "picshelf.image-metadata/1", "image_id": image_id,
        "identity": {"library": library[0], "rel_path": row["rel_path"], "sha256": row["sha256"]},
        "indexed_at": row["imported_at"], "file_mtime": row["mtime"], "missing": bool(row["missing"]),
        "workflow": dict(state) if state else {"state": "inbox", "updated_at": None},
        "sources": [dict(r) for r in conn.execute(
            "SELECT id,image_id,origin,original_filename,collected_at,created_time,url,note,created_by,created_at "
            "FROM image_sources WHERE image_id=? ORDER BY id", (image_id,))],
        "tag_claims": [dict(r) for r in conn.execute(
            "SELECT c.*,t.name FROM tag_claims c JOIN tags t ON t.id=c.tag_id WHERE image_id=? ORDER BY c.id", (image_id,))],
        "tag_aliases": [dict(r) for r in conn.execute(
            "SELECT a.entity_id,a.name FROM tag_aliases a JOIN image_tags it ON it.tag_id=a.entity_id WHERE it.image_id=? ORDER BY a.id", (image_id,))],
        "artist_aliases": [dict(r) for r in conn.execute(
            "SELECT a.entity_id,a.name FROM artist_aliases a JOIN image_artists ia ON ia.artist_id=a.entity_id WHERE ia.image_id=? ORDER BY a.id", (image_id,))],
    }


def mark(conn, access, image_ids: list[int], state: str) -> dict:
    if state not in ("inbox", "done") or not image_ids or len(image_ids) > 1000:
        raise _invalid(t("整理状态须为 inbox 或 done，每次明确选择 1 至 1000 张图片"))
    if any(type(iid) is not int or iid < 1 for iid in image_ids):
        raise _invalid(t("图片 ID 必须是正整数"))
    ids = sorted(set(image_ids))
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for iid in ids:
            access.require_image(conn, iid, include_missing=True)
        now = utcnow()
        conn.executemany("INSERT INTO image_workflow VALUES(?,?,?,?) ON CONFLICT(image_id,user_id) "
                         "DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at "
                         "WHERE image_workflow.state!=excluded.state", [(iid, access.user_id, state, now) for iid in ids])
    return {"image_ids": ids, "count": len(ids), "state": state}


def add_source(conn, access, image_id: int, *, original_filename=None, collected_at=None,
               created_time=None, url=None, note=None, origin="manual") -> dict:
    access.require_image(conn, image_id, minimum="edit", capability="tag_edit", include_missing=True)
    original_filename, url, note = _text(original_filename, 1000), _text(url, 4096), _text(note, 10000)
    if origin not in ("manual", "imported", "extracted"):
        raise _invalid(t("来源类型无效"))
    if original_filename and ("/" in original_filename or "\\" in original_filename):
        raise _invalid(t("原始文件名只保存名称，不包含目录路径"))
    if url:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise _invalid(t("来源地址须为不含登录凭据的 HTTP 或 HTTPS 地址")) from exc
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise _invalid(t("来源地址须为不含登录凭据的 HTTP 或 HTTPS 地址"))
    collected_at, created_time = timestamp(collected_at), timestamp(created_time)
    if not any((original_filename, collected_at, created_time, url, note)):
        raise _invalid(t("请至少填写一项来源信息"))
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        access.require_image(conn, image_id, minimum="edit", capability="tag_edit", include_missing=True)
        record = conn.execute("INSERT INTO image_sources(image_id,origin,original_filename,collected_at,created_time,url,note,created_by,created_at) "
                              "VALUES(?,?,?,?,?,?,?,?,?)", (image_id, origin, original_filename, collected_at, created_time, url, note, access.user_id, utcnow())).lastrowid
    return next(row for row in get(conn, access, image_id)["sources"] if row["id"] == record)


def record_import(conn, library_id, plan_id, snapshot, created_by):
    """Publish provenance only for completed items whose indexed bytes match."""
    for row in conn.execute("SELECT position,completed_at FROM import_plan_items WHERE plan_id=? AND state='succeeded'", (plan_id,)):
        item = snapshot["items"][row["position"]]
        rel = Path(item["dst"]).relative_to(Path(snapshot["root"])).as_posix()
        image = conn.execute("SELECT id FROM images WHERE library_id=? AND rel_path=? AND sha256=?",
                             (library_id, rel, item["sha256"])).fetchone()
        if image:
            conn.execute("INSERT OR IGNORE INTO image_sources(image_id,origin,original_filename,collected_at,created_by,created_at,event_key) "
                         "VALUES(?,'imported',?,?,?,?,?)", (image[0], Path(item["src"]).name, row["completed_at"], created_by, utcnow(), f"import:{plan_id}:{row['position']}"))
    conn.commit()


def record_indexed_import(conn, library_id, image_id, path, sha):
    """Recover file provenance when a cancelled import is later scanned normally."""
    rows = conn.execute("SELECT p.id,p.created_by,p.snapshot,i.position,i.completed_at FROM import_plan_items i "
                        "JOIN import_plans p ON p.id=i.plan_id WHERE p.library_id=? AND i.destination=? AND i.state='succeeded'",
                        (library_id, str(path))).fetchall()
    for row in rows:
        item = json.loads(row["snapshot"])["items"][row["position"]]
        if item["sha256"] != sha:
            continue
        conn.execute("INSERT OR IGNORE INTO image_sources(image_id,origin,original_filename,collected_at,created_by,created_at,event_key) "
                     "VALUES(?,'imported',?,?,?,?,?)", (image_id, Path(item["src"]).name, row["completed_at"], row["created_by"], utcnow(), f"import:{row['id']}:{row['position']}"))


def record_manual_tag(conn, image_id, tag_id, user_id, when):
    conn.execute("INSERT OR IGNORE INTO tag_claims(image_id,tag_id,origin,confirmed,created_by,created_at,confirmed_by,confirmed_at) "
                 "VALUES(?,?,'manual',1,?,?,?,?)", (image_id, tag_id, user_id, when, user_id, when))


def add_tag_claim(conn, access, image_id, name, *, origin="imported", source_ref="") -> dict:
    from .ops import ensure_tag
    if origin not in ("manual", "imported", "suggested"):
        raise _invalid(t("标签来源类型无效"))
    source_ref = _text(source_ref, 1000) or ""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        access.require_image(conn, image_id, minimum="edit", capability="tag_edit", include_missing=True)
        existing = names.resolve(conn, "tag", name)
        if existing:
            access.require_entity(conn, "tag", existing)
        tag_id = ensure_tag(conn, name)
        when = utcnow()
        confirmed = origin == "manual"
        conn.execute("INSERT OR IGNORE INTO tag_claims(image_id,tag_id,origin,confirmed,source_ref,created_by,created_at,confirmed_by,confirmed_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (image_id, tag_id, origin, int(confirmed), source_ref, access.user_id, when,
                                                  access.user_id if confirmed else None, when if confirmed else None))
        row = conn.execute("SELECT * FROM tag_claims WHERE image_id=? AND tag_id=? AND origin=? AND source_ref=? AND retired_at IS NULL",
                           (image_id, tag_id, origin, source_ref)).fetchone()
        if confirmed:
            conn.execute("INSERT OR IGNORE INTO image_tags VALUES(?,?,?,?)", (image_id, tag_id, access.user_id, when))
    return dict(row)


def confirm_tag_claim(conn, access, claim_id) -> dict:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM tag_claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            raise _invalid(t("标签来源记录不存在"))
        access.require_image(conn, row["image_id"], minimum="edit", capability="tag_edit", include_missing=True)
        if row["retired_at"]:
            raise ServiceError("metadata_conflict", t("该标签已被移除，不能直接重新确认旧记录"), status=409,
                               next_step=t("请检查当前图片标签，必要时重新添加"))
        if not row["confirmed"]:
            now = utcnow()
            conn.execute("UPDATE tag_claims SET confirmed=1,confirmed_by=?,confirmed_at=? WHERE id=?", (access.user_id, now, claim_id))
            conn.execute("INSERT OR IGNORE INTO image_tags VALUES(?,?,?,?)", (row["image_id"], row["tag_id"], access.user_id, now))
    return dict(conn.execute("SELECT * FROM tag_claims WHERE id=?", (claim_id,)).fetchone())
