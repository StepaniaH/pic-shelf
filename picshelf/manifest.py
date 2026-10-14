"""Versioned, user-scoped metadata interchange with explicit identity mapping."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid
from urllib.parse import urlsplit

from . import auth, db, metadata, names, play, search
from .coordination import library_write_lock
from .errors import ServiceError
from .i18n import t

FORMAT = 2
MAX_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 50000
# This is an export allowlist, not a reflection of the database schema.
COLUMNS = {
    'users': ('id', 'username', 'display_name'),
    'libraries': ('id', 'name', 'description'),
    'images': ('id', 'library_id', 'rel_path', 'sha256', 'size_bytes'),
    'tags': ('id', 'name', 'namespace', 'category', 'description', 'created_at'),
    'artists': ('id', 'name', 'aliases', 'links', 'note', 'created_at'),
    'albums': ('id', 'library_id', 'name', 'kind', 'rel_dir', 'category', 'cover_image_id', 'description', 'created_at'),
    'tag_aliases': ('id', 'entity_id', 'name', 'match_key', 'created_by', 'created_at'),
    'artist_aliases': ('id', 'entity_id', 'name', 'match_key', 'created_by', 'created_at'),
    'image_tags': ('image_id', 'tag_id', 'added_by', 'added_at'),
    'image_artists': ('image_id', 'artist_id'),
    'album_images': ('album_id', 'image_id', 'position', 'added_at'),
    'ratings': ('image_id', 'user_id', 'value', 'updated_at'),
    'favorites': ('image_id', 'user_id', 'created_at'),
    'image_workflow': ('image_id', 'user_id', 'state', 'updated_at'),
    'comments': ('id', 'image_id', 'user_id', 'body', 'created_at', 'updated_at', 'deleted'),
    'notes': ('id', 'image_id', 'user_id', 'body', 'geometry', 'created_at', 'updated_at', 'deleted'),
    'image_sources': ('id', 'image_id', 'origin', 'original_filename', 'collected_at', 'created_time', 'url', 'note', 'created_by', 'created_at'),
    'tag_claims': ('id', 'image_id', 'tag_id', 'origin', 'confirmed', 'source_ref', 'created_by', 'created_at', 'confirmed_by', 'confirmed_at', 'retired_at'),
    'saved_queries': ('id', 'owner_id', 'name', 'kind', 'query', 'sort', 'display', 'created_at', 'updated_at'),
    'play_rules': ('id', 'owner_id', 'name', 'spec', 'created_at', 'updated_at'),
    'works': ('id', 'name', 'preferred_image_id', 'created_by', 'created_at'),
    'work_images': ('work_id', 'image_id', 'role', 'created_at'),
}
REFERENCES = {'library_id': 'libraries', 'image_id': 'images', 'cover_image_id': 'images',
              'tag_id': 'tags', 'artist_id': 'artists', 'album_id': 'albums',
              'user_id': 'users', 'owner_id': 'users', 'added_by': 'users',
              'created_by': 'users', 'confirmed_by': 'users', 'work_id': 'works', 'preferred_image_id': 'images'}
KEYS = {'image_tags': ('image_id', 'tag_id'), 'image_artists': ('image_id', 'artist_id'),
        'album_images': ('album_id', 'image_id'), 'ratings': ('image_id', 'user_id'),
        'favorites': ('image_id', 'user_id'), 'image_workflow': ('image_id', 'user_id'),
        'work_images': ('work_id', 'image_id')}
TIMES = {'created_at', 'updated_at', 'added_at', 'confirmed_at', 'retired_at', 'collected_at', 'created_time'}


def _error(code='manifest_invalid', status=400):
    return ServiceError(code, t('元数据清单无法安全导入'), status=status,
                        next_step=t('请检查格式版本、校验和、显式映射及预演冲突；修正后重新预演'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _uid(conn, table, local_id):
    row = conn.execute('SELECT uid FROM portable_import_ids WHERE kind=? AND local_id=? ORDER BY uid LIMIT 1',
                       (table, local_id)).fetchone()
    if row is None:
        row = conn.execute('SELECT uid FROM portable_ids WHERE kind=? AND local_id=?', (table, local_id)).fetchone()
    if row is None:
        raise _error('manifest_identity_missing', 409)
    return row[0]


def _resolve(conn, table, uid):
    rows = conn.execute('SELECT kind,local_id FROM portable_ids WHERE uid=? UNION '
                        'SELECT kind,local_id FROM portable_import_ids WHERE uid=?', (uid, uid)).fetchall()
    if len(rows) > 1 or (rows and rows[0]['kind'] != table):
        raise _error('manifest_identity_conflict', 409)
    return rows[0]['local_id'] if rows else None


def _rows(conn, table):
    return [dict(row) for row in conn.execute(f"SELECT {','.join(COLUMNS[table])} FROM {table}")]


def _export_rows(conn, access, selected, allowed):
    """Filter in SQLite before loading records, bounding the complete export."""
    for suffix in ('libraries', 'images', 'visible'):
        conn.execute(f'DROP TABLE IF EXISTS temp._manifest_{suffix}')
        conn.execute(f'CREATE TEMP TABLE _manifest_{suffix}(id INTEGER PRIMARY KEY)')
    conn.executemany('INSERT INTO _manifest_libraries VALUES(?)', [(lid,) for lid in selected])
    conn.execute('INSERT INTO _manifest_images SELECT id FROM images WHERE library_id IN (SELECT id FROM _manifest_libraries)')
    if allowed is not None:
        conn.executemany('INSERT INTO _manifest_visible VALUES(?)', [(lid,) for lid in allowed])
    scope = 'IN (SELECT id FROM _manifest_images)'
    album_where = (f'(library_id IN (SELECT id FROM _manifest_libraries) OR EXISTS(SELECT 1 FROM album_images ai WHERE ai.album_id=albums.id AND ai.image_id {scope})) '
                   f'AND NOT EXISTS(SELECT 1 FROM album_images ai WHERE ai.album_id=albums.id AND ai.image_id NOT {scope}) '
                   f'AND (cover_image_id IS NULL OR cover_image_id {scope})')
    tag_ids = f'SELECT tag_id FROM image_tags WHERE image_id {scope} UNION SELECT tag_id FROM tag_claims WHERE image_id {scope}'
    artist_ids = f'SELECT artist_id FROM image_artists WHERE image_id {scope}'
    work_ids = f'SELECT work_id FROM work_images WHERE image_id {scope}'
    filters = {'users': ('id=?', (access.user_id,)), 'libraries': ('id IN (SELECT id FROM _manifest_libraries)', ()),
               'images': ('id ' + scope, ()), 'tags': (f'id IN ({tag_ids})', ()), 'artists': (f'id IN ({artist_ids})', ()),
               'albums': (album_where, ()), 'album_images': (f'album_id IN (SELECT id FROM albums WHERE {album_where})', ()),
               'tag_aliases': (f'entity_id IN ({tag_ids})', ()), 'artist_aliases': (f'entity_id IN ({artist_ids})', ()),
               'works': (f'id IN ({work_ids})', ()), 'work_images': (f'work_id IN ({work_ids})' + ('' if allowed is None else ' AND image_id IN (SELECT id FROM images WHERE library_id IN (SELECT id FROM _manifest_visible))'), ())}
    for table in ('ratings', 'favorites', 'image_workflow', 'comments', 'notes'):
        filters[table] = (f'image_id {scope} AND user_id=?', (access.user_id,))
    for table in ('image_tags', 'image_artists', 'image_sources', 'tag_claims'):
        filters[table] = ('image_id ' + scope, ())
    for table in ('saved_queries', 'play_rules'):
        filters[table] = ('owner_id=?', (access.user_id,))
    rows, remaining = {}, MAX_RECORDS
    try:
        for table, fields in COLUMNS.items():
            where, args = filters[table]
            records = [dict(row) for row in conn.execute(f"SELECT {','.join(fields)} FROM {table} WHERE {where} LIMIT ?", (*args, remaining + 1))]
            if len(records) > remaining:
                raise _error('manifest_too_large', 413)
            rows[table] = records
            remaining -= len(records)
        return rows
    finally:
        for suffix in ('libraries', 'images', 'visible'):
            conn.execute(f'DROP TABLE temp._manifest_{suffix}')


def _query_export(conn, expression, selected_albums):
    parsed = search.parse(expression)
    if parsed.errors:
        raise _error('manifest_rule_invalid')
    for clause in parsed.clauses:
        if clause['kind'] == 'album':
            for index, value in enumerate(clause['values']):
                if value.isascii() and value.isdigit():
                    if int(value) not in selected_albums:
                        raise _error('manifest_rule_outside_scope', 409)
                    clause['values'][index] = {'album': _uid(conn, 'albums', int(value))}
    return parsed.clauses


def export(conn, access, library_ids=None):
    """Export one user's personal records and shared metadata in selected libraries."""
    access.require_capability(conn, 'view')
    with db.transaction(conn):
        allowed = access.allowed_libraries(conn)
        selected = set(library_ids if library_ids is not None else
                       (allowed if allowed is not None else [row[0] for row in conn.execute('SELECT id FROM libraries')]))
        for lid in selected:
            access.require_library(conn, lid)
        rows = _export_rows(conn, access, selected, allowed)
        rows['users'] = [row for row in rows['users'] if row['id'] == access.user_id]
        if len(rows['libraries']) != len(selected):
            raise _error('library_not_found', 404)
        rows['images'] = [row for row in rows['images'] if row['library_id'] in selected]
        image_ids = {row['id'] for row in rows['images']}
        work_ids = {row['work_id'] for row in rows['work_images'] if row['image_id'] in image_ids}
        visible_ids = {row['image_id'] for row in rows['work_images']} if allowed is not None else None
        rows['works'] = [row for row in rows['works'] if row['id'] in work_ids]
        rows['work_images'] = [row for row in rows['work_images'] if row['work_id'] in work_ids and (visible_ids is None or row['image_id'] in visible_ids)]
        for work in rows['works']:
            if visible_ids is not None and work['preferred_image_id'] not in visible_ids:
                work['preferred_image_id'] = None
        for table in ('ratings', 'favorites', 'image_workflow', 'comments', 'notes'):
            rows[table] = [row for row in rows[table] if row['image_id'] in image_ids and row['user_id'] == access.user_id]
        for table in ('image_tags', 'image_artists', 'image_sources', 'tag_claims'):
            rows[table] = [row for row in rows[table] if row['image_id'] in image_ids]
        tags = {row['tag_id'] for table in ('image_tags', 'tag_claims') for row in rows[table]}
        artists = {row['artist_id'] for row in rows['image_artists']}
        rows['tags'] = [row for row in rows['tags'] if row['id'] in tags]
        rows['artists'] = [row for row in rows['artists'] if row['id'] in artists]
        for table, ids in (('tag_aliases', tags), ('artist_aliases', artists)):
            rows[table] = [row for row in rows[table] if row['entity_id'] in ids]
        members = {}
        for row in rows['album_images']:
            members.setdefault(row['album_id'], set()).add(row['image_id'])
        rows['albums'] = [row for row in rows['albums'] if
                          (row['library_id'] in selected or members.get(row['id'], set()) & image_ids)
                          and members.get(row['id'], set()) <= image_ids
                          and (row['cover_image_id'] is None or row['cover_image_id'] in image_ids)]
        albums = {row['id'] for row in rows['albums']}
        rows['album_images'] = [row for row in rows['album_images'] if row['album_id'] in albums]
        for table in ('saved_queries', 'play_rules'):
            rows[table] = [row for row in rows[table] if row['owner_id'] == access.user_id]
        for row in rows['saved_queries']:
            row['query'] = _query_export(conn, row['query'], albums)
            row['display'] = json.loads(row['display'])
        for row in rows['play_rules']:
            spec = json.loads(row['spec'])
            play.validate_spec(spec)
            spec['query'] = _query_export(conn, spec.get('query', ''), albums)
            for field, table, ids in (('library_id', 'libraries', selected), ('album_id', 'albums', albums),
                                      ('artist_id', 'artists', artists)):
                if spec.get('source', {}).get(field) is not None:
                    local_id = spec['source'][field]
                    if local_id not in ids:
                        raise _error('manifest_rule_outside_scope', 409)
                    spec['source'][field] = _uid(conn, table, local_id)
            row['spec'] = spec
        for table, records in rows.items():
            refs = dict(REFERENCES)
            if table.endswith('_aliases'):
                refs['entity_id'] = 'tags' if table == 'tag_aliases' else 'artists'
            for row in records:
                for field, target in refs.items():
                    if row.get(field) is not None:
                        # Shared record content is portable, other users' identities are not.
                        row[field] = None if target == 'users' and row[field] != access.user_id else _uid(conn, target, row[field])
                if 'id' in row:
                    row['id'] = _uid(conn, table, row['id'])
            records.sort(key=canonical)
        for pending in conn.execute('SELECT * FROM pending_work_images ORDER BY work_id,image_uid'):
            if pending['work_id'] not in work_ids:
                continue
            local_image = _resolve(conn, 'images', pending['image_uid'])
            if local_image is not None and allowed is not None:
                library = conn.execute('SELECT library_id FROM images WHERE id=?', (local_image,)).fetchone()
                if library is None or library[0] not in allowed:
                    continue
            work_uid = _uid(conn, 'works', pending['work_id'])
            rows['work_images'].append({'work_id': work_uid, 'image_id': pending['image_uid'],
                                        'role': pending['role'], 'created_at': pending['created_at']})
            if pending['preferred']:
                next(work for work in rows['works'] if work['id'] == work_uid)['preferred_image_id'] = pending['image_uid']
        rows['work_images'].sort(key=canonical)
        result = {'type': 'picshelf.metadata', 'format': FORMAT, 'exported_at': db.utcnow(),
                  'owner': _uid(conn, 'users', access.user_id), 'records': rows}
        if sum(map(len, rows.values())) > MAX_RECORDS or len(canonical(result).encode()) > MAX_BYTES:
            raise _error('manifest_too_large', 413)
        return {**result, 'checksum': checksum(result)}


