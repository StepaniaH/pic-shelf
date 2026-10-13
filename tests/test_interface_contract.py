"""Public CLI/HTTP contracts, using D01 and real process boundaries."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from picshelf import db
from picshelf.api import create_app
from tests.scenario_support import build_catalog, cli


@pytest.fixture
def collection(tmp_path):
    return build_catalog(tmp_path)


def login(client, data, user="curator"):
    assert client.post('/api/v1/auth/login', json={
        'username': user, 'password': data.password}).status_code == 200


def test_machine_failures_and_usage_both_languages(collection):
    for lang in ('zh', 'en'):
        for args, code, exit_code in (
            (('ls', '--json', '--limit', '-1'), 'invalid_arguments', 2),
            (('batch', 'show', 'does-not-exist'), 'batch_not_found', 1),
            (('queries', 'show', '99999'), 'saved_query_not_found', 1),
            (('rate', str(collection.images['n01']), '11', '--json'), 'invalid_rating', 1),
        ):
            result = cli(collection.cfg, *args, lang=lang)
            assert result.returncode == exit_code
            body = json.loads(result.stdout)
            assert body['error']['code'] == code
            assert body['error']['retryable'] is False
            assert body['error']['next_step']
            assert 'Traceback' not in result.stderr
        result = cli(collection.cfg, 'ls', '--json', '--library', 'north', '--sort', 'path', '--limit', '2', lang=lang)
        body = json.loads(result.stdout)
        assert result.returncode == 0 and body['total'] == 12
        assert [row['id'] for row in body['items']] == [collection.images['n01'], collection.images['n02']]


def test_pipes_do_not_answer_prompts_or_receive_progress(collection):
    result = cli(collection.cfg, 'tui')
    assert result.returncode != 0 and 'ls --json' in result.stderr
    result = cli(collection.cfg, 'users', 'add', 'pipeline-user', input_text='unused-input\nunused-input\n')
    assert result.returncode != 0 and '--password-stdin' in result.stderr
    with db.open_db(collection.cfg.db_path) as conn:
        assert conn.execute("SELECT id FROM users WHERE username='pipeline-user'").fetchone() is None
    result = cli(collection.cfg, 'play', '--library', 'north', '--player', 'terminal', '--interval', '0')
    assert result.returncode != 0 and '--player list' in result.stderr
    result = cli(collection.cfg, 'play', '--library', 'north', '--player', 'list', '--count', '2')
    assert result.returncode == 0 and len(result.stdout.splitlines()) == 2
    assert all(line.endswith('.png') for line in result.stdout.splitlines())
    assert result.stderr.strip()
    source = collection.roots / 'source'
    source.mkdir()
    (source / 'new.png').write_bytes(next((collection.roots / 'north').rglob('*.png')).read_bytes())
    result = cli(collection.cfg, 'import', str(source), 'south', '--apply', input_text='y\n')
    assert result.returncode != 0 and '--yes' in result.stderr


def test_http_error_shapes_numeric_bounds_and_strict_confirmation(collection, monkeypatch):
    app = create_app(collection.cfg)
    with TestClient(app, headers={'Origin': 'http://testserver'}) as client:
        for lang in ('zh', 'en'):
            client.headers['Accept-Language'] = lang
            denied = client.get('/api/v1/images')
            assert denied.status_code == 401
            assert denied.json()['error']['code'] == 'authentication_required'
        login(client, collection)
        iid = collection.images['n01']
        assert client.post(f'/api/v1/images/{iid}/rating', json={'value': 0}).status_code == 200
        for invalid in (True, '7', 7.0):
            response = client.post(f'/api/v1/images/{iid}/rating', json={'value': invalid})
            assert response.status_code == 422
            assert response.json()['error']['code'] == 'invalid_arguments'
        for bad in ('0', '-1', str(2**63)):
            response = client.get('/api/v1/images/' + bad)
            assert response.status_code == 422
        for params in ({'limit': -1}, {'offset': 2**63}, {'sort': 'typo'}):
            assert client.get('/api/v1/images', params=params).status_code == 422
        preview = client.post('/api/v1/batches/preview', json={'image_ids': [iid], 'action': {'kind': 'rating', 'value': 8}}).json()
        response = client.post(f"/api/v1/batches/{preview['id']}/execute", json={'confirm': 'true'})
        assert response.status_code == 422
        assert client.get(f'/api/v1/images/{iid}').json()['my_rating'] == 0
        missing = client.get('/api/v1/no-such-endpoint')
        assert missing.status_code == 404 and missing.json()['error']['code'] == 'not_found'
        from picshelf.api import routes_core
        def unavailable(*args):
            raise OSError('private-server-location and secret-material')
        monkeypatch.setattr(routes_core.libsvc, 'global_stats', unavailable)
        failure = client.get('/api/v1/stats')
        assert failure.status_code == 500
        assert failure.json()['error']['code'] == 'internal_error'
        assert 'private-server-location' not in failure.text and 'secret-material' not in failure.text
        assert failure.headers['Cache-Control'] == 'private, no-store'
        schema = client.get('/api/openapi.json').json()
        parameters = {p['name']: p['schema'] for p in schema['paths']['/api/v1/images']['get']['parameters']}
        assert parameters['limit']['maximum'] == 10000
        assert parameters['offset']['maximum'] == 2**63 - 1
        assert 'path' in parameters['sort']['enum']
        parameters = schema['paths']['/api/v1/images/{image_id}']['get']['parameters']
        assert next(p['schema'] for p in parameters if p['name'] == 'image_id')['minimum'] == 1


def test_real_worker_interrupt_exit_code(collection):
    process = subprocess.Popen([sys.executable, '-m', 'picshelf', '--data', str(collection.cfg.data_dir), 'worker'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=dict(os.environ, NO_COLOR='1'))
    try:
        time.sleep(1.5)
        assert process.poll() is None
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130
        assert 'Traceback' not in stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_partial_undo_is_nonzero_and_preserves_later_edit(collection):
    first, second = (collection.images[key] for key in ('n01', 'n02'))
    result = cli(collection.cfg, 'batch', 'preview', str(first), str(second), '--action', '{"kind":"rating","value":8}')
    batch = json.loads(result.stdout)
    result = cli(collection.cfg, 'batch', 'execute', batch['id'], '--yes')
    assert result.returncode == 0 and json.loads(result.stdout)['state'] == 'succeeded'
    assert cli(collection.cfg, 'rate', str(first), '9', '--json').returncode == 0
    result = cli(collection.cfg, 'batch', 'undo', batch['id'], '--yes')
    assert result.returncode == 1
    assert json.loads(result.stdout)['state'] == 'undo_conflict'
    with db.open_db(collection.cfg.db_path) as conn:
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (first, collection.users['curator'])).fetchone()[0] == 9
        assert conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=?', (second, collection.users['curator'])).fetchone() is None


def test_concurrent_languages_keep_machine_fields_and_recovery_separate(collection):
    import asyncio
    import httpx
    app = create_app(collection.cfg)

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            return await asyncio.gather(*(client.get('/api/v1/images', headers={'Accept-Language': lang})
                                          for lang in ('zh', 'en') * 8))

    responses = asyncio.run(requests())
    for index, response in enumerate(responses):
        assert response.status_code == 401
        error = response.json()['error']
        assert error['code'] == 'authentication_required' and error['objects'] == {}
        assert error['next_step'] == ('请先登录后重试' if index % 2 == 0 else 'Sign in before retrying')
