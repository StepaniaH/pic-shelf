"""Durable local jobs, current authorization, and bounded execution."""
from __future__ import annotations

from contextlib import ExitStack
from functools import wraps
import json
from pathlib import Path
import sqlite3
import time
import uuid

from . import auth, db, import_plans, indexer
from .access import AccessContext
from .coordination import library_write_lock, operation_lock, worker_slot
from .errors import ServiceError
from .i18n import current_lang, pop_lang, push_lang, t

ACTIVE = {"queued", "running", "cancelling"}
TERMINAL = {"cancelled", "succeeded", "partial", "failed", "interrupted"}


class JobCancelled(Exception):
    pass


class WorkerStopped(Exception):
    pass


def _error(code, message, *, status=409, retryable=False):
    return ServiceError(code, t(message), status=status, retryable=retryable,
                        next_step=t("请查看任务与逐项结果，解决问题后重试"))


def _authorized(conn, access, job_id):
    user = access.user(conn)
    row = conn.execute("SELECT jobs.*,libraries.name AS library_name FROM jobs JOIN libraries ON libraries.id=jobs.library_id "
                       "WHERE jobs.id=?", (job_id,)).fetchone()
    if row is None or (row["created_by"] != access.user_id and not user["is_admin"] and not access.local_admin):
        raise _error("job_not_found", "任务不存在或无权访问", status=404)
    access.require_library(conn, row["library_id"], "manage")
    return row


def _catalog_write(function):
    @wraps(function)
    def guarded(conn, *args, **kwargs):
        with operation_lock(_config(conn), "catalog", shared=True):
            return function(conn, *args, **kwargs)
    return guarded


@_catalog_write
def submit(conn, access, kind, library_id, *, request_key=None, plan_id=None, full=False, outdated_only=False) -> dict:
    access.require_library(conn, library_id, "manage")
    if kind not in ("scan", "rebuild", "import"):
        raise _error("invalid_job", "不支持此任务类型", status=400)
    request_key = request_key or uuid.uuid4().hex
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
        raise _error("invalid_request_key", "请求标识长度必须为 1–128", status=400)
    if kind == "import":
        plan = import_plans.get_plan(conn, access, plan_id)
        if plan["library"]["id"] != library_id:
            raise _error("plan_scope_changed", "源目录或目标库已变化，请重新预演")
    signature = {"plan_id": plan_id if kind == "import" else None, "full": bool(full or kind == "rebuild")}
    if outdated_only:
        if kind != "rebuild":
            raise _error("invalid_job", "仅重建任务支持按算法版本筛选", status=400)
        signature["outdated_only"] = True
    existing = conn.execute("SELECT * FROM jobs WHERE created_by=? AND request_key=?",
                            (access.user_id, request_key)).fetchone()
    if existing:
        saved = json.loads(existing["params"])
        if existing["kind"] != kind or existing["library_id"] != library_id or saved["input"] != signature:
            raise _error("request_conflict", "同一请求标识不能用于不同操作")
        return get_job(conn, access, existing["id"])
    library = conn.execute("SELECT root_path FROM libraries WHERE id=?", (library_id,)).fetchone()
    if library is None or not Path(library[0]).is_dir():
        raise _error("library_unavailable", "库目录暂不可访问，请检查挂载与权限", retryable=True)
    root = Path(library[0]).resolve()
    stat = root.stat()
    params = {"input": signature, "root": str(root), "root_device": stat.st_dev,
              "root_inode": stat.st_ino, "lang": current_lang(), "manifest_ready": False, "retry": False}
    job_id, now = uuid.uuid4().hex, db.utcnow()
    try:
        with conn:
            conn.execute("INSERT INTO jobs(id,kind,library_id,created_by,local_admin,request_key,params,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (job_id, kind, library_id, access.user_id, int(access.local_admin),
                                                       request_key, json.dumps(params), now, now))
    except sqlite3.IntegrityError:
        # Another request with the same key committed between lookup and insert.
        return submit(conn, access, kind, library_id, request_key=request_key, plan_id=plan_id, full=full, outdated_only=outdated_only)
    return get_job(conn, access, job_id)


def reconcile(conn, cfg, job_id):
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["state"] not in ("running", "cancelling"):
        return
    try:
        with operation_lock(cfg, "catalog", shared=True), operation_lock(cfg, "job:" + job_id, reentrant=False):
            error = _error("job_interrupted", "执行进程已退出，已完成项目保留，可重试未完成项目", retryable=True)
            conn.execute("UPDATE jobs SET state='interrupted',phase='interrupted',updated_at=?,error=? "
                         "WHERE id=? AND state IN ('running','cancelling')",
                         (db.utcnow(), json.dumps(error.as_dict()), job_id))
            if row["kind"] == "import":
                plan_id = json.loads(row["params"])["input"]["plan_id"]
                conn.execute("UPDATE import_plans SET state='interrupted' WHERE id=? AND state IN ('running','cancelling')",
                             (plan_id,))
                _import_progress(conn, job_id, plan_id)
            else:
                scan_id = json.loads(row["params"]).get("active_scan_id")
                if scan_id:
                    conn.execute("UPDATE scans SET status='interrupted',finished_at=? WHERE id=? AND status='running'",
                                 (db.utcnow(), scan_id))
            conn.commit()
    except ServiceError as exc:
        if exc.code != "operation_busy":
            raise


