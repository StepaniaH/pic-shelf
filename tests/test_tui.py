import asyncio

import pytest

from picshelf import auth, db as dbm, indexer, libsvc
from picshelf.config import Config
from tests.conftest import make_image


@pytest.fixture()
def env(tmp_path):
    cfg = Config(tmp_path / "data").ensure()
    conn = dbm.open_db(cfg.db_path)
    auth.ensure_default_groups(conn)
    auth.create_user(conn, "admin", "admin-pass-123", is_admin=True)
    root = tmp_path / "library"
    make_image(root / "alpha" / "red.png", (220, 40, 40))
    make_image(root / "alpha" / "blue.png", (40, 40, 220))
    make_image(root / "beta" / "green.jpg", (40, 200, 40))
    make_image(root / "beta" / "nested" / "yellow.png", (220, 200, 40))
    lib_id = libsvc.create_library(conn, "testlib", str(root))
    indexer.scan_library(conn, cfg, lib_id)
    conn.close()
    return {"cfg": cfg, "lib": lib_id}


def test_tui_smoke(env):
    from picshelf.tui import PicShelfTUI

    cfg: Config = env["cfg"]

    async def scenario():
        app = PicShelfTUI(cfg)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            table = app.query_one("#image-table")
            assert table.row_count == 4
            # 导航
            await pilot.press("j")
            await pilot.press("k")
            await pilot.press("p")   # 打开预览
            await pilot.press("p")   # 关闭预览
            await pilot.press("g")   # 刷新
            await pilot.pause()

            # 收藏当前选中项
            await pilot.press("f")
            await pilot.pause()

            # 评分（弹窗输入 8）
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("8")
            await pilot.press("enter")
            await pilot.pause()

            # 加标签（弹窗输入）
            await pilot.press("t")
            await pilot.pause()
            for ch in "测试标签":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()

            # 垃圾键不应崩溃
            await pilot.press("z", "x")
            await pilot.press("space")

    asyncio.run(scenario())

    conn = dbm.open_db(cfg.db_path)
    favs = conn.execute("SELECT COUNT(*) AS n FROM favorites").fetchone()["n"]
    ratings = conn.execute("SELECT COUNT(*) AS n FROM ratings").fetchone()["n"]
    tags = conn.execute("SELECT COUNT(*) AS n FROM image_tags").fetchone()["n"]
    assert favs == 1, "收藏动作未生效"
    assert ratings == 1, "评分动作未生效"
    assert tags == 1, "标签动作未生效"


def test_tui_metadata_does_not_interpret_rich_or_ansi(env):
    from picshelf.tui import PicShelfTUI
    from picshelf import ops
    with dbm.open_db(env["cfg"].db_path) as conn:
        for row in conn.execute("SELECT id FROM images"):
            ops.add_tags(conn,row[0],["[bold]literal tag[/bold]"],1)
        conn.execute("UPDATE images SET filename='[link=https://example.org]literal[/link].png'");conn.commit()
    async def scenario():
        app = PicShelfTUI(env["cfg"])
        async with app.run_test(size=(140,44)) as pilot:
            await pilot.pause()
            app._update_detail()
            text = app.query_one("#detail").render()
            assert "[bold]literal tag[/bold]" in text.plain
            assert "[link=https://example.org]literal[/link].png" in text.plain
            assert not text.spans
    asyncio.run(scenario())


def test_tui_small_terminal_pagination_and_slideshow(env):
    from picshelf.tui import PicShelfTUI

    async def scenario():
        app = PicShelfTUI(env["cfg"])
        app.PAGE_SIZE = 2
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert not app.query_one("#left").display
            assert app.total == 4 and len(app.current_items) == 2
            first = app.current_items[0]["id"]
            await pilot.press("]")
            await pilot.pause()
            assert app.page_offset == 2 and app.current_items[0]["id"] != first
            await pilot.press("[")
            await pilot.pause()
            assert app.page_offset == 0
            await pilot.press("s", "escape")
            await pilot.pause()
            assert app.slideshow_timer is None
            app._start_slideshow("60")
            timer = app.slideshow_timer
            assert timer is not None
            await pilot.press("space")
            assert app.slideshow_paused
            await pilot.press("space")
            assert not app.slideshow_paused and app.slideshow_timer is timer
            await pilot.press("b")
            assert app.query_one("#left").display

    asyncio.run(scenario())
