"""TUI handler/query measurements; generated metadata, no terminal latency claim."""
import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import time

from picshelf import play, search
from picshelf.tui import PicShelfTUI
from tests.test_tui_playback import generated_queue


async def run(root, count, rounds):
    cfg, lid = generated_queue(root, count)
    app = PicShelfTUI(cfg)
    original = search.get_image
    calls = []
    def detail(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    search.get_image = detail
    measurements = []
    try:
        async with app.run_test(size=(100, 32)) as pilot:
            app._activate_playback(play.create_for_user(app.conn, app.access, {
                'source': {'library_id': lid}, 'sort': 'path', 'interval': 0}), paused=True)
            await pilot.pause()
            for _ in range(rounds):
                calls.clear()
                queries = []
                app.conn.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
                start = time.perf_counter()
                app.action_play_next()
                elapsed = (time.perf_counter() - start) * 1000
                await pilot.pause(.01)
                app.conn.set_trace_callback(None)
                measurements.append({'handler_ms': elapsed, 'select_count': len(queries),
                                     'detail_calls': len(calls), 'page_rows': len(app.current_items)})
            assert app.playback['cursor'] == rounds and len(app.current_items) <= 500
    finally:
        search.get_image = original
    values = sorted(row['handler_ms'] for row in measurements)
    return {'count': count, 'rounds': rounds, 'handler_median_ms': statistics.median(values),
            'handler_p95_ms': values[min(len(values) - 1, int(len(values) * .95))],
            'max_select_count': max(row['select_count'] for row in measurements),
            'max_detail_calls': max(row['detail_calls'] for row in measurements), 'samples': measurements}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=30)
    args = parser.parse_args()
    if not 30 <= args.rounds < 100:
        parser.error('use 30–99 rounds so all navigation stays on the measured page')
    root = args.output.parent / ('tui-' + str(time.time_ns()))
    result = {'environment': {'python': platform.python_version(), 'textual': importlib.metadata.version('textual'),
                              'system': platform.system(), 'architecture': platform.machine()},
              'budget': {'same_page_handler_p95_ms': 10, 'max_page_rows': 500},
              'limitations': ['Generated metadata, current files may be unavailable',
                              'Handler timing excludes async redraw, physical terminal and renderer latency']}
    result['queues'] = [asyncio.run(run(root / str(count), count, args.rounds)) for count in (100, 500, 10000)]
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({**result, 'queues': [{k: v for k, v in q.items() if k != 'samples'} for q in result['queues']]}))
