"""Read-only catalog and storage checks; no migration or automatic cleanup."""
from collections import Counter
import hashlib
from pathlib import Path
import sqlite3

from . import db
from .coordination import operation_lock
from .errors import ServiceError
from .i18n import t


def check(cfg, *, full=False):
    if not cfg.db_path.is_file():
        raise ServiceError('not_initialized', t('数据目录尚未初始化'),
                           next_step=t('请先运行 picshelf init --admin <管理员名>'))
    counts, findings, libraries = Counter(), [], []
    total_bytes = {'originals': 0, 'derivatives': 0, 'database': cfg.db_path.stat().st_size}
    def issue(code, **objects):
        counts[code] += 1
        if len(findings) < 200:
            findings.append({'code': code, **objects})
    with operation_lock(cfg, 'catalog', shared=True):
        conn = sqlite3.connect(cfg.db_path.resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN')
            schema = db.validate_schema(conn)
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                issue('database_integrity')
            for row in conn.execute('PRAGMA foreign_key_check'):
                issue('orphan_relation', table=row[0], row_id=row[1])
            roots = {}
            for row in conn.execute('SELECT * FROM libraries ORDER BY id'):
                item = dict(row)
                root = Path(item['root_path'])
                state = 'available'
                try:
                    st = root.stat()
                    if not root.is_dir():
                        raise OSError
                    if item.get('root_device') is not None and (st.st_dev, st.st_ino) != (item['root_device'], item['root_inode']):
                        state = 'changed'
                        issue('library_root_changed', library_id=item['id'])
                except OSError:
                    state = 'unavailable'
                    issue('library_unavailable', library_id=item['id'])
                roots[item['id']] = (root.resolve(), state)
                libraries.append({'id': item['id'], 'name': item['name'], 'state': state})
            referenced = set()
            for raw in conn.execute('SELECT * FROM images ORDER BY id'):
                image = dict(raw)
                root, state = roots.get(image['library_id'], (cfg.data_dir, 'unavailable'))
                if state == 'available':
                    path = root / image['rel_path']
                    try:
                        resolved = path.resolve(strict=True)
                        if not resolved.is_relative_to(root) or not resolved.is_file():
                            issue('original_path_invalid', image_id=image['id'])
                        else:
                            total_bytes['originals'] += resolved.stat().st_size
                            if image['missing']:
                                issue('original_returned', image_id=image['id'])
                            if full:
                                before = resolved.stat()
                                with resolved.open('rb') as stream:
                                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                                after = resolved.stat()
                                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                                    issue('original_changed_during_check', image_id=image['id'])
                                elif digest != image['sha256']:
                                    issue('original_hash_mismatch', image_id=image['id'])
                    except FileNotFoundError:
                        issue('original_missing', image_id=image['id'])
                    except OSError:
                        issue('original_unreadable', image_id=image['id'])
                for field in ('thumb_rel', 'preview_rel'):
                    rel = image[field]
                    if not rel:
                        issue('derivative_missing', image_id=image['id'], kind=field)
                        continue
                    cache = (cfg.media_dir / rel).resolve()
                    referenced.add(cache)
                    if not cache.is_relative_to(cfg.media_dir.resolve()):
                        issue('derivative_path_invalid', image_id=image['id'], kind=field)
                    elif not cache.is_file():
                        issue('derivative_missing', image_id=image['id'], kind=field)
            for row in conn.execute('SELECT a.id FROM albums a LEFT JOIN images i ON i.id=a.cover_image_id '
                                    'WHERE a.cover_image_id IS NOT NULL AND (i.id IS NULL OR i.missing=1 OR '
                                    'NOT EXISTS(SELECT 1 FROM album_images ai WHERE ai.album_id=a.id AND ai.image_id=a.cover_image_id))'):
                issue('album_cover_invalid', album_id=row[0])
            if schema >= 13:
                for row in conn.execute('SELECT id FROM works w WHERE preferred_image_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM work_images wi WHERE wi.work_id=w.id AND wi.image_id=w.preferred_image_id)'):
                    issue('work_preference_invalid', work_id=row[0])
            for cache in cfg.media_dir.rglob('*.webp'):
                try:
                    if cache.is_symlink():
                        continue
                    total_bytes['derivatives'] += cache.stat().st_size
                    if cache.resolve() not in referenced:
                        issue('derivative_unreferenced')
                except OSError:
                    issue('derivative_unreadable')
            return {'schema': schema, 'full_hash_check': full, 'read_only': True,
                    'counts': dict(sorted(counts.items())), 'findings': findings,
                    'findings_truncated': sum(counts.values()) > len(findings),
                    'libraries': libraries, 'bytes': total_bytes,
                    'repair': {'automatic': False, 'derivatives': 'picshelf rebuild <library>',
                               'originals': 'Restore originals from your independent backup, then scan.'}}
        except sqlite3.Error as exc:
            raise ServiceError('database_integrity', t('数据库完整性检查未完成'),
                               next_step=t('请停止服务并检查数据库备份；不要删除原图或数据库')) from exc
        finally:
            conn.close()