def validate(value):
    if not isinstance(value, dict) or value.get('type') != 'picshelf.metadata' or type(value.get('format')) is not int or value['format'] not in (1, FORMAT):
        raise _error('manifest_version_unsupported')
    if not {'type', 'format', 'exported_at', 'owner', 'records', 'checksum'} <= value.keys():
        raise _error()
    if not isinstance(value['exported_at'], str):
        raise _error()
    metadata.timestamp(value['exported_at'])
    try:
        encoded = canonical(value).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise _error() from exc
    if len(encoded) > MAX_BYTES:
        raise _error('manifest_too_large', 413)
    if value.get('checksum') != checksum({k: v for k, v in value.items() if k != 'checksum'}):
        raise _error('manifest_checksum_mismatch')
    records = value.get('records')
    required = set(COLUMNS) - ({'works', 'work_images'} if value['format'] == 1 else set())
    if not isinstance(records, dict) or required - set(records):
        raise _error()
    records = {'works': [], 'work_images': [], **records}
    if any(not isinstance(records[table], list) for table in COLUMNS) or sum(len(records[t]) for t in COLUMNS) > MAX_RECORDS:
        raise _error('manifest_too_large', 413)
    unknown = [key for key in value if key not in {'type', 'format', 'exported_at', 'owner', 'records', 'checksum'}]
    unknown += ['records.' + key for key in records if key not in COLUMNS]
    seen = set()
    clean = {}
    for table, fields in COLUMNS.items():
        clean[table] = []
        for index, row in enumerate(records[table]):
            if not isinstance(row, dict) or set(fields) - set(row):
                raise _error()
            unknown += [f'records.{table}[{index}].{key}' for key in row if key not in fields]
            record = {key: row[key] for key in fields}
            for field, item in record.items():
                if field in REFERENCES or field in ('id', 'entity_id'):
                    nullable = field in ('added_by', 'created_by', 'confirmed_by', 'cover_image_id', 'preferred_image_id') or (table == 'albums' and field == 'library_id')
                    if item is None and not nullable:
                        raise _error()
                    if item is not None and (not isinstance(item, str) or not re.fullmatch('[0-9a-f]{32}', item)):
                        raise _error()
                elif field in ('query', 'spec', 'display'):
                    if not isinstance(item, (dict, list)):
                        raise _error()
                elif field in ('size_bytes', 'position', 'value', 'confirmed', 'deleted'):
                    if type(item) is not int or not 0 <= item <= 2**63 - 1:
                        raise _error()
                    if field in ('confirmed', 'deleted') and item not in (0, 1):
                        raise _error()
                elif item is not None and (not isinstance(item, str) or len(item) > 10000 or '\x00' in item):
                    raise _error()
                if item is not None and field in TIMES:
                    metadata.timestamp(item)
            if record.get('id'):
                if record['id'] in seen:
                    raise _error('manifest_identity_conflict', 409)
                seen.add(record['id'])
            if table == 'images':
                rel = record['rel_path']
                if not isinstance(rel, str) or not rel or PurePosixPath(rel).is_absolute() or '..' in PurePosixPath(rel).parts or '\\' in rel:
                    raise _error()
                if not isinstance(record['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', record['sha256']):
                    raise _error('manifest_hash_missing')
            if 'name' in record:
                if not isinstance(record['name'], str):
                    raise _error()
                names.display(record['name'])
            if table == 'albums' and record['kind'] not in ('folder', 'manual'):
                raise _error()
            if table == 'work_images':
                from .works import ROLES
                if record['role'] not in ROLES:
                    raise _error()
            if table in ('comments', 'notes') and (not isinstance(record['body'], str) or not record['body'].strip()):
                raise _error()
            clean[table].append(record)
    if len(clean['users']) != 1 or clean['users'][0]['id'] != value.get('owner'):
        raise _error()
    return clean, sorted(unknown)


def _fingerprint(conn):
    return checksum(conn.execute("SELECT value FROM meta WHERE key='catalog_revision'").fetchone()[0])


def _mappings(conn, access, records, mappings):
    if not isinstance(mappings, dict) or set(mappings) - {'libraries', 'users', 'images', 'tags', 'artists', 'albums'}:
        raise _error()
    found = {}
    for table, pairs in mappings.items():
        expected = {row['id'] for row in records[table]}
        if not isinstance(pairs, dict) or set(pairs) - expected or any(type(x) is not int or not 1 <= x <= 2**63-1 for x in pairs.values()):
            raise _error()
        if len(set(pairs.values())) != len(pairs):
            raise _error('manifest_identity_conflict', 409)
        for uid, local_id in pairs.items():
            if conn.execute(f'SELECT id FROM {table} WHERE id=?', (local_id,)).fetchone() is None:
                raise _error('manifest_mapping_missing', 404)
            found[(table, uid)] = local_id
    if any((table, row['id']) not in found for table in ('users', 'libraries') for row in records[table]):
        raise _error('manifest_mapping_required')
    access.require_capability(conn, 'tag_edit')
    for row in records['libraries']:
        access.require_library(conn, found['libraries', row['id']], 'edit')
    for row in records['users']:
        local_id = found['users', row['id']]
        if local_id != access.user_id:
            access.require_capability(conn, 'admin')
        if auth.get_user(conn, local_id) is None:
            raise _error('manifest_owner_unavailable', 409)
    for table, cap in (('ratings', 'rate'), ('favorites', 'rate'), ('comments', 'comment'), ('notes', 'note')):
        if records[table]:
            access.require_capability(conn, cap)
    # Reusing global entities is an explicit administrative catalog decision.
    if any(mappings.get(table) for table in ('tags', 'artists')):
        access.require_capability(conn, 'admin')
    for uid, local_id in mappings.get('albums', {}).items():
        access.require_album(conn, local_id, 'edit')
    return found


def _remember(conn, table, uid, local_id):
    existing = _resolve(conn, table, uid)
    if existing is not None and existing != local_id:
        raise _error('manifest_identity_conflict', 409)
    conn.execute('INSERT OR IGNORE INTO portable_import_ids VALUES(?,?,?)', (uid, table, local_id))


def _query_import(clauses, mapping):
    if not isinstance(clauses, list) or len(clauses) > 128:
        raise _error()
    expression = []
    for clause in clauses:
        if not isinstance(clause, dict) or set(clause) != {'kind', 'values', 'neg'} or type(clause['neg']) is not bool:
            raise _error()
        kind, values = clause['kind'], clause['values']
        if kind not in ('term', *search._KEYWORDS) or not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise _error()
        parts = []
        for value in values:
            if kind == 'album' and isinstance(value, dict) and set(value) == {'album'}:
                value = str(mapping['albums', value['album']])
            elif kind == 'album' and isinstance(value, str) and value.isascii() and value.isdigit():
                raise _error('manifest_rule_invalid')
            if not isinstance(value, str):
                raise _error()
            parts.append('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"')
        expression.append(('-' if clause['neg'] else '') + ('' if kind == 'term' else kind + ':') + ','.join(parts))
    result = ' '.join(expression)
    if search.parse(result).errors:
        raise _error('manifest_rule_invalid')
    return result


def _verify_file(conn, image, record):
    library = conn.execute('SELECT * FROM libraries WHERE id=?', (image['library_id'],)).fetchone()
    root = Path(library['root_path']).resolve(strict=True)
    stat = root.stat()
    if library['root_device'] is not None and (stat.st_dev, stat.st_ino) != (library['root_device'], library['root_inode']):
        raise _error('library_root_changed', 409)
    path = root / image['rel_path']
    if not path.resolve(strict=True).is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)):
        raise _error('manifest_file_changed', 409)
    before = path.stat()
    if before.st_size != record['size_bytes'] or image['sha256'] != record['sha256']:
        raise _error('manifest_file_changed', 409)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise _error('manifest_file_changed', 409)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or digest != record['sha256']:
        raise _error('manifest_file_changed', 409)


