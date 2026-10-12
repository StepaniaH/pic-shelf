"""托管库导入器测试。"""
import errno
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from picshelf import auth, db as dbm, importer, indexer, libsvc
from picshelf.cli import main
from picshelf.config import Config
from tests.conftest import make_image


@pytest.fixture()
def env(tmp_path):
    cfg = Config(tmp_path / "data").ensure()
    conn = dbm.open_db(cfg.db_path)
    auth.ensure_default_groups(conn)
    auth.create_user(conn, "admin", "admin-pass-123", is_admin=True)  # CLI 的 ls 需要管理员视角
    libroot = tmp_path / "managed"
    libroot.mkdir()
    lib_id = libsvc.create_library(conn, "managed", str(libroot))
    src = tmp_path / "incoming"
    make_image(src / "a.png", (200, 10, 10))
    make_image(src / "sub" / "b.jpg", (10, 200, 10))
    make_image(src / "sub" / "dup-of-a.png", (200, 10, 10))  # 与 a 同内容 → 去重
    lib = libsvc.get_library(conn, lib_id)
    return {"cfg": cfg, "conn": conn, "tmp": tmp_path, "libroot": libroot,
            "src": src, "lib": lib}


def _count(env) -> int:
    return env["conn"].execute(
        "SELECT COUNT(*) AS n FROM images WHERE library_id=?", (env["lib"]["id"],)
    ).fetchone()["n"]


def test_plan_dry_run_leaves_fs_untouched(env):
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="hardlink")
    assert plan["stats"] == {"hardlink": 2, "copy": 0, "move": 0, "skip": 1, "error": 0}
    done = importer.execute_import(plan, apply=False)
    assert done["hardlink"] == 2 and done["skip"] == 1
    # 目标库目录为空、源目录原样
    assert list(env["libroot"].rglob("*.png")) == []
    assert (env["src"] / "a.png").exists()


def test_apply_hardlink_and_scan(env):
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="hardlink")
    done = importer.execute_import(plan, apply=True)
    assert done["hardlink"] == 2 and done["error"] == 0
    dst = env["libroot"] / "a.png"
    assert dst.exists()
    assert dst.stat().st_ino == (env["src"] / "a.png").stat().st_ino  # 同 inode = 硬链接
    assert (env["src"] / "a.png").exists()  # 原文件仍在
    stats = indexer.scan_library(env["conn"], env["cfg"], env["lib"]["id"])
    assert stats["added"] == 2
    assert _count(env) == 2


def test_apply_copy(env):
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="copy")
    done = importer.execute_import(plan, apply=True)
    assert done["copy"] == 2
    dst = env["libroot"] / "a.png"
    src = env["src"] / "a.png"
    assert dst.stat().st_ino != src.stat().st_ino  # 独立副本
    assert dst.read_bytes() == src.read_bytes()
    assert src.exists()


def test_apply_move_removes_source(env):
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="move")
    done = importer.execute_import(plan, apply=True)
    assert done["move"] == 2
    assert not (env["src"] / "a.png").exists()
    assert (env["libroot"] / "a.png").exists()


def test_hardlink_exdev_falls_back_to_copy(env, monkeypatch):
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="hardlink")

    def fake_link(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(importer.os, "link", fake_link)
    done = importer.execute_import(plan, apply=True)
    assert done["copy"] == 2 and done["hardlink"] == 0 and done["error"] == 0
    assert (env["libroot"] / "a.png").exists()


def test_name_conflict_gets_suffix(env):
    # 目标库先放一个不同内容的同名文件
    make_image(env["libroot"] / "a.png", (1, 2, 3))
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="hardlink")
    targets = [i["dst"] for i in plan["items"] if i["action"] != "skip"]
    assert any(t and t.endswith("a-1.png") for t in targets)


