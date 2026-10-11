"""SQLite 数据层：连接、PRAGMA、版本化迁移。

schema 变更规则：在 MIGRATIONS 里追加新版本块并把 SCHEMA_VERSION 加一，
禁止修改已发布版本的块（迁移是顺序执行的历史，不是当前态快照）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

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

MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：FastAPI 的同步依赖与端点在同一个线程池的“不同工作线程”里
    # 依次执行（依赖建连 → 端点使用），必须允许跨线程顺序使用；并发请求各持独立连接，
    # WAL + busy_timeout 保证互不干扰。
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; returns final version."""
    current = schema_version(conn)
    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        conn.executescript(MIGRATIONS[version])
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(version),),
        )
        conn.commit()
        current = version
    return current


def open_db(db_path: Path) -> sqlite3.Connection:
    """Connect + migrate. Use everywhere a fresh handle is needed."""
    conn = connect(db_path)
    migrate(conn)
    return conn
