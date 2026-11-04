"""Terminal journeys with independent metadata outcomes and real key input."""
import asyncio
import os
import sys
import time

import pytest

from picshelf import db, ops, play
from picshelf.access import AccessContext
from tests.scenario_support import build_catalog


async def settle_batch(app, pilot):
    for _ in range(100):
        await pilot.pause(.1)
        row = app.conn.execute("SELECT state FROM metadata_batches ORDER BY created_at DESC LIMIT 1").fetchone()
        if row and row[0] == "succeeded":
            return
    raise AssertionError("batch did not finish")


@pytest.mark.parametrize("size", [(80, 24), (140, 44)])
def test_keyboard_cross_page_batch_preview_cancel_zero_and_help(tmp_path, size, monkeypatch):
    from picshelf.tui import PicShelfTUI
    from textual.widgets import DataTable, Input
    monkeypatch.setenv("NO_COLOR", "1")
    catalog = build_catalog(tmp_path)
    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        app.PAGE_SIZE = 2
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("x", "]", "x", "r", "0", "enter")
            await pilot.pause()
            assert app.screen.query_one("#confirm-no").has_focus
            assert "2" in app.screen.query_one("#confirm-text").render().plain
            await pilot.press("escape")
            assert app.conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",(catalog.images['n02'],catalog.users['curator'])).fetchone() is None
            await pilot.press("r", "0", "enter")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await settle_batch(app, pilot)
            rows = app.conn.execute("SELECT image_id,value FROM ratings WHERE user_id=? AND image_id IN (?,?) ORDER BY image_id", (catalog.users['curator'],catalog.images['n01'],catalog.images['n02'])).fetchall()
            assert [tuple(row) for row in rows] == [(catalog.images['n01'],0),(catalog.images['n02'],0)]
            await pilot.press("f1")
            assert "Shift" not in app.screen.query_one("#help-text").render().plain
            await pilot.press("escape", "/")
            await pilot.press("q", "r", "x", "a", "l", "w")
            assert app.query_one("#search-input", Input).value == "qrxalw"
            assert isinstance(app.focused, Input)
            await pilot.press("escape")
            assert isinstance(app.focused, DataTable)
    asyncio.run(scenario())


def test_terminal_artist_album_workflow_and_quoted_tag(tmp_path):
    from picshelf.tui import PicShelfTUI
    catalog = build_catalog(tmp_path)
    with db.open_db(catalog.cfg.db_path) as conn:
        artist_id = ops.create_artist(conn, "张三 / Long Name")['id']
        album_id = ops.create_album(conn, "手工整理", catalog.libraries['north'])['id']
    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.press(*list('"night garden"'), "enter")
            await pilot.pause()
            assert app.conn.execute("SELECT COUNT(*) FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE it.image_id=? AND t.name='night garden'",(catalog.images['n01'],)).fetchone()[0] == 1
            for key, value in [("a",str(artist_id)),("l",str(album_id)),("w","done")]:
                await pilot.press(key, *list(value), "enter")
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await settle_batch(app,pilot)
            assert app.conn.execute("SELECT 1 FROM image_artists WHERE image_id=? AND artist_id=?",(catalog.images['n01'],artist_id)).fetchone()
            assert app.conn.execute("SELECT 1 FROM album_images WHERE image_id=? AND album_id=?",(catalog.images['n01'],album_id)).fetchone()
            assert app.conn.execute("SELECT state FROM image_workflow WHERE image_id=? AND user_id=?",(catalog.images['n01'],catalog.users['curator'])).fetchone()[0] == 'done'
    asyncio.run(scenario())


def test_terminal_playback_reopens_fixed_session_paused(tmp_path):
    from picshelf.tui import PicShelfTUI
    catalog = build_catalog(tmp_path)
    async def first():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(140,44)) as pilot:
            await pilot.press("s", "6", "0", "enter")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            return dict(app.playback)
    state = asyncio.run(first())
    assert state['cursor'] == 1 and state['total'] == 24
    async def second():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert app.playback['token'] == state['token']
            assert app.playback['cursor'] == 1 and app.playback['paused']
            await pilot.press("f1")
            assert app.playback['paused']
    asyncio.run(second())


