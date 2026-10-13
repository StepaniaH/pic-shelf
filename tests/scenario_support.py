"""Real entry points and catalog setup shared by behavioral tests."""
from __future__ import annotations

import contextlib
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import uvicorn

from picshelf import auth, db, indexer, libsvc, ops
from picshelf.api import create_app
from picshelf.config import Config
from tests.datasets import CATALOG, catalog_files


@dataclass
class Catalog:
    cfg: Config
    roots: Path
    libraries: dict
    users: dict
    images: dict
    password: str = field(repr=False)


def build_catalog(root: Path) -> Catalog:
    cfg = Config(root / "data").ensure()
    roots = root / "originals"
    catalog_files(roots)
    conn = db.open_db(cfg.db_path)
    try:
        auth.ensure_default_groups(conn)
        password = secrets.token_urlsafe(24)
        users = {name: auth.create_user(conn, name, password, is_admin=spec["admin"])
                 for name, spec in CATALOG["users"].items()}
        libraries = {name: libsvc.create_library(conn, name, str(roots / name))
                     for name in ("north", "south")}
        # Permissions come from the manifest, never from visibility query output.
        conn.execute("DELETE FROM library_grants")
        for name, spec in CATALOG["users"].items():
            gid = conn.execute("INSERT INTO groups(name,created_at) VALUES(?,?)",
                               (name, db.utcnow())).lastrowid
            conn.execute("DELETE FROM user_groups WHERE user_id=?", (users[name],))
            conn.execute("INSERT INTO user_groups VALUES(?,?)", (users[name], gid))
            for library in spec["libraries"]:
                conn.execute("INSERT INTO library_grants VALUES(?,?,'view')", (gid, libraries[library]))
        conn.commit()
        for lid in libraries.values():
            result = indexer.scan_library(conn, cfg, lid)
            assert result["added"] == 12 and result["errors"] == []
        images, artists, albums = {}, {}, {}
        for item in CATALOG["images"]:
            iid = conn.execute("SELECT id FROM images WHERE library_id=? AND rel_path=?",
                               (libraries[item["library"]], item["path"])).fetchone()[0]
            images[item["key"]] = iid
            uid = users["reader"]
            ops.add_tags(conn, iid, item["tags"], uid)
            if item["rating"] is not None: ops.set_rating(conn, iid, uid, item["rating"])
            if item.get("favorite"): ops.set_favorite(conn, iid, uid, True)
            if name := item.get("artist"):
                if name not in artists: artists[name] = ops.create_artist(conn, name)["id"]
                ops.link_artist(conn, iid, artists[name])
            if name := item.get("album"):
                if name not in albums:
                    albums[name] = ops.create_album(conn, name, libraries[item["library"]])["id"]
                ops.album_add_images(conn, albums[name], [iid])
            if item.get("comment"): ops.add_comment(conn, iid, uid, item["comment"])
            if item.get("note"): ops.add_note(conn, iid, uid, item["note"])
        return Catalog(cfg, roots, libraries, users, images, password)
    finally:
        conn.close()


def cli(cfg: Config, *args: str, input_text="", lang="zh") -> subprocess.CompletedProcess:
    env = dict(os.environ, PICSHELF_LANG=lang, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    return subprocess.run([sys.executable, "-m", "picshelf", "--data", str(cfg.data_dir),
                           "--lang", lang, *args], input=input_text, text=True,
                          capture_output=True, env=env, timeout=30)


@contextlib.contextmanager
def http_server(cfg: Config):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(create_app(cfg), log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        try:
            assert server.started, "Local HTTP server did not start"
            with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
                              trust_env=False, timeout=10) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Local HTTP server did not stop"
    finally:
        sock.close()


def metadata_snapshot(conn) -> dict:
    """Raw relational observation, independent of detail/search serializers."""
    tables = ("ratings", "favorites", "tags", "image_tags", "artists", "image_artists",
              "comments", "notes", "library_grants", "user_groups")
    result = {name: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {name}"))
              for name in tables}
    result["manual_albums"] = [tuple(row) for row in conn.execute(
        "SELECT * FROM albums WHERE kind='manual' ORDER BY id")]
    result["manual_members"] = [tuple(row) for row in conn.execute(
        "SELECT ai.* FROM album_images ai JOIN albums a ON ai.album_id=a.id "
        "WHERE a.kind='manual' ORDER BY ai.album_id,ai.image_id")]
    return result