def _apply(conn, access, records, mappings):
    mapping = _mappings(conn, access, records, mappings)
    conflicts, targets, counts = [], [], {'created': 0, 'unchanged': 0, 'matched_images': 0}
    created_works, pending_preferences = set(), {}
    for table, rows in records.items():
        for index, source in enumerate(rows):
            uid = source.get('id')
            conn.execute('SAVEPOINT manifest_row')
            try:
                if table in ('users', 'libraries'):
                    # Accounts, grants and root registration are never imported.
                    if table == 'libraries':
                        _remember(conn, table, uid, mapping[table, uid])
                    continue
                record = dict(source)
                if table == 'works' and record['preferred_image_id'] is not None and ('images', record['preferred_image_id']) not in mapping:
                    pending_preferences[uid] = record['preferred_image_id']
                    candidate = _resolve(conn, 'images', record['preferred_image_id'])
                    if candidate is not None:
                        access.require_image(conn, candidate, 'edit', include_missing=True)
                        mapping['images', record['preferred_image_id']] = candidate
                    else:
                        record['preferred_image_id'] = None
                if table == 'work_images':
                    from . import works
                    work_id = mapping['works', source['work_id']]
                    if work_id not in created_works:
                        works._edit(conn, access, work_id)
                    if ('images', source['image_id']) not in mapping:
                        preferred = int(pending_preferences.get(source['work_id']) == source['image_id'])
                        existing = conn.execute('SELECT role,preferred FROM pending_work_images WHERE work_id=? AND image_uid=?', (work_id, source['image_id'])).fetchone()
                        if existing and tuple(existing) != (source['role'], preferred):
                            raise _error('manifest_metadata_conflict', 409)
                        conn.execute('INSERT OR IGNORE INTO pending_work_images VALUES(?,?,?,?,?,?)',
                                     (work_id, source['image_id'], source['role'], preferred, access.user_id, source['created_at']))
                        counts['unchanged' if existing else 'created'] += 1
                        continue
                    access.require_image(conn, mapping['images', source['image_id']], 'edit', include_missing=True)
                refs = dict(REFERENCES)
                if table.endswith('_aliases'):
                    refs['entity_id'] = 'tags' if table == 'tag_aliases' else 'artists'
                for field, target in refs.items():
                    if record.get(field) is not None:
                        record[field] = mapping[target, record[field]]
                target_id = mapping.get((table, uid)) or (_resolve(conn, table, uid) if uid else None)
                if table == 'works' and target_id is not None:
                    from .works import _edit
                    _edit(conn, access, target_id)
                if table == 'images':
                    if target_id is None:
                        row = conn.execute('SELECT id FROM images WHERE library_id=? AND rel_path=?',
                                           (record['library_id'], record['rel_path'])).fetchone()
                        target_id = row[0] if row else None
                    image = conn.execute('SELECT * FROM images WHERE id=?', (target_id,)).fetchone()
                    if image is None:
                        raise _error('manifest_file_missing', 409)
                    if image['library_id'] != record['library_id'] or target_id in [lid for (kind, key), lid in mapping.items() if kind == 'images' and key != uid]:
                        raise _error('manifest_identity_conflict', 409)
                    aliases = conn.execute('SELECT uid FROM portable_import_ids WHERE kind=? AND local_id=?', (table, target_id)).fetchall()
                    if aliases and uid not in {row[0] for row in aliases} and _resolve(conn, table, uid) != target_id:
                        raise _error('manifest_identity_conflict', 409)
                    access.require_image(conn, target_id, 'edit', include_missing=True)
                    _verify_file(conn, image, record)
                    targets.append({'id': uid, 'image_id': target_id, 'library_id': image['library_id'], 'rel_path': image['rel_path']})
                    counts['matched_images'] += 1
                else:
                    if (table == 'albums' and record['kind'] == 'folder' and record['rel_dir'] == ''
                            and source['name'] == next(row['name'] for row in records['libraries'] if row['id'] == source['library_id'])):
                        # The root folder label follows the explicitly mapped library name.
                        record['name'] = conn.execute('SELECT name FROM libraries WHERE id=?', (record['library_id'],)).fetchone()[0]
                    if table == 'albums' and target_id is None:
                        if record['kind'] == 'folder':
                            row = conn.execute("SELECT id FROM albums WHERE library_id=? AND kind='folder' AND rel_dir=?", (record['library_id'], record['rel_dir'])).fetchone()
                        else:
                            row = conn.execute('SELECT id FROM albums WHERE library_id IS ? AND name=?', (record['library_id'], record['name'])).fetchone()
                            if row:
                                raise _error('manifest_name_conflict', 409)
                        target_id = row[0] if row else None
                    if table == 'albums' and target_id is not None:
                        access.require_album(conn, target_id, 'edit')
                    if table == 'album_images':
                        access.require_album(conn, record['album_id'], 'edit')
                        access.require_image(conn, record['image_id'], 'edit', include_missing=True)
                    if table in ('tags', 'artists') and target_id is None and names.find(conn, 'tag' if table == 'tags' else 'artist', record['name']):
                        raise _error('manifest_name_conflict', 409)
                    if table.endswith('_aliases'):
                        kind = 'tag' if table == 'tag_aliases' else 'artist'
                        if record['match_key'] != names.match_key(record['name']) or set(names.find(conn, kind, record['name'])) - {record['entity_id']}:
                            raise _error('manifest_name_conflict', 409)
                        if target_id is None:
                            access.require_capability(conn, 'admin')
                    if table == 'albums' and record['library_id'] is None:
                        access.require_capability(conn, 'admin')
                    if table == 'image_sources':
                        if record['original_filename'] and ('/' in record['original_filename'] or '\\' in record['original_filename']):
                            raise _error()
                        if record['url']:
                            url = urlsplit(record['url'])
                            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                                raise _error()
                    if table == 'saved_queries':
                        from .saved_queries import _validate
                        record['query'] = _query_import(record['query'], mapping)
                        if _validate(record):
                            raise _error('manifest_rule_invalid')
                        record['display'] = canonical(record['display'])
                    if table == 'play_rules':
                        spec = dict(record['spec'])
                        spec['source'] = dict(spec.get('source', {}))
                        spec['query'] = _query_import(spec['query'], mapping)
                        for field, target in (('library_id', 'libraries'), ('album_id', 'albums'), ('artist_id', 'artists')):
                            if spec['source'].get(field) is not None:
                                spec['source'][field] = mapping[target, spec['source'][field]]
                        spec = play.validate_spec(spec)
                        record['spec'] = canonical(spec)
                    keys = ('id',) if uid else KEYS[table]
                    values = [target_id] if uid else [record[key] for key in keys]
                    existing = conn.execute(f"SELECT {','.join(COLUMNS[table])} FROM {table} WHERE " + ' AND '.join(key + ' IS ?' for key in keys), values).fetchone()
                    if existing is not None:
                        # Timestamps do not identify associations; later values are never overwritten.
                        ignored = TIMES | {'id'}
                        for key, value in record.items():
                            if table == 'works' and key == 'preferred_image_id' and existing[key] is None and value is not None:
                                pending = conn.execute('SELECT 1 FROM pending_work_images WHERE work_id=? AND image_uid=? AND preferred=1', (target_id, source[key])).fetchone()
                                if pending:
                                    conn.execute('UPDATE works SET preferred_image_id=? WHERE id=?', (value, target_id))
                                    continue
                            if key not in ignored and (json.loads(existing[key]) if key in ('spec', 'display') else existing[key]) != (json.loads(value) if key in ('spec', 'display') else value):
                                raise _error('manifest_metadata_conflict', 409)
                        counts['unchanged'] += 1
                    else:
                        fields = [field for field in COLUMNS[table] if field != 'id']
                        result = conn.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", [record[field] for field in fields])
                        target_id = result.lastrowid if uid else None
                        if table == 'works':
                            created_works.add(target_id)
                        counts['created'] += 1
                if uid:
                    _remember(conn, table, uid, target_id)
                    mapping[table, uid] = target_id
            except (ServiceError, sqlite3.IntegrityError, KeyError, OSError, ValueError, TypeError) as exc:
                conn.execute('ROLLBACK TO manifest_row')
                conflicts.append({'table': table, 'index': index, 'id': uid,
                                  'code': exc.code if isinstance(exc, ServiceError) else 'manifest_record_conflict'})
            finally:
                conn.execute('RELEASE manifest_row')
    from .works import resolve_pending
    resolved = resolve_pending(conn, access) if not conflicts else 0
    for work in records['works']:
        local_id = mapping.get(('works', work['id']))
        if local_id is not None and conn.execute('SELECT 1 FROM works w WHERE w.id=? AND w.preferred_image_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM work_images wi WHERE wi.work_id=w.id AND wi.image_id=w.preferred_image_id)', (local_id,)).fetchone():
            conflicts.append({'table': 'works', 'id': work['id'], 'code': 'manifest_work_preference_invalid'})
    pending = conn.execute('SELECT COUNT(*) FROM pending_work_images WHERE work_id IN (' + ','.join('?' for _ in records['works']) + ')',
                           [mapping.get(('works', work['id'])) for work in records['works']]).fetchone()[0]
    return {'counts': counts, 'conflicts': conflicts, 'images': targets,
            'pending_relations': pending, 'resolved_relations': resolved}


