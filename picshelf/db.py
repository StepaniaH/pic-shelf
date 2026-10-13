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

SCHEMA_VERSION = 4

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

MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3, 4: _SCHEMA_V4}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：FastAPI 的同步依赖与端点在同一个线程池的“不同工作线程”里
    # 依次执行（依赖建连 → 端点使用），必须允许跨线程顺序使用；并发请求各持独立连接，
    # WAL + busy_timeout 保证互不干扰。
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
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
    conn.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK)
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
