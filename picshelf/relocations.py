"""Verify copied roots and rebind a library without touching original files."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from . import db, libsvc, manifest
from .errors import ServiceError
from .i18n import t


def _error(code='relocation_conflict'):
    status = 404 if code == 'relocation_plan_not_found' else 410 if code == 'relocation_plan_expired' else 409
    return ServiceError(code, t('库路径迁移未执行'), status=status,
                        next_step=t('请检查副本的相对路径、内容、根目录身份与预演冲突；修正后重新预演'))


def _identity(root):
    stat = root.stat()
    return [stat.st_dev, stat.st_ino]


def _catalog(conn, library_id):
    return {'library': libsvc.get_library(conn, library_id),
            'images': [dict(row) for row in conn.execute('SELECT id,rel_path,sha256,size_bytes,missing FROM images WHERE library_id=? ORDER BY id', (library_id,))],
            'revision': conn.execute("SELECT value FROM meta WHERE key='catalog_revision'").fetchone()[0]}


def _inspect(root, images):
    inventory, conflicts = {}, []
    def walk_error(_):
        conflicts.append({'code': 'target_unreadable'})
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                conflicts.append({'code': 'target_symlink', 'rel_path': path.relative_to(root).as_posix()})
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                continue
            try:
                before = path.stat()
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                after = path.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise OSError
                inventory[rel] = {'sha256': digest, 'size_bytes': after.st_size,
                                  'identity': [after.st_dev, after.st_ino, after.st_mtime_ns]}
            except OSError:
                conflicts.append({'code': 'target_unreadable', 'rel_path': rel})
    matches, missing, ambiguities = [], [], []
    for image in images:
        rel = image['rel_path']
        current = inventory.get(rel)
        if current is None:
            missing.append({'image_id': image['id'], 'rel_path': rel})
            candidates = [path for path, entry in inventory.items() if entry['sha256'] == image['sha256']]
            if candidates:
                ambiguities.append({'image_id': image['id'], 'candidates': sorted(candidates), 'automatic_match': False})
        elif image['sha256'] is None or (current['sha256'], current['size_bytes']) != (image['sha256'], image['size_bytes']):
            conflicts.append({'code': 'target_content_changed', 'image_id': image['id'], 'rel_path': rel})
        else:
            matches.append({'image_id': image['id'], 'rel_path': rel})
    known = {image['rel_path'] for image in images}
    return {'matches': matches, 'missing': missing, 'conflicts': conflicts,
            'ambiguities': ambiguities, 'extra_files': sorted(set(inventory) - known),
            'ready': not missing and not conflicts}, inventory


def preview(conn, access, library_id, target_path):
    access.require_capability(conn, 'admin')
    access.require_library(conn, library_id, 'manage')
    with db.transaction(conn):
        catalog = _catalog(conn, library_id)
        root = libsvc._library_root(target_path, writable=catalog['library']['mode'] == 'managed')
        libsvc._check_overlap(conn, root, exclude=library_id)
        identity = _identity(root)
    report, inventory = _inspect(root, catalog['images'])
    with db.transaction(conn):
        access.require_capability(conn, 'admin')
        access.require_library(conn, library_id, 'manage')
        if (identity != _identity(root) or
                manifest.checksum(_catalog(conn, library_id)) != manifest.checksum(catalog)):
            raise _error('relocation_root_changed')
        snapshot = {'catalog': manifest.checksum(catalog), 'target': str(root),
                    'identity': identity, 'inventory': manifest.checksum(inventory)}
        report.update({'library_id': library_id, 'old_root': catalog['library']['root_path'],
                       'new_root': str(root), 'moves_files': False, 'indexes_extra_files': False})
        now = datetime.now(timezone.utc)
        plan_id = uuid.uuid4().hex
        conn.execute('INSERT INTO relocation_plans(id,library_id,created_by,created_at,expires_at,snapshot,preview) VALUES(?,?,?,?,?,?,?)',
                     (plan_id, library_id, access.user_id, now.isoformat(), (now + timedelta(hours=24)).isoformat(),
                      json.dumps(snapshot), json.dumps(report)))
    return get(conn, access, plan_id)


def get(conn, access, plan_id):
    access.require_capability(conn, 'admin')
    row = conn.execute('SELECT * FROM relocation_plans WHERE id=? AND created_by=?', (plan_id, access.user_id)).fetchone()
    if row is None:
        raise _error('relocation_plan_not_found')
    access.require_library(conn, row['library_id'], 'manage')
    return {'id': row['id'], 'state': row['state'], 'expires_at': row['expires_at'], 'preview': json.loads(row['preview'])}


def execute(conn, cfg, access, plan_id):
    plan = get(conn, access, plan_id)
    if plan['state'] == 'succeeded':
        return plan
    if not plan['preview']['ready']:
        raise _error()
    row = conn.execute('SELECT * FROM relocation_plans WHERE id=?', (plan_id,)).fetchone()
    snapshot = json.loads(row['snapshot'])
    old_root, target = Path(plan['preview']['old_root']), Path(snapshot['target'])
    with ExitStack() as stack:
        # The old mount may be offline; lock its stored physical identity too.
        from .coordination import operation_lock
        library = libsvc.get_library(conn, row['library_id'])
        stack.enter_context(operation_lock(cfg, 'catalog', shared=True))
        identities = {tuple(snapshot['identity'])}
        if library['root_device'] is not None:
            identities.add((library['root_device'], library['root_inode']))
        if old_root.is_dir():
            identities.add(tuple(_identity(old_root)))
        for device, inode in sorted(identities):
            stack.enter_context(operation_lock(cfg, f'library:{device}:{inode}'))
        with db.transaction(conn):
            current = get(conn, access, plan_id)
            if current['state'] == 'succeeded':
                return current
            if row['expires_at'] < db.utcnow():
                raise _error('relocation_plan_expired')
            catalog = _catalog(conn, row['library_id'])
            try:
                target_identity = _identity(target)
            except OSError as exc:
                raise _error('relocation_target_changed') from exc
            if manifest.checksum(catalog) != snapshot['catalog'] or target_identity != snapshot['identity']:
                raise _error('relocation_target_changed')
            libsvc._check_overlap(conn, target, exclude=row['library_id'])
        report, inventory = _inspect(target, catalog['images'])
        with db.transaction(conn):
            current = get(conn, access, plan_id)
            if current['state'] == 'succeeded':
                return current
            if row['expires_at'] < db.utcnow():
                raise _error('relocation_plan_expired')
            if manifest.checksum(_catalog(conn, row['library_id'])) != snapshot['catalog']:
                raise _error('relocation_target_changed')
            if not report['ready'] or manifest.checksum(inventory) != snapshot['inventory'] or _identity(target) != snapshot['identity']:
                raise _error('relocation_target_changed')
            device, inode = snapshot['identity']
            conn.execute('UPDATE libraries SET root_path=?,root_device=?,root_inode=? WHERE id=?', (str(target), device, inode, row['library_id']))
            conn.execute("UPDATE relocation_plans SET state='succeeded' WHERE id=?", (plan_id,))
    return get(conn, access, plan_id)