def test_jobs_cancel_and_real_background_scan_stay_interactive(tmp_path):
    from picshelf.tui import PicShelfTUI
    from picshelf import jobs
    catalog = build_catalog(tmp_path)
    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        pending = jobs.submit(app.conn,app.access,'scan',catalog.libraries['north'])
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.press('ctrl+j')
            await pilot.pause()
            await pilot.click('#work-cancel')
            assert jobs.get_job(app.conn,app.access,pending['id'])['state'] == 'cancelled'
            await pilot.click('#work-retry')
            for _ in range(100):
                await pilot.pause(.1)
                if jobs.get_job(app.conn,app.access,pending['id'])['state'] == 'succeeded': break
            assert jobs.get_job(app.conn,app.access,pending['id'])['succeeded'] == 12
            await pilot.click('#work-table')
            await pilot.press('enter')
            await pilot.pause()
            assert '01 zero.png' in app.screen.query_one('#work-items').render().plain
            await pilot.press('escape','escape','j')
            assert app._selected_id() == catalog.images['s01']
    asyncio.run(scenario())


@pytest.mark.parametrize('size,exit_key', [((80,24),b'q'),((140,44),b'\x03')])
def test_real_pty_exit_restores_terminal_and_keeps_saved_rating(tmp_path,size,exit_key):
    import fcntl
    import pty
    import select
    import struct
    import subprocess
    import termios
    from pathlib import Path
    catalog = build_catalog(tmp_path)
    master,slave = pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',size[1],size[0],0,0))
    before = termios.tcgetattr(slave)
    command = [sys.executable,'-m','picshelf','--data',str(catalog.cfg.data_dir),'tui']
    process = subprocess.Popen(command,stdin=slave,stdout=slave,stderr=slave,
                               env={**os.environ,'TERM':'xterm-256color','NO_COLOR':'1'},start_new_session=True)
    output = bytearray()
    def drain(seconds):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            if select.select([master],[],[],.05)[0]:
                try: output.extend(os.read(master,65536))
                except OSError: break
            if process.poll() is not None: break
    try:
        deadline = time.monotonic()+10
        while b'F1' not in output and process.poll() is None and time.monotonic()<deadline: drain(.2)
        assert b'F1' in output, bytes(output[-1000:])
        os.write(master,b'r');drain(.3)
        os.write(master,b'7\r');drain(.5)
        os.write(master,exit_key);drain(8)
        assert process.wait(timeout=2) == (130 if exit_key == b'\x03' else 0)
        assert termios.tcgetattr(slave) == before
        with db.open_db(catalog.cfg.db_path) as conn:
            assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?',
                                (catalog.images['n01'],catalog.users['curator'])).fetchone()[0] == 7
        if destination := os.environ.get('PICSHELF_TEST_PTY_TRANSCRIPTS'):
            path = Path(destination);path.mkdir(parents=True,exist_ok=True)
            (path/f'tui-{size[0]}x{size[1]}.txt').write_bytes(output)
    finally:
        if process.poll() is None: process.kill();process.wait()
        os.close(master);os.close(slave)


def test_slow_preview_process_is_cancelled_without_blocking_keys(tmp_path,monkeypatch):
    """A slow subprocess injects latency; it is not a renderer compatibility test."""
    import sys
    from picshelf.tui import PicShelfTUI
    catalog = build_catalog(tmp_path / 'catalog')
    directory = tmp_path / 'tools';directory.mkdir()
    executable = directory / 'chafa'
    executable.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(20)\nprint("stale preview")\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH',str(directory)+os.pathsep+os.environ['PATH'])
    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(140,44)) as pilot:
            await pilot.press('p')
            await pilot.pause(.1)
            started = time.monotonic()
            await pilot.press('j','p','f1')
            assert time.monotonic()-started < 2
            assert app.screen.query_one('#help-text')
            await pilot.press('escape')
            assert app._selected_id() == catalog.images['s01']
            assert 'stale preview' not in app.query_one('#detail').render().plain
    asyncio.run(scenario())


def test_resumed_sampling_queue_keeps_repeated_positions(tmp_path):
    from picshelf.tui import PicShelfTUI
    catalog = build_catalog(tmp_path)
    with db.open_db(catalog.cfg.db_path) as conn:
        state = play.create_for_user(conn,AccessContext(catalog.users['curator']),
            {'source':{'library_id':catalog.libraries['north']},'query':'file:"01 zero.png"','order':'sample','limit':4,'interval':0})
    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.press('ctrl+p')
            await pilot.pause()
            assert [item['id'] for item in app.current_items] == [catalog.images['n01']]*4
            await pilot.press('right','right')
            await pilot.pause()
            assert app.playback['cursor'] == 2
            assert app.query_one('#image-table').cursor_row == 2
    asyncio.run(scenario())
