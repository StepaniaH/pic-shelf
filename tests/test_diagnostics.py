import json
import logging
import secrets

from fastapi.testclient import TestClient

from picshelf import db, diagnostics
from picshelf.api import create_app
from picshelf.config import Config
from tests.scenario_support import build_catalog, cli, metadata_snapshot


def test_diagnostics_omit_private_content_and_do_not_change_metadata(tmp_path):
    catalog=build_catalog(tmp_path/'source')
    secret=secrets.token_urlsafe(32)
    with db.open_db(catalog.cfg.db_path) as conn:
        conn.execute('UPDATE notes SET body=?',(secret,))
        conn.execute('UPDATE libraries SET description=?',('https://private.invalid/'+secret,));conn.commit()
        before=metadata_snapshot(conn)
        report=diagnostics.doctor(catalog.cfg,full=True)
        serialized=json.dumps(report)
        assert secret not in serialized and 'private.invalid' not in serialized
        assert str(catalog.roots) not in serialized and str(catalog.cfg.data_dir) not in serialized
        assert 'password_hash' not in serialized and 'username' not in serialized and 'token' not in serialized
        assert report['ready'] and not report['uploaded'] and report['schema']['observed']==db.SCHEMA_VERSION
        assert metadata_snapshot(conn)==before
    result=cli(catalog.cfg,'doctor','--full')
    assert result.returncode==0 and json.loads(result.stdout)['ready']


def test_uninitialized_diagnostics_do_not_initialize_or_create_secret(tmp_path):
    cfg=Config(tmp_path/'not-created')
    result=cli(cfg,'doctor')
    assert result.returncode==1
    body=json.loads(result.stdout)
    assert body['issues'][0]['code']=='not_initialized' and 'init' in body['issues'][0]['next_step']
    assert not cfg.data_dir.exists()


def test_request_logs_only_route_pattern_and_codes_not_secrets(capsys,caplog,tmp_path):
    catalog=build_catalog(tmp_path/'source')
    secret=secrets.token_urlsafe(32)
    caplog.set_level(logging.INFO,logger='uvicorn.error')
    with TestClient(create_app(catalog.cfg),headers={'Origin':'http://testserver'}) as client:
        response=client.post('/api/v1/auth/login',json={'username':secret,'password':secret})
        assert response.status_code==401 and len(response.headers['X-Request-ID'])==32
        response=client.get('/api/v1/missing/'+secret,params={'private':secret},headers={'Authorization':'Bearer '+secret,'Cookie':'private='+secret})
        assert response.status_code==404
    assert secret not in caplog.text
    entries=[json.loads(record.message) for record in caplog.records if record.name=='uvicorn.error']
    assert entries and all(set(entry)=={'request_id','route','status','code','elapsed_ms'} for entry in entries)
