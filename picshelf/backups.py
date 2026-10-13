"""Consistent catalog archives and validated, exclusive database restore."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile

from . import __version__, db
from .config import Config
from .coordination import operation_lock
from .errors import ServiceError
from .i18n import t

MAGIC = b'PICSHELF1'
FORMAT = 2
MAX_MEMBERS = 100000
MAX_EXPANDED = 4 * 1024**3
MAX_ENCRYPTED = 256 * 1024**2


def _error(code='backup_invalid'):
    return ServiceError(code, t('口令错误或备份文件损坏') if code == 'backup_passphrase_invalid' else t('备份无法安全恢复'),
                        next_step=t('请检查口令、归档校验和与程序版本；停止所有 Pic Shelf 服务后重试'))


def _digest(path):
    with path.open('rb') as stream:
        return {'size_bytes': path.stat().st_size,
                'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def snapshot(source, destination):
    """Use SQLite's transactionally consistent copy, including committed WAL."""
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    target = sqlite3.connect(destination)
    deadline = time.monotonic() + 30
    try:
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise _error('backup_busy')
        source.backup(target, pages=256, sleep=.05, progress=progress)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise _error()
    finally:
        target.close()


def _key(passphrase, salt):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode('utf-8')))


def export(cfg, out, *, with_thumbs=False, passphrase=None):
    out = Path(out).expanduser()
    if passphrase is not None and out.suffix != '.fernet':
        out = out.with_suffix(out.suffix + '.fernet')
    if out.exists():
        raise _error('backup_destination_exists')
    with operation_lock(cfg, 'catalog', shared=True), tempfile.TemporaryDirectory(dir=cfg.data_dir) as temp:
        stage = Path(temp)
        conn = db.open_db(cfg.db_path)
        try:
            snap = stage / 'picshelf.db'
            snapshot(conn, snap)
        finally:
            conn.close()
        with sqlite3.connect(snap) as saved:
            saved.row_factory = sqlite3.Row
            meta = {'app': 'picshelf', 'version': __version__, 'format': FORMAT,
                    'schema': db.schema_version(saved), 'exported_at': db.utcnow(),
                    'contains': {'database': True, 'derivatives': with_thumbs,
                                 'originals': False, 'signing_key': False, 'configuration': False},
                    'libraries': [dict(row) for row in saved.execute('SELECT id,name FROM libraries ORDER BY id')],
                    'required_originals': [dict(row) for row in saved.execute(
                        'SELECT library_id,rel_path,sha256,size_bytes,missing FROM images ORDER BY library_id,rel_path')],
                    'files': {'picshelf.db': _digest(snap)}}
            cache = set()
            if with_thumbs:
                for row in saved.execute('SELECT thumb_rel,preview_rel FROM images'):
                    cache.update(rel for rel in row if rel)
        archive = stage / 'backup.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, 'picshelf.db')
            for rel in sorted(cache):
                path = (cfg.media_dir / rel).resolve()
                if (PurePosixPath(rel).parts[0] not in ('thumbs', 'previews') or
                        not path.is_relative_to(cfg.media_dir.resolve())):
                    raise _error('backup_cache_path_invalid')
                if path.is_file():
                    name = 'media/' + rel
                    # Copy once before hashing, so archive bytes match the checksum.
                    copied = stage / name
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, copied)
                    zf.write(copied, name)
                    meta['files'][name] = _digest(copied)
            zf.writestr('meta.json', json.dumps(meta, ensure_ascii=False, indent=2))
        artifact = archive
        if passphrase is not None:
            if archive.stat().st_size > MAX_ENCRYPTED:
                raise _error('backup_encrypted_too_large')
            from cryptography.fernet import Fernet
            salt = os.urandom(16)
            artifact = stage / 'encrypted'
            artifact.write_bytes(MAGIC + salt + Fernet(_key(passphrase, salt)).encrypt(archive.read_bytes()))
        fd, partial_name = tempfile.mkstemp(prefix='.backup-', dir=out.parent)
        partial = Path(partial_name)
        try:
            with os.fdopen(fd, 'wb') as target, artifact.open('rb') as source:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            # Publish only a complete file, without replacing an existing backup.
            os.link(partial, out)
        finally:
            partial.unlink(missing_ok=True)
    return {'path': str(out), 'size_bytes': out.stat().st_size, 'format': FORMAT}


