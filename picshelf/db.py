"""SQLite 数据层：连接、PRAGMA、版本化迁移。

schema 变更规则：在 MIGRATIONS 里追加新版本块并把 SCHEMA_VERSION 加一，
禁止修改已发布版本的块（迁移是顺序执行的历史，不是当前态快照）。
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import ServiceError
from .i18n import t

SCHEMA_VERSION = 11

_SCHEMA_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  capabilities TEXT NOT NULL DEFAULT '["view","rate","comment","note","tag_edit"]',
  is_system INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE user_groups (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, group_id)
);

CREATE TABLE libraries (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL,
  description TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE library_grants (
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  access TEXT NOT NULL DEFAULT 'view',
  PRIMARY KEY (group_id, library_id)
);

CREATE TABLE albums (
  id INTEGER PRIMARY KEY,
  library_id INTEGER REFERENCES libraries(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'folder',
  rel_dir TEXT,
  category TEXT,
  cover_image_id INTEGER,
  description TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (library_id, rel_dir)
);

CREATE TABLE images (
  id INTEGER PRIMARY KEY,
  library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  ext TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  width INTEGER,
  height INTEGER,
  mtime REAL NOT NULL,
  sha256 TEXT,
  phash TEXT,
  thumb_rel TEXT,
  preview_rel TEXT,
  missing INTEGER NOT NULL DEFAULT 0,
  imported_at TEXT NOT NULL,
  UNIQUE (library_id, rel_path)
);

CREATE TABLE album_images (
  album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL,
  PRIMARY KEY (album_id, image_id)
);

CREATE TABLE artists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  aliases TEXT,
  links TEXT,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE image_artists (
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
  PRIMARY KEY (image_id, artist_id)
);

CREATE TABLE tags (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  namespace TEXT,
  category TEXT,
  description TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE image_tags (
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  added_by INTEGER,
  added_at TEXT NOT NULL,
  PRIMARY KEY (image_id, tag_id)
);

CREATE TABLE ratings (
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  value INTEGER NOT NULL CHECK (value >= 0 AND value <= 10),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (image_id, user_id)
);

CREATE TABLE favorites (
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY (image_id, user_id)
);

CREATE TABLE comments (
  id INTEGER PRIMARY KEY,
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE notes (
  id INTEGER PRIMARY KEY,
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  geometry TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE play_sessions (
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  spec TEXT NOT NULL,
  order_mode TEXT NOT NULL DEFAULT 'sequential',
  interval INTEGER NOT NULL DEFAULT 0,
  loop INTEGER NOT NULL DEFAULT 0,
  cursor INTEGER NOT NULL DEFAULT -1,
  created_at TEXT NOT NULL
);

CREATE TABLE play_items (
  session_id INTEGER NOT NULL REFERENCES play_sessions(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  image_id INTEGER NOT NULL,
  PRIMARY KEY (session_id, position)
);

CREATE TABLE scans (
  id INTEGER PRIMARY KEY,
  library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  added INTEGER NOT NULL DEFAULT 0,
  updated INTEGER NOT NULL DEFAULT 0,
  moved INTEGER NOT NULL DEFAULT 0,
  missing INTEGER NOT NULL DEFAULT 0,
  unchanged INTEGER NOT NULL DEFAULT 0,
  errors TEXT
);

CREATE INDEX idx_images_lib ON images(library_id, missing);
CREATE INDEX idx_images_sha ON images(sha256);
CREATE INDEX idx_images_phash ON images(phash);
CREATE INDEX idx_albums_lib ON albums(library_id, kind);
CREATE INDEX idx_album_images_image ON album_images(image_id);
CREATE INDEX idx_image_tags_tag ON image_tags(tag_id);
CREATE INDEX idx_image_artists_artist ON image_artists(artist_id);
CREATE INDEX idx_ratings_image ON ratings(image_id);
CREATE INDEX idx_comments_image ON comments(image_id, deleted);
CREATE INDEX idx_notes_image ON notes(image_id, deleted);
"""

_SCHEMA_V2 = """
CREATE TABLE import_plans (
  id TEXT PRIMARY KEY,
  library_id INTEGER NOT NULL REFERENCES libraries(id),
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'preview',
  started_at TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  result TEXT
);
CREATE TABLE import_plan_items (
  plan_id TEXT NOT NULL REFERENCES import_plans(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  destination TEXT,
  state TEXT NOT NULL DEFAULT 'pending',
  source_identity TEXT,
  destination_identity TEXT,
  actual_action TEXT,
  error_code TEXT,
  detail TEXT,
  PRIMARY KEY(plan_id, position)
);
CREATE INDEX idx_import_plans_owner ON import_plans(created_by, created_at);
CREATE INDEX idx_import_plan_dest ON import_plan_items(destination, state);
"""

