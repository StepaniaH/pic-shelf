"""Task panels render Chinese message IDs without changing stored machine values."""
import asyncio

import pytest
from textual.widgets import DataTable, Static

from picshelf import batches, i18n, jobs
from tests.test_tui_playback import generated_queue


@pytest.mark.parametrize(('lang', 'kind_label', 'phases', 'item_labels', 'preview_label', 'unknown_total'), [
    ('zh', '扫描', ('启动中', '等待图库', '处理结束', '已恢复'), ('待处理', '写入中', '创建中', '未变化', '已跳过'), '已预演', '待统计'),
    ('en', 'Scan', ('Starting', 'Waiting for library', 'Finished', 'Restored'), ('Pending', 'Writing', 'Creating', 'Unchanged', 'Skipped'), 'Previewed', 'Not counted yet'),
])
def test_work_panel_and_item_labels_use_selected_language(tmp_path, lang, kind_label, phases, item_labels, preview_label, unknown_total):
    from picshelf.tui import PicShelfTUI
    cfg, lid = generated_queue(tmp_path, 3)

    async def scenario():
        app = PicShelfTUI(cfg)
        async with app.run_test(size=(100, 32)) as pilot:
            job = jobs.submit(app.conn, app.access, 'scan', lid)
            app.conn.execute("UPDATE jobs SET phase='starting' WHERE id=?", (job['id'],))
            app.conn.executemany('INSERT INTO job_items(job_id,item_key,state) VALUES(?,?,?)',
                                 [(job['id'], f'item-{number}.png', state) for number, state in enumerate(('pending','writing','creating','unchanged','skipped'))])
            app.conn.commit()
            await pilot.press('ctrl+j')
            await pilot.pause()
            screen = app.screen
            table = screen.query_one('#work-table', DataTable)
            assert kind_label in str(table.get_row_at(0)[0])
            assert unknown_total in str(table.get_row_at(0)[2])
            for phase, expected in zip(('starting', 'waiting_for_library', 'finished', 'restored'), phases):
                app.conn.execute('UPDATE jobs SET phase=? WHERE id=?', (phase, job['id']))
                app.conn.commit()
                screen.refresh_rows()
                assert expected in screen.query_one('#work-detail', Static).render().plain
            await pilot.press('enter')
            await pilot.pause()
            content = app.screen.query_one('#work-items', Static).render().plain
            assert all(label in content for label in item_labels)
            assert [row[0] for row in app.conn.execute('SELECT state FROM job_items ORDER BY item_key')] == ['pending','writing','creating','unchanged','skipped']
            await pilot.press('escape')
            batch = batches.preview(app.conn, app.access, {'kind':'rating','value':0}, image_ids=[1])
            screen.refresh_rows()
            batch_row = next(index for index, (_, value) in enumerate(screen.records.values()) if value['id'] == batch['id'])
            assert preview_label in str(table.get_row_at(batch_row)[1])
    token = i18n.push_lang(lang)
    try:
        asyncio.run(scenario())
    finally:
        i18n.pop_lang(token)
