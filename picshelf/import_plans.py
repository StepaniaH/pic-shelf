"""Persisted import previews and item recovery; snapshots never change on execution."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from . import hashing, importer, indexer, libsvc, metadata, source_access
from .access import AccessContext
from .config import Config
from .coordination import library_write_lock
from .errors import ServiceError
from .i18n import t


def _failure(code: str, detail: str, *, retryable=False) -> ServiceError:
    return ServiceError(code, detail, status=409, retryable=retryable,
                        next_step=t("请查看逐项结果；保留源文件，解决冲突后重试或重新预演"))


def _identity(stat) -> dict:
    value = {"device": stat.st_dev, "inode": stat.st_ino}
    if hasattr(stat, "st_birthtime"):
        value["birthtime"] = stat.st_birthtime
    return value


def _source_identity(stat) -> dict:
    return {**_identity(stat), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def create_plan(conn: sqlite3.Connection, access: AccessContext, library_id: int,
                source_dir: str, *, mode=None, rename=True) -> dict:
    access.require_library(conn, library_id, "manage")
    library = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    if library is None:
        raise _failure("library_not_found", t("库不存在"))
    libsvc.require_importable(library)
    mode = library["default_import_mode"] if mode is None else mode
    source_access.require_source(conn, access, source_dir, mode=mode)
    snapshot = importer.plan_import(conn, library, source_dir, mode=mode, rename=rename)
    snapshot["rename"] = rename
    snapshot["root_identity"] = _identity(Path(snapshot["root"]).stat())
    snapshot["source_identity"] = _identity(Path(snapshot["source"]).stat())
    identities = []
    for item in snapshot["items"]:
        identity = None
        if item["action"] in importer.MODES:
            try:
                identity = _source_identity(Path(item["src"]).stat())
            except OSError:
                pass  # An unavailable source will be reported by execution.
        identities.append(identity)
    now = datetime.now(timezone.utc)
    plan_id = uuid.uuid4().hex
    with conn:
        conn.execute("INSERT INTO import_plans(id,library_id,created_by,created_at,expires_at,snapshot) "
                     "VALUES(?,?,?,?,?,?)", (plan_id, library_id, access.user_id, now.isoformat(),
                                             (now + timedelta(hours=24)).isoformat(), json.dumps(snapshot)))
        conn.executemany("INSERT INTO import_plan_items(plan_id,position,destination,state,source_identity,error_code,detail) "
                         "VALUES(?,?,?,?,?,?,?)", [
            (plan_id, n, item["dst"], "skipped" if item["action"] == "skip" else
             "failed" if item["action"] == "error" else "pending",
             json.dumps(identities[n]) if identities[n] else None,
             "preview_error" if item["action"] == "error" else None,
             item.get("reason") if item["action"] == "error" else None)
            for n, item in enumerate(snapshot["items"])])
    return get_plan(conn, access, plan_id)


def _authorized_plan(conn, access, plan_id):
    user = access.user(conn)
    row = conn.execute("SELECT * FROM import_plans WHERE id=?", (plan_id,)).fetchone()
    if row is None or (row["created_by"] != access.user_id and not user["is_admin"] and not access.local_admin):
        raise ServiceError("plan_not_found", t("导入计划不存在或无权访问"), status=404,
                           next_step=t("请使用创建计划的账户，或重新预演"))
    access.require_library(conn, row["library_id"], "manage")
    snapshot = json.loads(row["snapshot"])
    source_access.require_source(conn, access, snapshot["source"], mode=snapshot["mode"])
    return row


def get_plan(conn, access, plan_id, *, offset=0, limit=300) -> dict:
    row = _authorized_plan(conn, access, plan_id)
    snapshot = json.loads(row["snapshot"])
    total = len(snapshot["items"])
    offset, limit = max(0, offset), max(1, min(limit, 1000))
    states = {r["position"]: dict(r) for r in conn.execute(
        "SELECT position,state,actual_action,error_code,detail FROM import_plan_items "
        "WHERE plan_id=? AND position>=? AND position<? ORDER BY position", (plan_id, offset, offset + limit))}
    snapshot["items"] = [{**item, **states[n]} for n, item in
                         enumerate(snapshot["items"][offset:offset + limit], offset)]
    snapshot.update(plan_id=plan_id, state=row["state"], created_at=row["created_at"], expires_at=row["expires_at"],
                    started_at=row["started_at"],
                    items_total=total, items_truncated=offset > 0 or total > offset + limit,
                    offset=offset, limit=limit, next_offset=offset + limit if offset + limit < total else None)
    snapshot.pop("root_identity")
    snapshot.pop("source_identity")
    return snapshot


def cancel_plan(conn, access, plan_id) -> dict:
    row = _authorized_plan(conn, access, plan_id)
    if row["state"] != "succeeded":
        conn.execute("UPDATE import_plans SET cancel_requested=1,state=CASE WHEN state='running' "
                     "THEN 'cancelling' ELSE 'cancelled' END WHERE id=?", (plan_id,))
        conn.commit()
    return get_plan(conn, access, plan_id)


def _record_item(conn, plan_id, position, state, *, identity=None, action=None, error=None):
    conn.execute("UPDATE import_plan_items SET state=?,completed_at=COALESCE(completed_at,?),destination_identity=COALESCE(?,destination_identity), "
                 "actual_action=COALESCE(?,actual_action),error_code=?,detail=? WHERE plan_id=? AND position=?",
                 (state, datetime.now(timezone.utc).isoformat() if state == "succeeded" else None,
                  json.dumps(identity) if identity is not None else None, action,
                  error.code if error else None, error.detail if error else None, plan_id, position))
    conn.commit()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _verified_source(path, expected_identity, expected_hash):
    with path.open("rb") as source:
        if expected_identity is None or _source_identity(os.fstat(source.fileno())) != expected_identity:
            raise _failure("source_changed", t("源文件在预演后发生变化，请重新预演"))
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != expected_hash:
            raise _failure("source_changed", t("源文件在预演后发生变化，请重新预演"))
        source.seek(0)
        yield source


def _copy_or_resume(conn, plan_id, position, item, record, source, dst, action):
    known = json.loads(record["destination_identity"]) if record["destination_identity"] else None
    if known:
        target = dst.open("r+b")
    else:
        _record_item(conn, plan_id, position, "creating", action=action)
        target = dst.open("xb")
    with target:
        if not known:
            # On APFS, moving mtime backwards can also move birth time. Set the
            # intended timestamps before recording the destination's identity.
            original_stat = os.fstat(source.fileno())
            os.utime(target.fileno(), ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            os.fsync(target.fileno())
        identity = _identity(os.fstat(target.fileno()))
        if known and identity != known:
            raise _failure("destination_conflict", t("目标文件已被替换，未改写该文件"))
        if not known:
            _sync_directory(dst.parent)
            _record_item(conn, plan_id, position, "writing", identity=identity, action=action)
        else:
            # Only append when the owned partial file is an exact prefix of the
            # original planned bytes. A later user edit becomes a conflict.
            while block := target.read(1 << 20):
                if block != source.read(len(block)):
                    raise _failure("destination_conflict", t("目标文件已有其他内容，未改写该文件"))
            target.seek(0, os.SEEK_END)
        shutil.copyfileobj(source, target, 1 << 20)
        target.flush()
        os.fsync(target.fileno())
    return identity


def _execute_item(conn, plan_id, position, snapshot, item, record):
    src, dst = Path(item["src"]), Path(item["dst"])
    root, source_root = Path(snapshot["root"]), Path(snapshot["source"])
    if (_identity(root.stat()) != snapshot["root_identity"] or
            _identity(source_root.stat()) != snapshot["source_identity"]):
        raise _failure("plan_scope_changed", t("源目录或目标库已变化，请重新预演"))
    if (not dst.resolve().is_relative_to(root) or dst.resolve() == root or
            not src.resolve().is_relative_to(source_root) or dst.is_symlink()):
        raise _failure("path_changed", t("路径已变化或越出预演范围，请重新预演"))
    # A symlink source may be copied, but moving its referent would violate the
    # displayed source path. Persistent moves require an ordinary source file.
    if item["action"] == "move" and src.is_symlink():
        raise _failure("source_changed", t("移动模式不处理符号链接源，请使用复制或重新选择源文件"))
    known = json.loads(record["destination_identity"]) if record["destination_identity"] else None
    action = record["actual_action"] or item["action"]
    expected_identity = json.loads(record["source_identity"]) if record["source_identity"] else None
    if known:
        if not dst.exists() or _identity(dst.stat()) != known:
            raise _failure("destination_conflict", t("目标文件已被替换，未改写该文件"))
        complete = hashing.sha256_file(dst) == item["sha256"]
    else:
        complete = False
        if dst.exists():
            raise _failure("destination_conflict", t("目标路径已被占用，请重新预演"))
    if complete and item["action"] == "move" and not src.exists():
        _record_item(conn, plan_id, position, "succeeded", action="move")
        return
    with _verified_source(src, expected_identity, item["sha256"]) as source:
        if not complete:
            dst.parent.mkdir(parents=True, exist_ok=True)
            linked = False
            if action in ("hardlink", "move") and not known:
                _record_item(conn, plan_id, position, "creating", action=action)
                try:
                    os.link(src.resolve(), dst)
                    known = _identity(dst.stat())
                    linked = True
                    _sync_directory(dst.parent)
                    _record_item(conn, plan_id, position, "writing", identity=known, action=action)
                except OSError as exc:
                    if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK):
                        raise
                    if action == "hardlink":
                        action = "copy"
            if action != "hardlink" and not linked:
                known = _copy_or_resume(conn, plan_id, position, item, record, source, dst, action)
        if hashing.sha256_file(dst) != item["sha256"]:
            raise _failure("source_changed", t("复制期间文件内容发生变化，源文件已保留"))
        if _source_identity(src.stat()) != expected_identity or hashing.sha256_file(src) != item["sha256"]:
            raise _failure("source_changed", t("复制期间文件内容发生变化，源文件已保留"))
        if action != "hardlink" and _identity(src.stat()) != known:
            shutil.copystat(src, dst)
        with dst.open("rb") as durable:
            os.fsync(durable.fileno())
        _record_item(conn, plan_id, position, "ready", identity=known, action=action)
    if item["action"] == "move":
        # The durable target and journal entry precede removal of the source.
        if _source_identity(src.stat()) != expected_identity or hashing.sha256_file(src) != item["sha256"]:
            raise _failure("source_changed", t("复制期间文件内容发生变化，源文件已保留"))
        if _identity(dst.stat()) != known or hashing.sha256_file(dst) != item["sha256"]:
            raise _failure("destination_conflict", t("目标文件已被替换，未改写该文件"))
        src.unlink()
        _sync_directory(src.parent)
    _record_item(conn, plan_id, position, "succeeded", action=action)


def _result(conn, plan_id, snapshot, scan=None):
    done = {"hardlink": 0, "copy": 0, "move": 0, "skip": 0, "error": 0, "pending": 0, "errors": []}
    for row in conn.execute("SELECT * FROM import_plan_items WHERE plan_id=? ORDER BY position", (plan_id,)):
        item = snapshot["items"][row["position"]]
        if row["state"] == "succeeded": done[row["actual_action"]] += 1
        elif row["state"] == "skipped": done["skip"] += 1
        elif row["state"] in ("failed", "conflict"):
            done["error"] += 1
            done["errors"].append(row["detail"] or item["reason"])
        else: done["pending"] += 1
    return {"plan_id": plan_id, "planned": snapshot["stats"], "done": done, "scan": scan}


def execute_plan(conn, cfg: Config, access: AccessContext, plan_id: str, *, retry=False,
                 check=None, progress=None) -> dict:
    row = _authorized_plan(conn, access, plan_id)
    snapshot = json.loads(row["snapshot"])
    if row["state"] == "succeeded" or (row["state"] == "partial" and not retry):
        return json.loads(row["result"])
    expired = datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc)
    if expired and not (retry and row["started_at"]):
        raise _failure("plan_expired", t("导入计划已过期，请重新预演"))
    root = Path(snapshot["root"])
    library = conn.execute("SELECT * FROM libraries WHERE id=?", (row["library_id"],)).fetchone()
    libsvc.require_importable(library)
    try:
        scope_matches = (Path(library["root_path"]).resolve() == root and _identity(root.stat()) == snapshot["root_identity"]
                         and _identity(Path(snapshot["source"]).stat()) == snapshot["source_identity"])
    except OSError:
        scope_matches = False
    if not scope_matches:
        raise _failure("plan_scope_changed", t("源目录或目标库已变化，请重新预演"))
    with library_write_lock(cfg, root):
        # A concurrent request may have completed while this one was preparing.
        row = _authorized_plan(conn, access, plan_id)
        libsvc.require_importable(conn.execute("SELECT * FROM libraries WHERE id=?", (row["library_id"],)).fetchone())
        if row["state"] == "succeeded" or (row["state"] == "partial" and not retry):
            return json.loads(row["result"])
        if row["cancel_requested"] and not retry:
            result = _result(conn, plan_id, snapshot)
            return {**result, "state": "cancelled"}
        conn.execute("UPDATE import_plans SET state='running',cancel_requested=0,started_at=COALESCE(started_at,?) WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), plan_id))
        conn.commit()
        try:
            for position, item in enumerate(snapshot["items"]):
                if check:
                    check()
                current = _authorized_plan(conn, access, plan_id)
                if current["cancel_requested"]:
                    break
                record = conn.execute("SELECT * FROM import_plan_items WHERE plan_id=? AND position=?",
                                      (plan_id, position)).fetchone()
                if record["state"] in ("succeeded", "skipped") or item["action"] == "error":
                    continue
                try:
                    _execute_item(conn, plan_id, position, snapshot, item, record)
                except (OSError, ServiceError, RuntimeError) as exc:
                    error = exc if isinstance(exc, ServiceError) else _failure(
                        "import_io_error", t("无法完成文件操作，请检查文件和目录权限或可用空间"), retryable=True)
                    _record_item(conn, plan_id, position, "failed" if error.retryable else "conflict", error=error)
                if progress:
                    progress()
            result = _result(conn, plan_id, snapshot)
            # Recheck authority before publishing imported files into the catalog.
            current = _authorized_plan(conn, access, plan_id)
            if sum(result["done"][key] for key in importer.MODES):
                result["scan"] = indexer.scan_library(conn, cfg, row["library_id"], check=check)
                metadata.record_import(conn, row["library_id"], plan_id, snapshot, row["created_by"])
            scan_errors = len(result["scan"]["errors"]) if result["scan"] else 0
            result["scan_error_count"] = scan_errors
            result["resumed_after_expiry"] = expired
            result["state"] = "cancelled" if current["cancel_requested"] else "partial" if result["done"]["error"] or scan_errors else "succeeded"
            conn.execute("UPDATE import_plans SET state=?,result=? WHERE id=?",
                         (result["state"], json.dumps(result), plan_id))
            conn.commit()
            return result
        except BaseException:
            conn.execute("UPDATE import_plans SET state='interrupted' WHERE id=?", (plan_id,))
            conn.commit()
            raise
