"""Explicit managed-library reorganization using recoverable import journals."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

from . import db, hashing, importer, import_plans, libsvc, manifest, metadata, templates
from .coordination import library_write_lock
from .errors import ServiceError
from .i18n import t


def _error(code='reorganization_conflict'):
    return ServiceError(code, t('库文件重组未完成'), status=409,
                        next_step=t('请检查逐项路径与冲突；保留文件，使用原计划重试或重新预演'))


def _signature(conn, library_id):
    return manifest.checksum({'library': libsvc.get_library(conn, library_id),
        'revision': conn.execute("SELECT value FROM meta WHERE key='catalog_revision'").fetchone()[0],
        'images': [dict(row) for row in conn.execute('SELECT id,rel_path,sha256,size_bytes,missing FROM images WHERE library_id=? ORDER BY id', (library_id,))]})


def verify_preview(conn, snapshot):
    if _signature(conn, snapshot['library']['id']) != snapshot['catalog_fingerprint']:
        raise _error('reorganization_target_changed')


def preview(conn, cfg, access, library_id, template):
    access.require_library(conn, library_id, 'manage')
    library = libsvc.get_library(conn, library_id)
    if library['mode'] != 'managed':
        raise _error('reorganization_requires_managed')
    templates.validate(template)
    root = libsvc._library_root(library['root_path'], writable=True)
    identity = root.stat()
    if library['root_device'] is not None and (identity.st_dev, identity.st_ino) != (library['root_device'], library['root_inode']):
        raise _error('library_root_changed')
    with library_write_lock(cfg, root):
        with db.transaction(conn):
            access.require_library(conn, library_id, 'manage')
            root_identity = import_plans._identity(root.stat())
            fingerprint = _signature(conn, library_id)
            images = []
            for row in conn.execute('SELECT * FROM images WHERE library_id=? ORDER BY id', (library_id,)):
                image = dict(row)
                fields = {'artists': [r[0] for r in conn.execute('SELECT a.name FROM artists a JOIN image_artists ia ON ia.artist_id=a.id WHERE ia.image_id=?', (image['id'],))],
                          'tags': [r[0] for r in conn.execute('SELECT t.name FROM tags t JOIN image_tags it ON it.tag_id=t.id WHERE it.image_id=?', (image['id'],))]}
                source = conn.execute('SELECT note,url,collected_at FROM image_sources WHERE image_id=? ORDER BY id LIMIT 1', (image['id'],)).fetchone()
                if source:
                    fields['source'] = source['note'] or source['url']
                    if source['collected_at']:
                        fields['date'] = metadata.timestamp(source['collected_at'])[:10]
                images.append((image, fields))
        items, identities = [], []
        reserved = {importer._portable_key(path) for path in root.rglob('*')}
        for image, fields in images:
            src = root / image['rel_path']
            destination = root / templates.render(template, image['filename'], fields)
            action, error, identity = 'move', '', None
            try:
                if image['missing'] or src.is_symlink() or not src.resolve(strict=True).is_relative_to(root):
                    raise _error('reorganization_source_missing')
                identity = import_plans._source_identity(src.stat())
                if hashing.sha256_file(src) != image['sha256']:
                    raise _error('reorganization_source_changed')
                if destination == src:
                    action = 'skip'
                else:
                    stem, suffix, count = destination.stem, destination.suffix, 1
                    while importer._portable_key(destination) in reserved or destination.exists():
                        destination = destination.with_name(f'{stem}-{count}{suffix}')
                        count += 1
                    if not destination.resolve().is_relative_to(root):
                        raise _error()
                    reserved.add(importer._portable_key(destination))
            except (ServiceError, OSError) as exc:
                action, error = 'error', exc.code if isinstance(exc, ServiceError) else 'reorganization_source_missing'
            items.append({'src': str(src), 'dst': str(destination), 'action': action, 'sha256': image['sha256'],
                          'image_id': image['id'], 'original_rel': image['rel_path'], 'fields': fields, 'reason': error})
            identities.append(identity)
        now, plan_id = datetime.now(timezone.utc), uuid.uuid4().hex
        snapshot = {'library': {'id': library_id, 'name': library['name']}, 'root': str(root), 'source': str(root),
                    'root_identity': root_identity, 'source_identity': root_identity,
                    'mode': 'move', 'rename': True, 'template': template, 'reorganize': True,
                    'catalog_fingerprint': fingerprint, 'items': items,
                    'stats': {key: sum(item['action'] == key for item in items) for key in ('hardlink', 'copy', 'move', 'skip', 'error')}}
        with db.transaction(conn):
            access.require_library(conn, library_id, 'manage')
            if (_signature(conn, library_id) != fingerprint or root.resolve() != root or
                    import_plans._identity(root.stat()) != root_identity):
                raise _error('reorganization_target_changed')
            conn.execute('INSERT INTO import_plans(id,library_id,created_by,created_at,expires_at,snapshot) VALUES(?,?,?,?,?,?)',
                         (plan_id, library_id, access.user_id, now.isoformat(), (now + timedelta(hours=24)).isoformat(), json.dumps(snapshot)))
            conn.executemany('INSERT INTO import_plan_items(plan_id,position,destination,state,source_identity,image_id,catalog_published) VALUES(?,?,?,?,?,?,?)',
                             [(plan_id, index, item['dst'], 'skipped' if item['action'] == 'skip' else 'failed' if item['action'] == 'error' else 'pending',
                               json.dumps(identities[index]) if identities[index] else None, item['image_id'], int(item['action'] == 'skip'))
                              for index, item in enumerate(items)])
    return import_plans.get_plan(conn, access, plan_id)


def publish(conn, plan_id, position, snapshot, item):
    record = conn.execute('SELECT * FROM import_plan_items WHERE plan_id=? AND position=?', (plan_id, position)).fetchone()
    if record['catalog_published']:
        return
    destination = Path(item['dst'])
    known = json.loads(record['destination_identity']) if record['destination_identity'] else None
    if (record['state'] != 'succeeded' or known != import_plans._identity(destination.stat()) or
            hashing.sha256_file(destination) != item['sha256']):
        raise _error()
    rel = destination.relative_to(snapshot['root']).as_posix()
    with db.transaction(conn):
        image = conn.execute('SELECT * FROM images WHERE id=?', (item['image_id'],)).fetchone()
        if image is None or image['library_id'] != snapshot['library']['id'] or image['rel_path'] not in (item['original_rel'], rel) or image['sha256'] != item['sha256']:
            raise _error('reorganization_identity_changed')
        conn.execute('UPDATE images SET rel_path=?,filename=?,mtime=?,missing=0 WHERE id=?', (rel, destination.name, destination.stat().st_mtime, item['image_id']))
        conn.execute('UPDATE import_plan_items SET catalog_published=1 WHERE plan_id=? AND position=?', (plan_id, position))