_SCHEMA_V3 = """
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  library_id INTEGER NOT NULL REFERENCES libraries(id),
  created_by INTEGER NOT NULL REFERENCES users(id),
  local_admin INTEGER NOT NULL DEFAULT 0,
  request_key TEXT NOT NULL,
  params TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued',
  phase TEXT NOT NULL DEFAULT 'queued',
  total INTEGER,
  completed INTEGER NOT NULL DEFAULT 0,
  succeeded INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  result TEXT,
  error TEXT,
  UNIQUE(created_by, request_key)
);
CREATE TABLE job_items (
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  error_code TEXT,
  detail TEXT,
  PRIMARY KEY(job_id, item_key)
);
CREATE INDEX idx_jobs_queue ON jobs(state, created_at);
CREATE INDEX idx_jobs_owner ON jobs(created_by, created_at);
"""

_SCHEMA_V4 = """
ALTER TABLE libraries ADD COLUMN root_device INTEGER;
ALTER TABLE libraries ADD COLUMN root_inode INTEGER;
ALTER TABLE images ADD COLUMN phash_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN derivative_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN scan_error_code TEXT;
ALTER TABLE images ADD COLUMN scan_error_detail TEXT;
ALTER TABLE images ADD COLUMN verified_at TEXT;
CREATE INDEX idx_images_encoding_version ON images(library_id, derivative_version, phash_version);
"""

_SCHEMA_V5 = """
ALTER TABLE libraries ADD COLUMN mode TEXT CHECK(mode IN ('indexed','managed'));
ALTER TABLE libraries ADD COLUMN default_import_mode TEXT NOT NULL DEFAULT 'hardlink'
  CHECK(default_import_mode IN ('hardlink','copy','move'));
"""

_SCHEMA_V6 = """
ALTER TABLE import_plan_items ADD COLUMN completed_at TEXT;
CREATE TABLE image_sources (
 id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
 origin TEXT NOT NULL CHECK(origin IN ('manual','imported','extracted')),
 original_filename TEXT, collected_at TEXT, created_time TEXT, url TEXT, note TEXT,
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, event_key TEXT UNIQUE
);
CREATE INDEX idx_image_sources_image ON image_sources(image_id);
CREATE TABLE image_workflow (
 image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 state TEXT NOT NULL CHECK(state IN ('inbox','done')), updated_at TEXT NOT NULL,
 PRIMARY KEY(image_id,user_id)
);
CREATE INDEX idx_workflow_user_state ON image_workflow(user_id,state,image_id);
CREATE TABLE tag_claims (
 id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
 tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
 origin TEXT NOT NULL CHECK(origin IN ('manual','imported','suggested')),
 confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)), source_ref TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
 confirmed_by INTEGER REFERENCES users(id), confirmed_at TEXT, retired_at TEXT
);
CREATE UNIQUE INDEX idx_tag_claim_active ON tag_claims(image_id,tag_id,origin,source_ref) WHERE retired_at IS NULL;
INSERT INTO tag_claims(image_id,tag_id,origin,confirmed,created_by,created_at,confirmed_by,confirmed_at)
 SELECT image_id,tag_id,'manual',1,CASE WHEN added_by IN (SELECT id FROM users) THEN added_by END,
 added_at,CASE WHEN added_by IN (SELECT id FROM users) THEN added_by END,added_at FROM image_tags;
CREATE TABLE tag_aliases (
 id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
 name TEXT NOT NULL, match_key TEXT NOT NULL, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(entity_id,match_key)
);
CREATE INDEX idx_tag_alias_match ON tag_aliases(match_key);
CREATE TABLE artist_aliases (
 id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
 name TEXT NOT NULL, match_key TEXT NOT NULL, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(entity_id,match_key)
);
CREATE INDEX idx_artist_alias_match ON artist_aliases(match_key);
CREATE TABLE entity_edit_plans (
 id TEXT PRIMARY KEY, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT
);
"""

