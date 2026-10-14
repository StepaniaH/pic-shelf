"""Rejected tag edits cannot leak into later writes on a persistent connection."""
import asyncio
import sqlite3

import pytest
from textual.widgets import Input

from picshelf import db, indexer, ops
from picshelf.errors import ServiceError
from tests.scenario_support import build_catalog, metadata_snapshot


def test_late_tag_write_failure_rolls_back_all_rows_and_claims(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library['id'])
    image_id = conn.execute('SELECT id FROM images ORDER BY id').fetchone()[0]
    conn.execute("CREATE TRIGGER fail_second_tag BEFORE INSERT ON image_tags "
                 "WHEN (SELECT name FROM tags WHERE id=NEW.tag_id)='second-tag' "
                 "BEGIN SELECT RAISE(ABORT,'injected tag write failure'); END")
    conn.commit()
    before = metadata_snapshot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        ops.add_tags(conn, image_id, ['first-tag', 'second-tag'], admin['id'])
    assert not conn.in_transaction
    assert metadata_snapshot(conn) == before
    ops.set_favorite(conn, image_id, admin['id'], True)
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name IN ('first-tag','second-tag')").fetchone()[0] == 0


def test_tag_validation_preserves_the_callers_open_transaction(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library['id'])
    image_id = conn.execute('SELECT id FROM images ORDER BY id').fetchone()[0]
    conn.execute('INSERT INTO tags(name,created_at) VALUES(?,?)', ('outer-pending', db.utcnow()))
    with pytest.raises(ServiceError):
        ops.add_tags(conn, image_id, ['first-tag', 'x' * 301], admin['id'])
    assert conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name='first-tag'").fetchone()[0] == 0
    with db.open_db(cfg.db_path) as observer:
        assert observer.execute("SELECT COUNT(*) FROM tags WHERE name='outer-pending'").fetchone()[0] == 0
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name='outer-pending'").fetchone()[0] == 0


def test_invalid_second_tag_does_not_commit_on_later_favorite(tmp_path):
    from picshelf.tui import PicShelfTUI
    catalog = build_catalog(tmp_path)

    async def scenario():
        app = PicShelfTUI(catalog.cfg)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            image_id = app._selected_id()
            await pilot.press('t')
            app.screen.query_one(Input).value = 'partial-tag ' + 'x' * 301
            await pilot.press('enter')
            await pilot.pause()
            assert not app.conn.in_transaction
            await pilot.press('f')
            await pilot.pause()
            with db.open_db(catalog.cfg.db_path) as observer:
                assert observer.execute("SELECT COUNT(*) FROM tags WHERE name='partial-tag'").fetchone()[0] == 0
                assert observer.execute('SELECT 1 FROM favorites WHERE image_id=? AND user_id=?',
                                        (image_id, app.user['id'])).fetchone()
    asyncio.run(scenario())


def test_legacy_exact_display_names_keep_their_distinct_tag_identity(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library['id'])
    image_id = conn.execute('SELECT id FROM images ORDER BY id').fetchone()[0]
    tags = {}
    for name in ('e\u0301', '\u00e9'):
        tags[name] = conn.execute('INSERT INTO tags(name,created_at) VALUES(?,?)', (name, db.utcnow())).lastrowid
    conn.commit()
    for name in tags:
        added = ops.add_tags(conn, image_id, [name], admin['id'])
        assert added == [{'id': tags[name], 'name': name}]
    assert {row[0] for row in conn.execute('SELECT tag_id FROM image_tags WHERE image_id=?', (image_id,))} == set(tags.values())