@contextmanager
def validated(path, *, passphrase=None, temp_dir=None):
    """Extract only bounded, declared members; validate before touching live data."""
    path = Path(path)
    with tempfile.TemporaryDirectory(dir=temp_dir) as temp:
        stage = Path(temp)
        with path.open('rb') as stream:
            encrypted = stream.read(len(MAGIC)) == MAGIC
        archive = path
        if encrypted:
            if path.stat().st_size > MAX_ENCRYPTED * 2 or passphrase is None:
                raise _error('backup_passphrase_required')
            from cryptography.fernet import Fernet, InvalidToken
            raw = path.read_bytes()
            salt, token = raw[len(MAGIC):len(MAGIC)+16], raw[len(MAGIC)+16:]
            try:
                archive = stage / 'decrypted.zip'
                archive.write_bytes(Fernet(_key(passphrase, salt)).decrypt(token))
            except (InvalidToken, ValueError) as exc:
                raise _error('backup_passphrase_invalid') from exc
        try:
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                names = [item.filename for item in infos]
                if (len(names) > MAX_MEMBERS or len(set(names)) != len(names) or
                        sum(item.file_size for item in infos) > MAX_EXPANDED or
                        'picshelf.db' not in names or
                        any(item.filename == 'meta.json' and item.file_size > 20 * 1024**2 for item in infos)):
                    raise _error()
                for item in infos:
                    name = PurePosixPath(item.filename)
                    if (name.is_absolute() or '..' in name.parts or '\\' in item.filename or
                            name.as_posix() != item.filename or item.is_dir() or
                            ((item.external_attr >> 16) & 0o170000) == 0o120000 or
                            (item.filename not in ('meta.json', 'picshelf.db') and
                             (len(name.parts) < 4 or name.parts[:2] not in (('media', 'thumbs'), ('media', 'previews')) or name.suffix != '.webp'))):
                        raise _error('backup_path_invalid')
                    destination = stage / item.filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(item) as source, destination.open('xb') as target:
                        shutil.copyfileobj(source, target)
                meta = (json.loads((stage / 'meta.json').read_text(encoding='utf-8'))
                        if 'meta.json' in names else {'app': 'picshelf'})
                if not isinstance(meta, dict) or meta.get('app') != 'picshelf':
                    raise _error()
                version = meta.get('format', 1)
                if type(version) is not int or version not in (1, FORMAT):
                    raise _error('backup_version_unsupported')
                if version == FORMAT:
                    files = meta.get('files')
                    if not isinstance(files, dict) or set(files) != set(names) - {'meta.json'}:
                        raise _error('backup_checksum_mismatch')
                    if any(files[name] != _digest(stage / name) for name in files):
                        raise _error('backup_checksum_mismatch')
            conn = sqlite3.connect(stage / 'picshelf.db')
            try:
                schema = db.validate_schema(conn)
                if version == 1 and 'schema' not in meta:
                    meta['schema'] = schema
                if type(meta.get('schema')) is not int or meta['schema'] != schema:
                    raise _error('backup_schema_mismatch')
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
                    raise _error('backup_database_invalid')
            finally:
                conn.close()
        except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, ValueError, UnicodeError) as exc:
            raise _error() from exc
        yield stage, meta


def inspect(path, *, passphrase=None):
    with validated(path, passphrase=passphrase) as (_, meta):
        return {**meta, 'validated': True, 'legacy_without_checksums': meta.get('format', 1) == 1}


def restore(cfg, path, *, force=False, passphrase=None):
    # Validation is read-only with respect to the current installation.
    with validated(path, passphrase=passphrase, temp_dir=cfg.data_dir) as (stage, meta):
        with operation_lock(cfg, 'catalog'):
            if cfg.db_path.exists() and not force:
                raise _error('backup_confirmation_required')
            source = db.open_db(stage / 'picshelf.db')
            target = None
            try:
                target = sqlite3.connect(cfg.db_path)
                safety = None
                maximum = source.execute('SELECT COALESCE(MAX(token_version),0) FROM users').fetchone()[0]
                if cfg.db_path.stat().st_size:
                    safety = cfg.db_path.with_name(f'picshelf.db.bak-{time.time_ns()}')
                    snapshot(target, safety)
                    current = db.validate_schema(target)
                    if current >= 7:
                        maximum = max(maximum, target.execute('SELECT COALESCE(MAX(token_version),0) FROM users').fetchone()[0])
                source.execute('UPDATE users SET token_version=?', (maximum + 1,))
                source.execute("UPDATE jobs SET state='interrupted',phase='restored',updated_at=? WHERE state IN ('queued','running','cancelling')", (db.utcnow(),))
                source.execute("UPDATE import_plans SET state='interrupted' WHERE state IN ('running','cancelling')")
                source.commit()
                prefix = 'restored-' + uuid.uuid4().hex
                for row in source.execute('SELECT id,thumb_rel,preview_rel FROM images').fetchall():
                    for field in ('thumb_rel', 'preview_rel'):
                        rel = row[field]
                        if not rel:
                            continue
                        parts = PurePosixPath(rel).parts
                        if (not parts or parts[0] not in ('thumbs', 'previews') or '..' in parts
                                or '\\' in rel):
                            raise _error('backup_cache_path_invalid')
                        if not (stage / 'media' / rel).is_file():
                            continue
                        restored = Path(parts[0]) / prefix / Path(*parts[1:])
                        destination = cfg.media_dir / restored
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with (stage / 'media' / rel).open('rb') as saved, destination.open('xb') as output:
                            shutil.copyfileobj(saved, output)
                            output.flush()
                            os.fsync(output.fileno())
                        source.execute(f'UPDATE images SET {field}=? WHERE id=?', (restored.as_posix(), row['id']))
                source.commit()
                # A failed backup operation rolls back its destination transaction.
                source.backup(target)
            except (sqlite3.Error, OSError) as exc:
                raise _error('backup_restore_failed') from exc
            finally:
                source.close()
                if target is not None:
                    target.close()
    return {'restored': True, 'format': meta.get('format', 1),
            'safety_backup': str(safety) if safety else None, 'sessions_invalidated': True,
            'originals_included': False, 'derivatives_included': meta.get('contains', {}).get('derivatives', False)}