def preview(conn, access, manifest, mappings):
    records, unknown = validate(manifest)
    with db.transaction(conn):
        _mappings(conn, access, records, mappings)
        fingerprint = _fingerprint(conn)
        conn.execute('SAVEPOINT manifest_preview')
        try:
            result = _apply(conn, access, records, mappings)
        finally:
            conn.execute('ROLLBACK TO manifest_preview')
            conn.execute('RELEASE manifest_preview')
        result.update({'unknown_fields': unknown, 'ready': not result['conflicts'],
                       'policy': 'abort_on_conflict', 'checksum': manifest['checksum'], 'mappings': mappings})
        now = datetime.now(timezone.utc)
        plan_id = uuid.uuid4().hex
        conn.execute('INSERT INTO manifest_plans(id,created_by,created_at,expires_at,manifest,mappings,fingerprint,preview) VALUES(?,?,?,?,?,?,?,?)',
                     (plan_id, access.user_id, now.isoformat(), (now + timedelta(hours=24)).isoformat(),
                      canonical(manifest), canonical(mappings), fingerprint, canonical(result)))
    return get_plan(conn, access, plan_id)


def _plan(conn, access, plan_id):
    access.user(conn)
    row = conn.execute('SELECT * FROM manifest_plans WHERE id=? AND created_by=?', (plan_id, access.user_id)).fetchone()
    if row is None:
        raise _error('manifest_plan_not_found', 404)
    records, _ = validate(json.loads(row['manifest']))
    _mappings(conn, access, records, json.loads(row['mappings']))
    return row, records