_SCHEMA_V7 = """
ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0;
CREATE TABLE revoked_tokens (digest TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
CREATE INDEX idx_revoked_expiry ON revoked_tokens(expires_at);
CREATE TABLE login_attempts (bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL);
CREATE TABLE import_source_roots (
 id INTEGER PRIMARY KEY, root_path TEXT NOT NULL UNIQUE, device INTEGER NOT NULL, inode INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, allow_move INTEGER NOT NULL DEFAULT 0 CHECK(allow_move IN (0,1)),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
"""

_SCHEMA_V8 = """
CREATE TABLE metadata_versions (
 image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
 field_key TEXT NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(image_id,field_key)
);
CREATE TABLE metadata_batches (
 id TEXT PRIMARY KEY, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, request_key TEXT NOT NULL, request TEXT NOT NULL, action TEXT NOT NULL,
 selection TEXT NOT NULL, target_snapshot TEXT NOT NULL, local_admin INTEGER NOT NULL DEFAULT 0, resolved TEXT,
 state TEXT NOT NULL DEFAULT 'preview', phase TEXT NOT NULL DEFAULT 'apply', cancel_requested INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL, error_code TEXT, executed_by INTEGER REFERENCES users(id),
 UNIQUE(created_by,request_key)
);
CREATE INDEX idx_batch_queue ON metadata_batches(state,created_at);
CREATE TABLE metadata_batch_items (
 batch_id TEXT NOT NULL REFERENCES metadata_batches(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, image_id INTEGER NOT NULL REFERENCES images(id),
 before_value TEXT NOT NULL, before_version INTEGER NOT NULL, after_value TEXT, after_version INTEGER,
 state TEXT NOT NULL DEFAULT 'pending', error_code TEXT, PRIMARY KEY(batch_id,position), UNIQUE(batch_id,image_id)
);
"""

# Record field revisions at the storage boundary, including writes from legacy
# API/CLI entry points. Comparing revisions prevents an edit-and-revert from
# being mistaken for an untouched batch result.
for _table, _key in (
    ("ratings", "'rating:' || {row}.user_id"),
    ("favorites", "'favorite:' || {row}.user_id"),
    ("image_workflow", "'workflow:' || {row}.user_id"),
    ("image_tags", "'tags'"), ("tag_claims", "'tags'"),
    ("image_artists", "'artists'"), ("album_images", "'album:' || {row}.album_id"),
):
    for _event, _row in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        _SCHEMA_V8 += f"""
CREATE TRIGGER metadata_version_{_table}_{_event.lower()} AFTER {_event} ON {_table}
BEGIN
 INSERT INTO metadata_versions(image_id,field_key,version)
 SELECT {_row}.image_id,{_key.format(row=_row)},1 WHERE EXISTS(SELECT 1 FROM images WHERE id={_row}.image_id)
 ON CONFLICT(image_id,field_key) DO UPDATE SET version=version+1;
END;
"""

_SCHEMA_V9 = """
CREATE TABLE saved_queries (
 id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('query','smart')),
 query TEXT NOT NULL, sort TEXT NOT NULL, display TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_saved_query_owner ON saved_queries(owner_id,updated_at,id);
"""

_SCHEMA_V10 = """
CREATE TABLE play_rules (
 id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, spec TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_play_rule_owner ON play_rules(owner_id,updated_at,id);
ALTER TABLE play_sessions ADD COLUMN rule_id INTEGER REFERENCES play_rules(id) ON DELETE SET NULL;
ALTER TABLE play_sessions ADD COLUMN snapshot_info TEXT NOT NULL DEFAULT '{}';
ALTER TABLE play_sessions ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;
ALTER TABLE play_sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE play_sessions ADD COLUMN cycle INTEGER NOT NULL DEFAULT 0;
CREATE TABLE play_candidates (
 session_id INTEGER NOT NULL REFERENCES play_sessions(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, image_id INTEGER NOT NULL, library_id INTEGER,
 PRIMARY KEY(session_id,position)
);
INSERT INTO play_candidates SELECT p.session_id,p.position,p.image_id,i.library_id
 FROM play_items p LEFT JOIN images i ON i.id=p.image_id;
CREATE TABLE play_requests (
 session_id INTEGER NOT NULL REFERENCES play_sessions(id) ON DELETE CASCADE,
 request_key TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(session_id,request_key)
);
"""

