"""Browsing import sources does not create a destination or weaken authorization."""
from fastapi.testclient import TestClient

from picshelf import source_access
from picshelf.access import AccessContext
from picshelf.api import create_app


def test_admin_can_browse_before_creating_a_library(cfg, conn, admin, tmp_path):
    source = tmp_path / 'source'
    (source / 'nested').mkdir(parents=True)
    client = TestClient(create_app(cfg), headers={'Origin':'http://testserver'})
    endpoint = '/api/v1/import/sources'
    assert client.get(endpoint, params={'path':str(source)}).status_code == 401
    client.post('/api/v1/auth/login', json={'username':'admin', 'password':'admin-pass-123'})
    response = client.get(endpoint, params={'path':str(source)})
    assert response.status_code == 200, response.text
    assert response.json()['directories'] == [{'name':'nested', 'path':str(source / 'nested')}]
    assert conn.execute('SELECT COUNT(*) FROM libraries').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM albums').fetchone()[0] == 0


def test_manager_must_supply_a_currently_authorized_library(cfg, conn, admin, alice, library, tmp_path):
    source = tmp_path / 'approved'
    source.mkdir()
    source_access.register(conn, AccessContext(admin['id']), str(source))
    client = TestClient(create_app(cfg), headers={'Origin':'http://testserver'})
    client.post('/api/v1/auth/login', json={'username':'alice', 'password':'alice-pass-123'})
    endpoint = '/api/v1/import/sources'
    params = {'path':str(source), 'library_id':library['id']}
    assert client.get(endpoint, params={'path':str(source)}).status_code == 403
    assert client.get(endpoint, params=params).status_code == 403
    group_id = conn.execute('SELECT group_id FROM user_groups WHERE user_id=?', (alice['id'],)).fetchone()[0]
    conn.execute("UPDATE library_grants SET access='manage' WHERE library_id=? AND group_id=?",
                 (library['id'], group_id))
    conn.commit()
    assert client.get(endpoint, params=params).status_code == 200
    assert client.get(endpoint, params={'path':str(source)}).status_code == 403
    conn.execute("UPDATE library_grants SET access='view' WHERE library_id=? AND group_id=?",
                 (library['id'], group_id))
    conn.commit()
    assert client.get(endpoint, params=params).status_code == 403
