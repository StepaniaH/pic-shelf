"""Independent query sets and transport-specific strictness at real entry points."""
import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

from picshelf import batches, catalog, db, search
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.scenario_support import build_catalog, cli, http_server, metadata_snapshot


@pytest.mark.parametrize(('field', 'value'), [
    ('library_id', True), ('library_id', 0), ('library_id', 2**63), ('library_id', '1'),
    ('album_id', 1.0), ('artist_id', -1), ('artist_id', []),
    ('limit', None), ('limit', True), ('limit', -1), ('limit', 10001),
    ('offset', 2**63), ('offset', float('nan')), ('offset', float('inf')),
    ('favorites', None), ('favorites', 'false'), ('favorites', 1),
    ('rating_min', True), ('rating_min', 11), ('rating_min', 7.0),
    ('workflow', []), ('workflow', 'unknown'), ('query', None),
    ('tag', []), ('tag', 'x' * 301), ('sort', {}), ('sort', 'unknown'),
])
def test_domain_query_rejects_malformed_fields(field, value):
    with pytest.raises(ServiceError) as failure:
        catalog.ImageQuery(**{field: value})
    assert failure.value.code == 'query_invalid'
    assert failure.value.objects == {'field': field}
    assert failure.value.status == 400 and failure.value.next_step


def test_domain_preserves_zero_count_null_filters_and_integer_bounds():
    spec = catalog.ImageQuery(limit=0, offset=2**63 - 1, library_id=2**63 - 1,
                              favorites=False, rating_min=0, tag='', workflow=None)
    assert spec.limit == 0 and spec.rating_min == 0 and spec.favorites is False
    for kind in ([], {}, None, True):
        with pytest.raises(ServiceError) as failure:
            search.append_condition('', kind=kind, values=['x'])
        assert failure.value.code == 'invalid_query'


def test_batch_domain_errors_do_not_write_or_broaden_selection(tmp_path):
    data = build_catalog(tmp_path)
    with db.open_db(data.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        access = AccessContext(data.users['curator'])
        for query in ({'favorites': 'false'}, {'sort': []}, {'tag': 'x' * 301},
                      {'library_id': True}, {'rating_min': float('nan')}, {'workflow': {}},
                      {'query': 'x' * 4097}, {'offset': 0}, {'unknown': 1}):
            with pytest.raises(ServiceError) as failure:
                batches.preview(conn, access, {'kind': 'rating', 'value': 7}, query=query)
            assert failure.value.code == 'batch_invalid'
            assert metadata_snapshot(conn) == before
            assert conn.execute('SELECT COUNT(*) FROM metadata_batches').fetchone()[0] == 0


def test_url_conversion_and_strict_json_keep_distinct_contracts(tmp_path):
    data = build_catalog(tmp_path)
    with TestClient(create_app(data.cfg), headers={'Origin': 'http://testserver'}) as client:
        assert client.post('/api/v1/auth/login', json={'username': 'reader', 'password': data.password}).status_code == 200
        for value in ('true', '1', 'false', '0'):
            result = client.get('/api/v1/images', params={'library_id': str(data.libraries['north']), 'favorites': value})
            assert result.status_code == 200
            assert result.json()['total'] == (2 if value in ('true', '1') else 12)
        for value in (True, 1.0, '1', 0, 2**63):
            result = client.post('/api/v1/batches/preview', json={
                'image_ids': [value], 'action': {'kind': 'favorite', 'value': True}})
            assert result.status_code == 422 and result.json()['error']['code'] == 'invalid_arguments'
        result = client.get('/api/v1/images', params={'tag': 'x' * 301})
        assert result.status_code == 400 and result.json()['error']['code'] == 'query_invalid'
        assert client.get('/api/v1/images', params={'library_id': data.libraries['south']}).status_code == 403


CASES = [
    ('file:"comma,name"', ['n08'], False),
    (r'file:comma\,name', ['n08'], False),
    ('file:"quote\'name"', ['n10'], False),
    ('file:zero,unrated', ['n01', 'n02', 's01', 's02'], False),
    ('"unfinished', [], True),
    ('x' * 4097, [], True),
    ('width:nan', [], True),
    ('album:9223372036854775808', [], True),
]


@pytest.mark.http
@pytest.mark.skipif(os.environ.get('PICSHELF_HTTP_TESTS') != '1', reason='opt-in real HTTP suite')
def test_known_sets_via_service_real_http_and_cli(tmp_path):
    data = build_catalog(tmp_path)
    with db.open_db(data.cfg.db_path) as conn, http_server(data.cfg) as client:
        for username, visible in [('curator', {'n', 's'}), ('reader', {'n'}), ('outsider', set())]:
            client.cookies.clear()
            assert client.post('/api/v1/auth/login', json={'username': username, 'password': data.password}).status_code == 200
            for expression, keys, invalid in CASES:
                expected = {data.images[key] for key in keys if key[0] in visible}
                direct = catalog.query_images(conn, AccessContext(data.users[username]), catalog.ImageQuery(query=expression))
                response = client.get('/api/v1/images', params={'q': expression})
                assert response.status_code == 200
                process = cli(data.cfg, 'ls', '--json', '--user', username, '--query', expression)
                assert process.returncode == (2 if invalid else 0)
                for result in (direct, response.json(), json.loads(process.stdout)):
                    assert {row['id'] for row in result['items']} == expected
                    assert bool(result['errors']) == invalid
                assert 'Traceback' not in process.stderr


def test_tui_query_and_malformed_tag_use_real_input_events(tmp_path):
    from picshelf.tui import PicShelfTUI
    from textual.widgets import Input
    data = build_catalog(tmp_path)

    async def scenario():
        app = PicShelfTUI(data.cfg)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            for expression, keys, invalid in CASES:
                await pilot.press('/')
                app.query_one('#search-input', Input).value = expression
                await pilot.press('enter')
                await pilot.pause()
                assert {row['id'] for row in app.current_items} == {data.images[key] for key in keys}
            await pilot.press('/')
            app.query_one('#search-input', Input).value = 'file:"comma,name"'
            await pilot.press('enter', 't', '"', 'b', 'a', 'd', 'enter')
            await pilot.pause()
            assert app._selected_id() == data.images['n08']
            assert app.conn.execute("SELECT id FROM tags WHERE name='bad'").fetchone() is None
    asyncio.run(scenario())