_SCHEMA_V11 = """
CREATE TABLE portable_ids (
 kind TEXT NOT NULL, local_id INTEGER NOT NULL, uid TEXT NOT NULL UNIQUE,
 PRIMARY KEY(kind,local_id)
);
CREATE TABLE portable_import_ids (
 uid TEXT PRIMARY KEY, kind TEXT NOT NULL, local_id INTEGER NOT NULL,
 FOREIGN KEY(kind,local_id) REFERENCES portable_ids(kind,local_id) ON DELETE CASCADE
);
CREATE TABLE manifest_plans (
 id TEXT PRIMARY KEY, created_by INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'preview',
 manifest TEXT NOT NULL, mappings TEXT NOT NULL, fingerprint TEXT NOT NULL,
 preview TEXT NOT NULL, result TEXT
);
INSERT INTO meta(key,value) VALUES('catalog_revision','0');
"""
for _portable_table in ('users', 'libraries', 'images', 'tags', 'artists', 'albums',
                        'comments', 'notes', 'image_sources', 'tag_claims',
                        'tag_aliases', 'artist_aliases', 'saved_queries', 'play_rules'):
    _SCHEMA_V11 += f"""
INSERT INTO portable_ids(kind,local_id,uid) SELECT '{_portable_table}',id,lower(hex(randomblob(16))) FROM {_portable_table};
CREATE TRIGGER portable_{_portable_table}_insert AFTER INSERT ON {_portable_table}
BEGIN
 INSERT INTO portable_ids(kind,local_id,uid) VALUES('{_portable_table}',NEW.id,lower(hex(randomblob(16))));
END;
CREATE TRIGGER portable_{_portable_table}_delete AFTER DELETE ON {_portable_table}
BEGIN
 DELETE FROM portable_ids WHERE kind='{_portable_table}' AND local_id=OLD.id;
END;
"""
for _catalog_table in ('users', 'libraries', 'images', 'tags', 'artists', 'albums',
                       'comments', 'notes', 'image_sources', 'tag_claims',
                       'tag_aliases', 'artist_aliases', 'saved_queries', 'play_rules',
                       'image_tags', 'image_artists', 'album_images', 'ratings',
                       'favorites', 'image_workflow'):
    for _catalog_action in ('INSERT', 'UPDATE', 'DELETE'):
        _catalog_event = ('UPDATE OF username,display_name' if _catalog_table == 'users'
                          and _catalog_action == 'UPDATE' else _catalog_action)
        _SCHEMA_V11 += f"""
CREATE TRIGGER catalog_{_catalog_table}_{_catalog_action.lower()} AFTER {_catalog_event} ON {_catalog_table}
BEGIN
 UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='catalog_revision';
END;
"""

MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3, 4: _SCHEMA_V4, 5: _SCHEMA_V5, 6: _SCHEMA_V6, 7: _SCHEMA_V7, 8: _SCHEMA_V8, 9: _SCHEMA_V9, 10: _SCHEMA_V10, 11: _SCHEMA_V11}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：FastAPI 的同步依赖与端点在同一个线程池的“不同工作线程”里
    # 依次执行（依赖建连 → 端点使用），必须允许跨线程顺序使用；并发请求各持独立连接，
    # WAL + busy_timeout 保证互不干扰。
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from .names import match_key
    conn.create_function("picshelf_name_key", 1, match_key, deterministic=True)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        # Validate before changing persistent journal settings in an existing file.
        validate_schema(conn)
        deadline = time.monotonic() + 10
        while True:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError as exc:
                if not _busy(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn
    except BaseException:
        conn.close()
        raise


def _invalid_schema() -> ServiceError:
    return ServiceError("invalid_schema", t("数据库版本记录或结构无效"), status=503,
                        next_step=t("请检查数据目录，或停止服务后恢复有效备份"))


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if "meta" not in tables:
            if tables:
                raise _invalid_schema()
            return 0
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        value = str(row[0]) if row else ""
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise _invalid_schema()
        return int(value)
    except sqlite3.DatabaseError as exc:
        if _busy(exc):
            raise
        raise _invalid_schema() from exc


def validate_schema(conn: sqlite3.Connection) -> int:
    """Validate a database without migration or changes to persistent settings."""
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise ServiceError("unsupported_schema", t("数据库来自更新版本的 Pic Shelf"), status=503,
                           next_step=t("请使用支持此数据库的新版程序，或停止服务后恢复升级前快照"))
    if current:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"meta", "users", "groups", "user_groups", "libraries", "library_grants",
                    "images", "albums", "album_images", "tags", "image_tags", "artists",
                    "image_artists", "ratings", "favorites", "comments", "notes",
                    "play_sessions", "play_items", "scans"}
        if current >= 2:
            required |= {"import_plans", "import_plan_items"}
        if current >= 3:
            required |= {"jobs", "job_items"}
        if not required <= tables:
            raise _invalid_schema()
    return current


