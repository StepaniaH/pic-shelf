"""Bounded playback pages, real keys, refreshed metadata and current access."""
import asyncio
import io
import secrets

import pytest
from PIL import Image
from textual.widgets import DataTable

from picshelf import auth, db, libsvc, ops, play, search
from picshelf.access import AccessContext
from picshelf.config import Config
from picshelf.errors import ServiceError
from tests.datasets import metadata_rows
from tests.scenario_support import build_catalog


def generated_queue(root, count):
    cfg = Config(root / 'data').ensure()
    originals = root / 'originals'
    originals.mkdir()
    with db.open_db(cfg.db_path) as conn:
        auth.ensure_default_groups(conn)
        auth.create_user(conn, 'player', secrets.token_urlsafe(24), is_admin=True)
        lid = libsvc.create_library(conn, 'Generated queue', str(originals))
        rows = list(metadata_rows(count, lid))
        fields = list(rows[0])
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                         [list(row.values()) for row in rows])
        conn.commit()
    buffer = io.BytesIO()
    with Image.new('RGB', (96, 64), 'red') as image:
        image.save(buffer, 'PNG')
    for item in rows[:3]:
        (originals / item['rel_path']).write_bytes(buffer.getvalue())
    return cfg, lid


@pytest.mark.parametrize('count', [100, 500, 10000])
def test_keyboard_playback_reuses_only_current_page_and_refreshes_edits(tmp_path, monkeypatch, count):
    from picshelf.tui import PicShelfTUI
    cfg, lid = generated_queue(tmp_path, count)
    pages, details = [], []
    original_page, original_detail = play.queue_rows, search.get_image
    def page(*args, **kwargs):
        pages.append(kwargs.get('offset'))
        return original_page(*args, **kwargs)
    def detail(conn, image_id, *args, **kwargs):
        details.append(image_id)
        return original_detail(conn, image_id, *args, **kwargs)
    monkeypatch.setattr(play, 'queue_rows', page)
    monkeypatch.setattr(search, 'get_image', detail)

    async def scenario():
        app = PicShelfTUI(cfg)
        async with app.run_test(size=(100, 32)) as pilot:
            app._activate_playback(play.create_for_user(app.conn, app.access, {
                'source': {'library_id': lid}, 'sort': 'path', 'order': 'sequential', 'interval': 0}), paused=True)
            await pilot.pause()
            assert len(app.current_items) == min(count, 500)
            pages.clear(); details.clear()
            await pilot.press('right')
            await pilot.pause()
            assert app.playback['cursor'] == 1 and app._selected_id() == 2
            assert app.query_one('#image-table', DataTable).has_focus
            assert pages == [] and set(details) == {2}
            assert len(details) <= 2
            await pilot.press('r', '7', 'enter')
            await pilot.pause()
            assert app.current_items[1]['my_rating'] == 7
            assert pages == [0]
            with db.open_db(cfg.db_path) as writer:
                ops.set_favorite(writer, 3, app.user['id'], True)
            await pilot.press('right')
            await pilot.pause()
            assert app.current_items[2]['favorite'] == 1 and pages == [0, 0]
            if count == 10000:
                await pilot.press(']')
                await pilot.pause()
                assert app.playback['cursor'] == 500 and app._selected_id() == 501
                assert [item['id'] for item in app.current_items] == list(range(501, 1001))
                assert len(app.current_items) == 500 and pages[-1] == 500
                await pilot.press('[')
                await pilot.pause()
                assert app.playback['cursor'] == 0 and app._selected_id() == 1 and pages[-1] == 0
    asyncio.run(scenario())


