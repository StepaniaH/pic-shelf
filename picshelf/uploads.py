"""Owned browser uploads staged separately from library originals."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
import shutil
import uuid

from . import hashing, import_plans, libsvc
from .coordination import operation_lock
from .db import transaction
from .errors import ServiceError
from .i18n import t

MAX_FILES = 10000
MAX_BYTES = 2 * 1024 ** 3
MAX_FILE_BYTES = hashing.MAX_SOURCE_BYTES


def _error(code='upload_invalid', status=400):
    return ServiceError(code, t('无法接收所选文件'), status=status,
                        next_step=t('请选择有效图片；每次最多 10000 张、总计 2 GiB，单张最多 256 MiB。失败后可重新选择。'))


def _owned(conn, access, upload_id):
    row = conn.execute('SELECT * FROM import_uploads WHERE id=? AND created_by=?',
                       (upload_id, access.user_id)).fetchone()
    if row is None:
        raise _error('upload_not_found', 404)
    access.require_library(conn, row['library_id'], 'manage')
    libsvc.require_importable(libsvc.get_library(conn, row['library_id']))
    return row


def _staging(cfg, row):
    expected = cfg.data_dir.resolve() / 'uploads' / row['id']
    if Path(row['source']) != expected or expected.resolve() != expected:
        raise _error()
    return expected


def cleanup(conn, cfg):
    """Expire abandoned previews, retaining sources needed by started retries."""
    now = datetime.now(timezone.utc).isoformat()
    candidates = ("SELECT u.* FROM import_uploads u LEFT JOIN import_plans p ON p.id=u.plan_id "
                  "WHERE u.state IN ('receiving','sealed','completed') AND u.expires_at<? "
                  "AND (p.id IS NULL OR p.started_at IS NULL OR u.state='completed')")
    for candidate in conn.execute(candidates, (now,)).fetchall():
        try:
            with operation_lock(cfg, 'upload:' + candidate['id']):
                # Starting an import can change eligibility while cleanup waits.
                row = conn.execute(candidates + ' AND u.id=?', (now, candidate['id'])).fetchone()
                if row is None:
                    continue
                directory = _staging(cfg, row)
                with conn:
                    if row['state'] != 'completed':
                        conn.execute("UPDATE import_uploads SET state='expired' WHERE id=?", (row['id'],))
                if directory.exists():
                    shutil.rmtree(directory)
                if row['state'] == 'completed':
                    with conn:
                        conn.execute("UPDATE import_uploads SET state='cleaned' WHERE id=?", (row['id'],))
        except ServiceError as exc:
            if exc.code != 'operation_busy':
                raise


@contextmanager
def starting_plan(conn, cfg, plan_id):
    """Keep staging until the import has durably recorded that it started."""
    row = conn.execute('SELECT id FROM import_uploads WHERE plan_id=?', (plan_id,)).fetchone()
    if row is None:
        yield
        return
    # Execution takes the library lock first. Cleanup and discard only take the
    # upload lock; file processing releases it after committing started_at.
    with operation_lock(cfg, 'upload:' + row['id']):
        row = conn.execute('SELECT * FROM import_uploads WHERE id=?', (row['id'],)).fetchone()
        if row['state'] != 'sealed':
            raise _error('upload_closed', 409)
        _staging(cfg, row)
        yield


def create(conn, cfg, access, library_id):
    access.require_library(conn, library_id, 'manage')
    libsvc.require_importable(libsvc.get_library(conn, library_id))
    cleanup(conn, cfg)
    now = datetime.now(timezone.utc)
    upload_id = uuid.uuid4().hex
    directory = cfg.data_dir.resolve() / 'uploads' / upload_id
    if directory.parent.resolve() != directory.parent:
        raise _error()
    with transaction(conn):
        count = conn.execute("SELECT COUNT(*) FROM import_uploads WHERE created_by=? "
                             "AND state IN ('receiving','sealed')", (access.user_id,)).fetchone()[0]
        if count >= 4:
            raise ServiceError('upload_limit', t('未完成的上传过多'), status=429,
                               next_step=t('请先完成已有导入；未开始的上传会在 24 小时后过期。'))
        directory.mkdir(parents=True, mode=0o700)
        conn.execute('INSERT INTO import_uploads(id,created_by,library_id,source,created_at,expires_at) '
                     'VALUES(?,?,?,?,?,?)', (upload_id, access.user_id, library_id, str(directory),
                                             now.isoformat(), (now + timedelta(hours=24)).isoformat()))
    return {'id': upload_id, 'max_files': MAX_FILES, 'max_bytes': MAX_BYTES, 'max_file_bytes': MAX_FILE_BYTES}


async def receive(conn, cfg, access, upload_id, relative_path, request):
    from starlette.concurrency import run_in_threadpool
    row = _owned(conn, access, upload_id)
    parts = relative_path.split('/')
    relative = PurePosixPath(relative_path)
    if (relative.is_absolute() or any(part in ('', '.', '..') for part in parts)
            or '\\' in relative_path or ':' in relative_path or '\x00' in relative_path
            or len(relative_path) > 2048 or relative.suffix.lower() not in hashing.IMAGE_EXTS):
        raise _error()
    try:
        size = int(request.headers.get('content-length', ''))
    except ValueError:
        raise _error() from None
    if size <= 0 or size > MAX_FILE_BYTES:
        raise _error('upload_too_large', 413)
    with operation_lock(cfg, 'upload:' + upload_id, independent=True):
        row = _owned(conn, access, upload_id)
        if row['state'] != 'receiving' or row['expires_at'] <= datetime.now(timezone.utc).isoformat():
            raise _error('upload_closed', 409)
        if row['file_count'] >= MAX_FILES or row['bytes'] + size > MAX_BYTES:
            raise _error('upload_too_large', 413)
        target = _staging(cfg, row).joinpath(*parts)
        if target.resolve() != target or target.exists():
            raise _error('upload_path_conflict', 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        written, created = 0, False
        try:
            with target.open('xb') as output:
                created = True
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > size:
                        raise _error('upload_too_large', 413)
                    await run_in_threadpool(output.write, chunk)
            if written != size:
                raise _error()
            with conn:
                conn.execute('UPDATE import_uploads SET file_count=file_count+1, bytes=bytes+? WHERE id=?',
                             (written, upload_id))
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
    return {'ok': True}


def preview(conn, cfg, access, upload_id, *, rename=True, album_id=None):
    with operation_lock(cfg, 'upload:' + upload_id):
        row = _owned(conn, access, upload_id)
        if row['plan_id']:
            return import_plans.get_plan(conn, access, row['plan_id'])
        if row['state'] not in ('receiving', 'sealed') or not row['file_count']:
            raise _error('upload_closed', 409)
        if row['expires_at'] <= datetime.now(timezone.utc).isoformat():
            raise _error('upload_closed', 409)
        _staging(cfg, row)
        with conn:
            conn.execute("UPDATE import_uploads SET state='sealed' WHERE id=?", (upload_id,))
        plan = import_plans.create_plan(conn, access, row['library_id'], row['source'], mode='hardlink', rename=rename, album_id=album_id)
        with conn:
            conn.execute('UPDATE import_uploads SET plan_id=? WHERE id=?', (plan['plan_id'], upload_id))
        return import_plans.get_plan(conn, access, plan['plan_id'])


def complete(conn, cfg, plan_id):
    row = conn.execute("SELECT * FROM import_uploads WHERE plan_id=? AND state='sealed'", (plan_id,)).fetchone()
    if row is None:
        return
    with operation_lock(cfg, 'upload:' + row['id']):
        row = conn.execute("SELECT * FROM import_uploads WHERE plan_id=? AND state='sealed'", (plan_id,)).fetchone()
        if row is None:
            return
        directory = _staging(cfg, row)
        with conn:
            conn.execute("UPDATE import_uploads SET state='completed' WHERE id=?", (row['id'],))
        if directory.exists():
            shutil.rmtree(directory)
        with conn:
            conn.execute("UPDATE import_uploads SET state='cleaned' WHERE id=?", (row['id'],))


def discard(conn, cfg, access, upload_id):
    with operation_lock(cfg, 'upload:' + upload_id):
        row = _owned(conn, access, upload_id)
        if row['plan_id']:
            plan = conn.execute('SELECT started_at FROM import_plans WHERE id=?', (row['plan_id'],)).fetchone()
            if plan['started_at'] is not None:
                raise _error('upload_closed', 409)
        directory = _staging(cfg, row)
        with conn:
            conn.execute("UPDATE import_uploads SET state='discarded' WHERE id=?", (upload_id,))
            if row['plan_id']:
                conn.execute("UPDATE import_plans SET state='cancelled',cancel_requested=1 WHERE id=?", (row['plan_id'],))
        if directory.exists():
            shutil.rmtree(directory)
    return {'ok': True}
