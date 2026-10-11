import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from PIL import Image, ImageDraw

from picshelf import auth, db as dbm, libsvc
from picshelf.config import Config


@pytest.fixture()
def cfg(tmp_path):
    return Config(tmp_path / "data").ensure()


@pytest.fixture()
def conn(cfg):
    c = dbm.open_db(cfg.db_path)
    auth.ensure_default_groups(c)
    yield c
    c.close()


@pytest.fixture()
def admin(conn):
    uid = auth.create_user(conn, "admin", "admin-pass-123", is_admin=True)
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


@pytest.fixture()
def alice(conn):
    uid = auth.create_user(conn, "alice", "alice-pass-123")
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def make_image(path: Path, color=(220, 40, 40), size=(800, 600), box=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(0, 0, 0))
    img.save(path)
    return path


@pytest.fixture()
def library(conn, tmp_path):
    root = tmp_path / "library"
    make_image(root / "alpha" / "red.png", (220, 40, 40))
    make_image(root / "alpha" / "blue.png", (40, 40, 220))
    make_image(root / "beta" / "green.jpg", (40, 200, 40))
    make_image(root / "beta" / "nested" / "yellow.png", (220, 200, 40))
    lib_id = libsvc.create_library(conn, "testlib", str(root))
    return {"id": lib_id, "root": root}


def image_id_by_name(conn, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM images WHERE filename=?", (name,)
    ).fetchone()
    assert row is not None, f"image not found: {name}"
    return int(row["id"])
