import asyncio
import json

import pytest
from click.testing import CliRunner

from picshelf import db as dbm, indexer, libsvc
from picshelf.cli import main
from picshelf.config import Config
from tests.conftest import make_image


@pytest.fixture()
def workdir(tmp_path):
    root = tmp_path / "library"
    make_image(root / "alpha" / "red.png", (220, 40, 40))
    make_image(root / "alpha" / "blue.png", (40, 40, 220))
    make_image(root / "beta" / "green.jpg", (40, 200, 40))
    make_image(root / "beta" / "nested" / "yellow.png", (220, 200, 40))
    return {"tmp": tmp_path, "data": tmp_path / "data", "root": root}


def run_ok(args, input_text=None, **kw):
    runner = CliRunner()
    result = runner.invoke(main, args, input=input_text, catch_exceptions=False, **kw)
    assert result.exit_code == 0, f"命令失败: {args}\n{result.output}"
    return result.output


def _init(workdir):
    data = str(workdir["data"])
    run_ok(["--data", data, "init", "--admin", "admin", "--password-stdin"], input_text="admin-pass-123\n")
    run_ok(["--data", data, "library", "add", "testlib", str(workdir["root"])])
    out = run_ok(["--data", data, "scan", "testlib"])
    assert "新增 4" in out


def test_cli_full_flow(workdir):
    data = str(workdir["data"])
    _init(workdir)

    # 库列表
    out = run_ok(["--data", data, "library", "list"])
    assert "testlib" in out and "图片      4" in out

    # ls / search
    out = run_ok(["--data", data, "ls"])
    assert "共 4 张" in out
    out = run_ok(["--data", data, "ls", "-q", "file:green"])
    assert "共 1 张" in out and "green.jpg" in out

    # show --path
    out = run_ok(["--data", data, "ls", "-q", "file:red", "--json"])
    red_id = json.loads(out)["items"][0]["id"]
    path_out = run_ok(["--data", data, "show", str(red_id), "--path"])
    assert path_out.strip().endswith("alpha/red.png")

    # 评分 / 收藏 / 标签 / 评论 / 笔记
    out = run_ok(["--data", data, "rate", str(red_id), "9"])
    assert "我的评分 → 9" in out
    out = run_ok(["--data", data, "favorite", str(red_id)])
    assert "收藏 → 是" in out
    run_ok(["--data", data, "tag", "add", str(red_id), "色彩:红色", "场景:夜空"])
    out = run_ok(["--data", data, "tag", "list", "-q", "色彩"])
    assert "色彩:红色" in out
    out = run_ok(["--data", data, "show", str(red_id)])
    assert "色彩:红色" in out and "我的 9" in out
    out = run_ok(["--data", data, "ls", "-q", "色彩:红色"])
    assert "共 1 张" in out
    out = run_ok(["--data", data, "ls", "--favorites"])
    assert "共 1 张" in out
    run_ok(["--data", data, "comment", str(red_id), "这张不错"])
    run_ok(["--data", data, "note", str(red_id), "记得换壁纸"])
    out = run_ok(["--data", data, "show", str(red_id)])
    assert "这张不错" in out and "记得换壁纸" in out

    # 艺术家
    run_ok(["--data", data, "artist", "add", "画师A", "--aliases", "A"])
    run_ok(["--data", data, "artist", "link", str(red_id), "画师A"])
    out = run_ok(["--data", data, "artist", "list"])
    assert "画师A" in out and "1 张" in out
    out = run_ok(["--data", data, "ls", "-q", "artist:画师"])
    assert "共 1 张" in out

    # 播放（list 模式）
    out = run_ok(["--data", data, "play", "--player", "list", "--order", "sequential"])
    lines = [l for l in out.splitlines() if l.endswith(".png") or l.endswith(".jpg")]
    assert len(lines) == 4
    out = run_ok(["--data", data, "play", "--player", "list", "--count", "2"])
    lines = [l for l in out.splitlines() if "队列 2 张" in l]
    assert lines

    # 统计 / 查重
    out = run_ok(["--data", data, "stats"])
    assert "图片 4 张" in out
    out = run_ok(["--data", data, "dupes"])
    assert "没有完全重复" in out

    # 用户 / 组
    run_ok(["--data", data, "users", "add", "alice", "--password-stdin"], input_text="alice-pass-1\n")
    out = run_ok(["--data", data, "users", "list"])
    assert "alice" in out and "admin" in out
    run_ok(["--data", data, "groups", "add", "viewers", "--caps", "view,rate"])
    out = run_ok(["--data", data, "groups", "list"])
    assert "viewers" in out
    run_ok(["--data", data, "groups", "grant", "viewers", "testlib", "edit"])
    run_ok(["--data", data, "groups", "revoke", "viewers", "testlib"])

    # 导出（明文 + 加密）与恢复
    plain = workdir["tmp"] / "backup.zip"
    out = run_ok(["--data", data, "export", str(plain)])
    assert plain.exists() and plain.stat().st_size > 0
    enc = workdir["tmp"] / "backup-enc.fernet"
    out = run_ok(
        ["--data", data, "export", str(enc), "--encrypt", "--passphrase-stdin"],
        input_text="secret-pass\n",
    )
    assert enc.exists()
    assert enc.read_bytes().startswith(b"PICSHELF1")

    # 恢复到同一目录（--force，先自动备份现有库）
    out = run_ok(
        ["--data", data, "import-backup", str(enc), "--passphrase-stdin", "--force"],
        input_text="secret-pass\n",
    )
    assert "已恢复数据库" in out
    # 恢复错误口令应报错
    runner = CliRunner()
    bad = runner.invoke(
        main,
        ["--data", data, "import-backup", str(enc), "--passphrase-stdin", "--force"],
        input="wrong-pass\n",
    )
    assert bad.exit_code != 0 and "口令错误" in bad.output

    # 恢复后数据库仍可查询
    out = run_ok(["--data", data, "stats"])
    assert "图片 4 张" in out


def test_cli_not_initialized(workdir):
    runner = CliRunner()
    result = runner.invoke(main, ["--data", str(workdir["data"]), "ls"])
    assert result.exit_code != 0
    assert "尚未初始化" in result.output


def test_cli_library_missing_dir(workdir):
    data = str(workdir["data"])
    run_ok(["--data", data, "init"], )
    runner = CliRunner()
    result = runner.invoke(main, ["--data", data, "library", "add", "x", "/nonexistent-dir-xyz"])
    assert result.exit_code != 0
    assert "目录不存在" in result.output