def _config(conn):
    from .config import Config
    path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    return Config(Path(path).parent)


def get_job(conn, access, job_id, *, offset=0, limit=100, include_items=True):
    _authorized(conn, access, job_id)
    reconcile(conn, _config(conn), job_id)
    row = _authorized(conn, access, job_id)
    value = {key: row[key] for key in ("id", "kind", "library_id", "library_name", "created_by", "state", "phase", "total",
                                      "completed", "succeeded", "failed", "attempt", "created_at", "updated_at",
                                      "started_at", "finished_at")}
    value["result"] = json.loads(row["result"]) if row["result"] else None
    value["phase_error_count"] = (value["result"] or {}).get("scan_error_count", 0)
    value["error"] = json.loads(row["error"]) if row["error"] else None
    value["plan_id"] = json.loads(row["params"])["input"]["plan_id"]
    if not include_items:
        return value
    offset, limit = max(0, offset), max(1, min(1000, limit))
    value["items"] = [dict(item) for item in conn.execute(
        "SELECT item_key,state,error_code,detail FROM job_items WHERE job_id=? ORDER BY item_key LIMIT ? OFFSET ?",
        (job_id, limit, offset))]
    count = conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id=?", (job_id,)).fetchone()[0]
    value.update(items_total=count, offset=offset, limit=limit,
                 next_offset=offset + limit if offset + limit < count else None)
    return value


def list_jobs(conn, access, *, offset=0, limit=50, include_items=True):
    user = access.user(conn)
    clauses, args = [], []
    if not user["is_admin"] and not access.local_admin:
        clauses.append("created_by=?")
        args.append(access.user_id)
    allowed = None if access.local_admin else auth.visible_library_ids(conn, user, minimum="manage")
    if allowed is not None:
        clauses.append(f"library_id IN ({','.join('?' for _ in allowed)})" if allowed else "0=1")
        args.extend(allowed)
    offset, limit = max(0, offset), max(1, min(100, limit))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    page = conn.execute("SELECT id FROM jobs" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                        [*args, limit + 1, offset]).fetchall()
    return {"items": [get_job(conn, access, row["id"], limit=1, include_items=include_items) for row in page[:limit]],
            "offset": offset, "next_offset": offset + limit if len(page) > limit else None}


@_catalog_write
def cancel(conn, access, job_id):
    _authorized(conn, access, job_id)
    conn.execute("UPDATE jobs SET cancel_requested=1,state=CASE WHEN state='queued' THEN 'cancelled' "
                 "ELSE 'cancelling' END,updated_at=? WHERE id=? AND state IN ('queued','running','cancelling')",
                 (db.utcnow(), job_id))
    conn.commit()
    return get_job(conn, access, job_id)


@_catalog_write
def retry(conn, access, job_id):
    row = get_job(conn, access, job_id)
    if row["state"] in ACTIVE or row["state"] == "succeeded":
        return row
    with operation_lock(_config(conn), "job:" + job_id):
        raw = _authorized(conn, access, job_id)
        params = json.loads(raw["params"])
        params["retry"] = True
        conn.execute("UPDATE jobs SET state='queued',phase='queued',cancel_requested=0,error=NULL,result=NULL,finished_at=NULL,"
                     "params=?,updated_at=? WHERE id=? AND state NOT IN ('queued','running','cancelling','succeeded')",
                     (json.dumps(params), db.utcnow(), job_id))
        conn.commit()
    return get_job(conn, access, job_id)


def _counts(conn, job_id):
    counts = {row[0]: row[1] for row in conn.execute(
        "SELECT state,COUNT(*) FROM job_items WHERE job_id=? GROUP BY state", (job_id,))}
    ok = counts.get("succeeded", 0) + counts.get("skipped", 0)
    failed = counts.get("failed", 0) + counts.get("conflict", 0)
    conn.execute("UPDATE jobs SET total=?,completed=?,succeeded=?,failed=?,updated_at=? WHERE id=?",
                 (sum(counts.values()), ok + failed, ok, failed, db.utcnow(), job_id))
    conn.commit()