def test_reimport_skips_everything(env):
    plan = importer.plan_import(env["conn"], env["lib"], env["src"], mode="hardlink")
    importer.execute_import(plan, apply=True)
    indexer.scan_library(env["conn"], env["cfg"], env["lib"]["id"])
    plan2 = importer.plan_import(env["conn"], env["lib"], env["src"], mode="hardlink")
    assert plan2["stats"]["skip"] == 3  # 库内已有（a、b）+ 源内重复（dup）
    assert plan2["stats"]["hardlink"] == 0


def test_source_inside_library_rejected(env):
    with pytest.raises(ValueError):
        importer.plan_import(env["conn"], env["lib"], env["libroot"], mode="hardlink")


def test_cli_import_flow(env):
    runner = CliRunner()
    data = str(env["cfg"].data_dir)
    r = runner.invoke(main, ["--data", data, "import", str(env["src"]), "managed", "--mode", "hardlink"])
    assert r.exit_code == 0, r.output
    assert "预演" in r.output and "未改动任何文件" in r.output
    assert list(env["libroot"].rglob("*.png")) == []  # 预演不动文件

    r = runner.invoke(main, ["--data", data, "import", str(env["src"]), "managed",
                             "--mode", "hardlink", "--apply", "--yes"])
    assert r.exit_code == 0, r.output
    assert "执行完成" in r.output and "已扫描入库：新增 2" in r.output

    r = runner.invoke(main, ["--data", data, "ls"])
    assert "共 2 张" in r.output


def test_cli_move_requires_confirmation(env):
    runner = CliRunner()
    data = str(env["cfg"].data_dir)
    # 不给 --yes、确认输入 n → 取消且文件未动
    r = runner.invoke(main, ["--data", data, "import", str(env["src"]), "managed",
                             "--mode", "move", "--apply"], input="n\n")
    assert r.exit_code != 0
    assert (env["src"] / "a.png").exists()


# ---------------------------------------------------------------- Web API（导入向导）

def test_api_import_plan_execute(env):
    from fastapi.testclient import TestClient

    from picshelf.api import create_app

    c = TestClient(create_app(env["cfg"]))
    assert c.post("/api/v1/auth/login",
                  json={"username": "admin", "password": "admin-pass-123"}).status_code == 200

    r = c.post("/api/v1/import/plan", json={
        "source_dir": str(env["src"]), "library_id": env["lib"]["id"], "mode": "hardlink",
    })
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["stats"] == {"hardlink": 2, "copy": 0, "move": 0, "skip": 1, "error": 0}
    assert plan["items_total"] == 3

    # 未确认不可执行
    r = c.post("/api/v1/import/execute", json={
        "source_dir": str(env["src"]), "library_id": env["lib"]["id"], "confirm": False,
    })
    assert r.status_code == 400
    assert not (env["libroot"] / "a.png").exists()

    r = c.post("/api/v1/import/execute", json={
        "source_dir": str(env["src"]), "library_id": env["lib"]["id"],
        "mode": "hardlink", "confirm": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["done"]["hardlink"] == 2 and body["done"]["error"] == 0
    assert body["scan"]["added"] == 2
    assert (env["libroot"] / "a.png").exists()

    # 再次预演：全部跳过（库内已有 + 源内重复）
    plan2 = c.post("/api/v1/import/plan", json={
        "source_dir": str(env["src"]), "library_id": env["lib"]["id"],
    }).json()
    assert plan2["stats"]["hardlink"] == 0 and plan2["stats"]["skip"] == 3


def test_api_import_requires_manage(env):
    from fastapi.testclient import TestClient

    from picshelf.api import create_app

    auth.create_user(env["conn"], "alice2", "alice2-pass-1")  # members 组：只有 view
    c = TestClient(create_app(env["cfg"]))
    assert c.post("/api/v1/auth/login",
                  json={"username": "alice2", "password": "alice2-pass-1"}).status_code == 200
    r = c.post("/api/v1/import/plan", json={
        "source_dir": str(env["src"]), "library_id": env["lib"]["id"],
    })
    assert r.status_code == 403