def test_queue_summary_is_bounded_preserves_missing_position_and_rechecks_grants(tmp_path):
    data = build_catalog(tmp_path)
    with db.open_db(data.cfg.db_path) as conn:
        access = AccessContext(data.users['reader'])
        state = play.create_for_user(conn, access, {'source': {'library_id': data.libraries['north']}, 'sort': 'path'})
        conn.execute('UPDATE images SET missing=1 WHERE id=?', (data.images['n02'],)); conn.commit()
        rows = play.queue_rows(conn, access, state['token'], limit=3)
        assert [row['id'] for row in rows] == [data.images[key] for key in ('n01', 'n02', 'n03')]
        assert rows[1]['filename'] is None and rows[1]['position'] == 1
        assert rows[0]['my_rating'] == 0 and rows[0]['favorite'] == 1
        assert set(rows[0]) == {'position', 'id', 'filename', 'width', 'height', 'my_rating', 'favorite'}
        for kwargs in ({'limit': 501}, {'limit': True}, {'offset': 2**63}):
            with pytest.raises(ServiceError):
                play.queue_rows(conn, access, state['token'], **kwargs)
        conn.execute('DELETE FROM library_grants WHERE library_id=?', (data.libraries['north'],)); conn.commit()
        with pytest.raises(ServiceError) as failure:
            play.queue_rows(conn, access, state['token'])
        assert failure.value.code == 'play_scope_changed'


def test_delayed_preview_cannot_republish_after_account_is_disabled(tmp_path, monkeypatch):
    from picshelf import tui
    from textual.widgets import Static
    cfg, lid = generated_queue(tmp_path, 3)

    async def scenario():
        started, release, rendered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def delayed_preview(*args, **kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Native renderers may complete despite cancellation.
                await release.wait()
            rendered.set()
            return 'stale-preview-from-disabled-account'
        monkeypatch.setattr(tui, '_render_preview', delayed_preview)
        app = tui.PicShelfTUI(cfg)
        async with app.run_test(size=(100, 32)) as pilot:
            app._activate_playback(play.create_for_user(app.conn, app.access, {
                'source': {'library_id': lid}, 'sort': 'path', 'interval': 0}), paused=True)
            await pilot.pause()
            await pilot.press('p')
            await asyncio.wait_for(started.wait(), 3)
            with db.open_db(cfg.db_path) as writer:
                writer.execute('UPDATE users SET disabled=1 WHERE id=?', (app.user['id'],))
                writer.commit()
            await pilot.press('right')
            await pilot.pause()
            detail = app.query_one('#detail', Static)
            denied_text = detail.render().plain
            assert app.current_items == [] and app.slideshow_paused
            release.set()
            await asyncio.wait_for(rendered.wait(), 3)
            await pilot.pause()
            assert detail.render().plain == denied_text
            assert 'stale-preview-from-disabled-account' not in detail.render().plain
            assert app.query_one('#image-table', DataTable).row_count == 0
    asyncio.run(scenario())


def test_current_file_loss_pauses_and_disabled_account_clears_visible_page(tmp_path):
    from picshelf.tui import PicShelfTUI
    data = build_catalog(tmp_path)

    async def scenario():
        app = PicShelfTUI(data.cfg)
        async with app.run_test(size=(100, 32)) as pilot:
            app._activate_playback(play.create_for_user(app.conn, app.access, {
                'source': {'library_id': data.libraries['north']}, 'sort': 'path', 'interval': 0}), paused=False)
            await pilot.pause()
            (data.roots / 'north/02 unrated.png').unlink()  # This test owns the independent fixture.
            await pilot.press('right')
            await pilot.pause()
            assert app.playback['cursor'] == 1 and app.slideshow_paused and app.playback['paused']
            await pilot.press('right')
            assert app._selected_id() == data.images['n03']
            with db.open_db(data.cfg.db_path) as writer:
                writer.execute('UPDATE users SET disabled=1 WHERE id=?', (app.user['id'],)); writer.commit()
            await pilot.press('right')
            await pilot.pause()
            assert app.current_items == [] and app._playback_page_key is None and app.slideshow_paused
            await pilot.press('g')
            await pilot.pause()
            assert app.current_items == [] and app.slideshow_paused
    asyncio.run(scenario())