def _update_item(conn, job_id, item_key, state, error_code=None, detail=None):
    """Commit one item and its counter delta together; retries replace its contribution."""
    def contribution(value):
        return (int(value in ('succeeded', 'skipped')), int(value in ('failed', 'conflict')))

    with db.transaction(conn):
        old = conn.execute('SELECT state FROM job_items WHERE job_id=? AND item_key=?',
                           (job_id, item_key)).fetchone()
        old_ok, old_failed = contribution(old['state'] if old else None)
        ok, failed = contribution(state)
        conn.execute('INSERT INTO job_items(job_id,item_key,state,error_code,detail) VALUES(?,?,?,?,?) '
                     'ON CONFLICT(job_id,item_key) DO UPDATE SET '
                     'state=excluded.state,error_code=excluded.error_code,detail=excluded.detail',
                     (job_id, item_key, state, error_code, detail))
        conn.execute('UPDATE jobs SET total=COALESCE(total,0)+?,completed=completed+?, '
                     'succeeded=succeeded+?,failed=failed+?,updated_at=? WHERE id=?',
                     (int(old is None), ok + failed - old_ok - old_failed, ok - old_ok,
                      failed - old_failed, db.utcnow(), job_id))


def _scan(conn, cfg, row, params, check):
    job_id, root = row["id"], Path(params["root"])
    if not params["manifest_ready"]:
        if params["input"].get("outdated_only"):
            from .hashing import DERIVATIVE_VERSION, PHASH_VERSION
            paths = {item[0] for item in conn.execute(
                "SELECT rel_path FROM images WHERE library_id=? AND missing=0 AND (phash_version!=? OR derivative_version!=?)",
                (row["library_id"], PHASH_VERSION, DERIVATIVE_VERSION))}
        else:
            paths = {item[0] for item in conn.execute("SELECT rel_path FROM images WHERE library_id=?", (row["library_id"],))}

        def walk_error(exc):
            raise _error("scan_unavailable", "无法读取完整目录，未开始扫描，请检查挂载与权限", retryable=True)

        if not params["input"].get("outdated_only"):
            for path in indexer._iter_image_files(root, onerror=walk_error):
                check()
                paths.add(path.relative_to(root).as_posix())
        params["manifest_ready"] = True
        with conn:
            conn.executemany("INSERT INTO job_items(job_id,item_key) VALUES(?,?)", [(job_id, path) for path in sorted(paths)])
            conn.execute("UPDATE jobs SET params=?,total=?,phase='scan' WHERE id=?", (json.dumps(params), len(paths), job_id))
    paths = {item[0] for item in conn.execute(
        "SELECT item_key FROM job_items WHERE job_id=? AND state NOT IN ('succeeded','skipped')", (job_id,))}

    def item_result(path, errors):
        _update_item(conn, job_id, path, "failed" if errors else "succeeded",
                     "scan_item_error" if errors else None, "\n".join(errors)[:4096] if errors else None)

    def progress(stats):
        if stats.get("scan_id"):
            params["active_scan_id"] = stats["scan_id"]
            conn.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
            conn.commit()

    result = indexer.scan_library(conn, cfg, row["library_id"], full=params["input"]["full"],
                                  check=check, item_result=item_result, only_paths=paths, progress=progress)
    conn.execute("UPDATE job_items SET state='failed',error_code='file_unavailable',detail=? "
                 "WHERE job_id=? AND state='pending'", (t("文件未能处理，请检查路径后重试"), job_id))
    _counts(conn, job_id)
    return result


def _import_progress(conn, job_id, plan_id, position=None):
    if position is not None:
        item = conn.execute('SELECT state,error_code,detail FROM import_plan_items '
                            'WHERE plan_id=? AND position=?', (plan_id, position)).fetchone()
        _update_item(conn, job_id, str(position), item['state'], item['error_code'], item['detail'])
        return
    # Reconcile the complete journal only when starting, finishing or recovering.
    conn.execute("INSERT OR REPLACE INTO job_items(job_id,item_key,state,error_code,detail) "
                 "SELECT ?,CAST(position AS TEXT),state,error_code,detail FROM import_plan_items WHERE plan_id=?",
                 (job_id, plan_id))
    _counts(conn, job_id)


def _import(conn, cfg, row, params, access, check):
    job_id, plan_id = row["id"], params["input"]["plan_id"]

    def progress(position=None):
        _import_progress(conn, job_id, plan_id, position)

    conn.execute("UPDATE jobs SET phase='import' WHERE id=?", (job_id,))
    progress()
    result = import_plans.execute_plan(conn, cfg, access, plan_id, retry=params["retry"], check=check, progress=progress)
    progress()
    return result


def run_job(cfg, job_id, *, should_stop=lambda: False):
    try:
        with operation_lock(cfg, "catalog", shared=True):
            return _run_job(cfg, job_id, should_stop=should_stop)
    except ServiceError as exc:
        if exc.code == "operation_busy":
            return False
        raise