def get_plan(conn, access, plan_id):
    row, _ = _plan(conn, access, plan_id)
    return {'id': row['id'], 'state': row['state'], 'expires_at': row['expires_at'],
            'preview': json.loads(row['preview']), 'result': json.loads(row['result']) if row['result'] else None}


def execute(conn, cfg, access, plan_id):
    row, records = _plan(conn, access, plan_id)
    mappings = json.loads(row['mappings'])
    with ExitStack() as stack:
        for library_id in sorted(set(mappings['libraries'].values())):
            root = conn.execute('SELECT root_path FROM libraries WHERE id=?', (library_id,)).fetchone()[0]
            stack.enter_context(library_write_lock(cfg, Path(root)))
        with db.transaction(conn):
            row, records = _plan(conn, access, plan_id)
            if row['state'] == 'succeeded':
                return get_plan(conn, access, plan_id)
            if row['expires_at'] < db.utcnow():
                raise _error('manifest_plan_expired', 410)
            if not json.loads(row['preview'])['ready']:
                raise _error('manifest_conflicts', 409)
            if row['fingerprint'] != _fingerprint(conn):
                raise _error('manifest_target_changed', 409)
            result = _apply(conn, access, records, mappings)
            if result['conflicts']:
                raise _error('manifest_target_changed', 409)
            conn.execute("UPDATE manifest_plans SET state='succeeded',result=? WHERE id=?", (canonical(result), plan_id))
    return get_plan(conn, access, plan_id)