def _busy(exc: sqlite3.Error) -> bool:
    return (getattr(exc, "sqlite_errorcode", 0) & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _snapshot_before_migration(conn: sqlite3.Connection, current: int) -> Path | None:
    """The caller holds the writer lock; a second reader captures committed WAL."""
    filename = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    if not filename:  # An in-memory database has no durable state to restore.
        return None
    directory = Path(filename).parent / "migration-backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    destination = directory / f"schema-v{current}-to-v{SCHEMA_VERSION}-{time.time_ns()}.db"
    partial = destination.with_suffix(".partial")
    descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source = target = None
    try:
        source = sqlite3.connect(Path(filename).as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(partial)
        deadline = time.monotonic() + 30

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("snapshot timeout")

        source.backup(target, pages=256, progress=progress, sleep=.05)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("snapshot integrity check failed")
        target.close()
        target = None
        with partial.open("rb") as saved:
            os.fsync(saved.fileno())
        os.replace(partial, destination)
        return destination
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
        partial.unlink(missing_ok=True)


def _execute_migration(conn: sqlite3.Connection, script: str) -> None:
    # executescript() commits an existing transaction, so execute complete SQL
    # statements separately. SQLite recognizes trigger bodies and quoted ';'.
    forbidden = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                 sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA}
    def authorize(action, arg1, arg2, database, source):
        # ADD COLUMN with CHECK invokes a read-only quick_check internally.
        # Continue to reject PRAGMAs that can alter transaction/durability rules.
        if action == sqlite3.SQLITE_PRAGMA and arg1 == "quick_check":
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    try:
        start = 0
        for end, char in enumerate(script):
            if char == ";" and sqlite3.complete_statement(script[start:end + 1]):
                conn.execute(script[start:end + 1])
                start = end + 1
        if script[start:].strip():
            conn.execute(script[start:])
    finally:
        conn.set_authorizer(None)


def migrate(conn: sqlite3.Connection) -> int:
    """Atomically apply all pending versions, preserving a pre-upgrade snapshot."""
    if conn.in_transaction:
        raise ServiceError("transaction_active", t("迁移不能在未完成的事务中运行"),
                           next_step=t("请先提交或回滚当前操作，再重新打开数据库"))
    current = validate_schema(conn)
    if current == SCHEMA_VERSION:
        return current
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Another process may have finished the upgrade while this one waited.
        current = validate_schema(conn)
        pending = range(current + 1, SCHEMA_VERSION + 1)
        if any(version not in MIGRATIONS for version in pending):
            raise ServiceError("migration_unavailable", t("安装包缺少所需的数据库迁移"), status=503,
                               next_step=t("请重新安装完整的 Pic Shelf 软件包"))
        if current and current < SCHEMA_VERSION:
            _snapshot_before_migration(conn, current)
        for version in pending:
            _execute_migration(conn, MIGRATIONS[version])
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),))
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("foreign key check failed")
        validate_schema(conn)
        conn.commit()
        return SCHEMA_VERSION
    except BaseException as exc:
        conn.rollback()
        if isinstance(exc, (sqlite3.Error, OSError)):
            raise ServiceError("migration_failed", t("数据库迁移未完成，原有结构与数据已保留"),
                               status=503, retryable=isinstance(exc, sqlite3.Error) and _busy(exc),
                               next_step=t("请停止其他写入并检查可用空间；必要时从数据目录的 migration-backups 恢复快照")) from exc
        raise


def open_db(db_path: Path) -> sqlite3.Connection:
    """Connect + migrate. Use everywhere a fresh handle is needed."""
    conn = None
    try:
        conn = connect(db_path)
        migrate(conn)
        return conn
    except BaseException as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, (sqlite3.Error, OSError)):
            busy = isinstance(exc, sqlite3.Error) and _busy(exc)
            raise ServiceError("database_busy" if busy else "database_unavailable",
                               t("无法打开数据库"), status=503, retryable=busy,
                               next_step=t("请停止其他写入并检查数据目录权限和可用空间，然后重试")) from exc
        raise