def _run_job(cfg, job_id, *, should_stop):
    """Attempt one queued job. Busy work stays queued for another pass."""
    conn = db.open_db(cfg.db_path)
    try:
        with ExitStack() as locks:
            try:
                locks.enter_context(worker_slot(cfg))
                locks.enter_context(operation_lock(cfg, "job:" + job_id))
            except ServiceError as exc:
                if exc.code in ("operation_busy", "workers_busy"):
                    return False
                raise
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["state"] != "queued":
                return False
            params = json.loads(row["params"])
            access = AccessContext(row["created_by"], local_admin=bool(row["local_admin"]))
            language = push_lang(params["lang"])
            try:
                def check():
                    if should_stop():
                        raise WorkerStopped()
                    access.require_library(conn, row["library_id"], "manage")
                    if conn.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0]:
                        raise JobCancelled()
                    library = conn.execute("SELECT root_path FROM libraries WHERE id=?", (row["library_id"],)).fetchone()
                    root = Path(params["root"])
                    if (library is None or Path(library[0]).resolve() != root or not root.is_dir() or
                            (root.stat().st_dev, root.stat().st_ino) != (params["root_device"], params["root_inode"])):
                        raise _error("job_scope_changed", "任务的库目录已变化或离线，请检查后重新提交", retryable=True)

                check()
                try:
                    locks.enter_context(library_write_lock(cfg, Path(params["root"])))
                except ServiceError as exc:
                    if exc.code != "library_busy":
                        raise
                    conn.execute("UPDATE jobs SET phase='waiting_for_library',updated_at=? WHERE id=?", (db.utcnow(), job_id))
                    conn.commit()
                    return False
                conn.execute("UPDATE jobs SET state='running',phase='starting',attempt=attempt+1,started_at=?,updated_at=? "
                             "WHERE id=?", (db.utcnow(), db.utcnow(), job_id))
                conn.commit()
                check()
                result = (_import(conn, cfg, row, params, access, check) if row["kind"] == "import"
                          else _scan(conn, cfg, row, params, check))
                check()
                failed = conn.execute("SELECT failed FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                state = result.get("state") or ("partial" if failed or result.get("errors") else "succeeded")
                conn.execute("UPDATE jobs SET state=?,phase='finished',result=?,error=NULL,finished_at=?,updated_at=? WHERE id=?",
                             (state, json.dumps(result), db.utcnow(), db.utcnow(), job_id))
                conn.commit()
            except (Exception, KeyboardInterrupt) as exc:
                conn.rollback()
                if isinstance(exc, JobCancelled):
                    state, error = "cancelled", None
                elif isinstance(exc, (WorkerStopped, KeyboardInterrupt)):
                    state, error = "interrupted", _error("job_interrupted", "执行进程已退出，已完成项目保留，可重试未完成项目", retryable=True)
                else:
                    state = "failed"
                    error = exc if isinstance(exc, ServiceError) else _error(
                        "job_failed", "任务未能完成，请检查目录权限、文件和可用空间", retryable=True)
                conn.execute("UPDATE jobs SET state=?,phase=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                             (state, state, json.dumps(error.as_dict()) if error else None, db.utcnow(), db.utcnow(), job_id))
                if row["kind"] == "import" and state in ("cancelled", "interrupted"):
                    conn.execute("UPDATE import_plans SET state=? WHERE id=? AND state!='succeeded'",
                                 (state, params["input"]["plan_id"]))
                conn.commit()
            finally:
                pop_lang(language)
        return True
    finally:
        conn.close()


def run_next(cfg, *, should_stop=lambda: False):
    try:
        with operation_lock(cfg, "catalog", shared=True):
            return _run_next(cfg, should_stop=should_stop)
    except ServiceError as exc:
        if exc.code == "operation_busy":
            return False
        raise


def _run_next(cfg, *, should_stop):
    conn = db.open_db(cfg.db_path)
    try:
        pending = [row[0] for row in conn.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created_at,id")]
        for row in conn.execute("SELECT id FROM jobs WHERE state IN ('running','cancelling')").fetchall():
            reconcile(conn, cfg, row[0])
    finally:
        conn.close()
    for job_id in pending:
        if should_stop():
            break
        if run_job(cfg, job_id, should_stop=should_stop):
            return True
    return False


def wait(cfg, access, job_id):
    delay = .05
    while True:
        run_job(cfg, job_id)
        conn = db.open_db(cfg.db_path)
        try:
            value = get_job(conn, access, job_id)
        finally:
            conn.close()
        if value["state"] in TERMINAL:
            return value
        time.sleep(delay)
        delay = min(1, delay * 1.5)
