"""Manual work membership; each file retains its own identity and organization."""
import sqlite3

from . import db, names
from .errors import ServiceError
from .i18n import t

ROLES = ('original', 'compressed', 'crop', 'resize', 'revision', 'other')


def _error(code='work_invalid', status=400):
    return ServiceError(code, t('作品关联无法完成'), status=status,
                        next_step=t('请检查图片权限与关系类型；先解除错误关联，再重新关联；首选图片必须属于该作品'))


def get(conn, access, work_id):
    access.require_capability(conn, 'view')
    row = conn.execute('SELECT * FROM works WHERE id=?', (work_id,)).fetchone()
    if row is None:
        raise _error('work_not_found', 404)
    images = []
    for member in conn.execute('SELECT wi.role,i.* FROM work_images wi JOIN images i ON i.id=wi.image_id WHERE wi.work_id=? ORDER BY i.id', (work_id,)):
        try:
            access.require_image(conn, member['id'], include_missing=True)
        except ServiceError:
            continue
        images.append({'id': member['id'], 'library_id': member['library_id'], 'filename': member['filename'],
                       'role': member['role'], 'missing': bool(member['missing'])})
    if not images:
        has_members = conn.execute('SELECT 1 FROM work_images WHERE work_id=?', (work_id,)).fetchone()
        if has_members or (row['created_by'] != access.user_id and not access.user(conn)['is_admin'] and not access.local_admin):
            raise _error('work_not_found', 404)
    preferred = row['preferred_image_id'] if row['preferred_image_id'] in {image['id'] for image in images} else None
    return {'id': row['id'], 'name': row['name'], 'images': images,
            'preferred_image_id': preferred, 'view_image_id': preferred or next((image['id'] for image in images if not image['missing']), None)}


def by_image(conn, access, image_id):
    access.require_image(conn, image_id, include_missing=True)
    row = conn.execute('SELECT work_id FROM work_images WHERE image_id=?', (image_id,)).fetchone()
    return get(conn, access, row[0]) if row else None


def _edit(conn, access, work_id):
    access.require_capability(conn, 'tag_edit')
    value = get(conn, access, work_id)
    for row in conn.execute('SELECT image_id FROM work_images WHERE work_id=?', (work_id,)):
        access.require_image(conn, row[0], 'edit', include_missing=True)
    return value


def _members(conn, access, members):
    access.require_capability(conn, 'tag_edit')
    if not isinstance(members, list) or not 1 <= len(members) <= 1000:
        raise _error()
    seen = set()
    for member in members:
        if (not isinstance(member, dict) or set(member) != {'image_id', 'role'} or
                type(member['image_id']) is not int or not 1 <= member['image_id'] <= 2**63-1 or
                member['role'] not in ROLES or member['image_id'] in seen):
            raise _error()
        access.require_image(conn, member['image_id'], 'edit', include_missing=True)
        seen.add(member['image_id'])


def create(conn, access, name, members, preferred=None):
    name = names.display(name)
    with db.transaction(conn):
        _members(conn, access, members)
        if preferred is not None and (type(preferred) is not int or preferred not in {item['image_id'] for item in members}):
            raise _error()
        work_id = conn.execute('INSERT INTO works(name,created_by,created_at) VALUES(?,?,?)', (name, access.user_id, db.utcnow())).lastrowid
        try:
            conn.executemany('INSERT INTO work_images VALUES(?,?,?,?)', [(work_id, item['image_id'], item['role'], db.utcnow()) for item in members])
        except sqlite3.IntegrityError as exc:
            raise _error('image_already_linked', 409) from exc
        conn.execute('UPDATE works SET preferred_image_id=? WHERE id=?', (preferred, work_id))
    return get(conn, access, work_id)


def link(conn, access, work_id, members):
    with db.transaction(conn):
        _edit(conn, access, work_id)
        _members(conn, access, members)
        for item in members:
            existing = conn.execute('SELECT work_id FROM work_images WHERE image_id=?', (item['image_id'],)).fetchone()
            if existing and existing[0] != work_id:
                raise _error('image_already_linked', 409)
            conn.execute('INSERT INTO work_images VALUES(?,?,?,?) ON CONFLICT(work_id,image_id) DO UPDATE SET role=excluded.role',
                         (work_id, item['image_id'], item['role'], db.utcnow()))
    return get(conn, access, work_id)


def unlink(conn, access, work_id, image_id):
    with db.transaction(conn):
        _edit(conn, access, work_id)
        if conn.execute('SELECT 1 FROM work_images WHERE work_id=? AND image_id=?', (work_id, image_id)).fetchone() is None:
            raise _error('work_member_not_found', 404)
        conn.execute('DELETE FROM work_images WHERE work_id=? AND image_id=?', (work_id, image_id))
    return get(conn, access, work_id)


def prefer(conn, access, work_id, image_id):
    with db.transaction(conn):
        _edit(conn, access, work_id)
        if type(image_id) is not int or conn.execute('SELECT 1 FROM work_images WHERE work_id=? AND image_id=?', (work_id, image_id)).fetchone() is None:
            raise _error('work_member_not_found', 404)
        conn.execute('UPDATE works SET preferred_image_id=? WHERE id=?', (image_id, work_id))
        conn.execute('UPDATE pending_work_images SET preferred=0 WHERE work_id=?', (work_id,))
    return get(conn, access, work_id)


def pending(conn, access, work_id):
    _edit(conn, access, work_id)
    return {'work_id': work_id, 'items': [dict(row) for row in conn.execute(
        'SELECT image_uid,role,preferred,created_at FROM pending_work_images WHERE work_id=? ORDER BY image_uid', (work_id,))]}


def drop_pending(conn, access, work_id, image_uid):
    with db.transaction(conn):
        _edit(conn, access, work_id)
        conn.execute('DELETE FROM pending_work_images WHERE work_id=? AND image_uid=?', (work_id, image_uid))
    return pending(conn, access, work_id)


def resolve_pending(conn, access):
    """Resolve stable identities only; preserve later choices and inaccessible files."""
    from .manifest import _resolve
    resolved = 0
    for row in conn.execute('SELECT * FROM pending_work_images ORDER BY work_id,image_uid').fetchall():
        image_id = _resolve(conn, 'images', row['image_uid'])
        if image_id is None:
            continue
        try:
            _edit(conn, access, row['work_id'])
            access.require_image(conn, image_id, 'edit', include_missing=True)
        except ServiceError:
            continue
        existing = conn.execute('SELECT work_id,role FROM work_images WHERE image_id=?', (image_id,)).fetchone()
        if existing and (existing['work_id'], existing['role']) != (row['work_id'], row['role']):
            continue
        conn.execute('INSERT OR IGNORE INTO work_images VALUES(?,?,?,?)', (row['work_id'], image_id, row['role'], row['created_at']))
        if row['preferred']:
            conn.execute('UPDATE works SET preferred_image_id=? WHERE id=? AND preferred_image_id IS NULL', (image_id, row['work_id']))
        conn.execute('DELETE FROM pending_work_images WHERE work_id=? AND image_uid=?', (row['work_id'], row['image_uid']))
        resolved += 1
    return resolved
