"""Playback is a permission-checked snapshot, with explicit random semantics."""
import json
import random

import pytest
from fastapi.testclient import TestClient

from picshelf import db, indexer, play
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import draw_image, metadata_rows
from tests.scenario_support import build_catalog, cli


@pytest.fixture()
def collection(tmp_path):
    return build_catalog(tmp_path)


def access(data):
    return AccessContext(data.users['reader'])


def test_rule_start_freezes_members_and_configuration(collection):
    from picshelf import play_rules
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        rule=play_rules.create(conn,access(data),name='Blue evenings',spec={
            'query':'tag:色彩:蓝色','sort':'path','order':'sequential','interval':3,
            'limit':0,'loop':True,'size':'original','captions':False})
        session=play_rules.start(conn,access(data),rule['id'])
        assert play.queue_items(conn,session['session_id']) == [data.images['n02'],data.images['n04']]
        revised=play_rules.update(conn,access(data),rule['id'],version=1,name='Red evenings',spec={'query':'tag:色彩:红色'})
        draw_image(data.roots/'north'/'13 newer.png',40)
        indexer.scan_library(conn,data.cfg,data.libraries['north'])
        unchanged=play.session_state(conn,access(data),session['token'])
        assert unchanged['spec']['size']=='original' and unchanged['spec']['captions'] is False
        assert unchanged['spec']['query']=='tag:色彩:蓝色' and unchanged['total']==2
        fresh=play_rules.start(conn,access(data),rule['id'])
        assert play.queue_items(conn,fresh['session_id']) == [data.images['n01'],data.images['n03']]
        assert play_rules.export(conn,access(data),rule['id'])['rule']['spec']['query']=='tag:色彩:红色'
        with pytest.raises(ServiceError):
            play_rules.get(conn,AccessContext(data.users['curator']),rule['id'])


@pytest.mark.parametrize('order',['shuffle','random','shuffle_each_round'])
def test_random_orders_preserve_members_with_seeded_rounds(collection,order):
    data=collection; seed=42
    expected=[data.images[f'n{n:02}'] for n in range(1,13)]
    first=expected.copy(); random.Random(seed).shuffle(first)
    with db.open_db(data.cfg.db_path) as conn:
        session=play.create_for_user(conn,access(data),{'order':order,'loop':True,'seed':seed})
        assert play.queue_items(conn,session['session_id']) == first
        for _ in range(13):
            state=play.navigate(conn,access(data),session['token'],'next')
        next_round=expected.copy();random.Random(seed+1).shuffle(next_round)
        assert play.queue_items(conn,session['session_id']) == (next_round if order=='shuffle_each_round' else first)
        assert state['cursor']==0
        # A later scan is not an implicit rule refresh, including between rounds.
        draw_image(data.roots/'north'/'13 added.png',42)
        indexer.scan_library(conn,data.cfg,data.libraries['north'])
        assert set(play.queue_items(conn,session['session_id']))==set(expected)


def test_sample_mode_allows_repeats_and_can_be_reproduced(collection):
    data=collection
    candidates=[data.images['n01'],data.images['n03']]
    expected=random.Random(17).choices(candidates,k=12)
    with db.open_db(data.cfg.db_path) as conn:
        spec={'query':'tag:色彩:红色','order':'sample','seed':17,'limit':12,'loop':True}
        session=play.create_for_user(conn,access(data),spec)
        assert play.queue_items(conn,session['session_id'])==expected
        assert len(set(expected)) < len(expected)  # Two candidates, twelve draws.
        for _ in range(13): play.navigate(conn,access(data),session['token'],'next')
        assert play.queue_items(conn,session['session_id'])==random.Random(18).choices(candidates,k=12)
        assert session['matched_total']==2 and session['total']==12


def test_navigation_retry_restart_pause_and_version_conflict(collection):
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        session=play.create_for_user(conn,access(data),{'query':'tag:色彩:红色'})
        first=play.navigate(conn,access(data),session['token'],'next',request_key='first',expected_version=0)
        assert first['cursor']==0 and first['image_id']==data.images['n01']
        assert play.navigate(conn,access(data),session['token'],'next',request_key='first',expected_version=0)==first
        with pytest.raises(ServiceError) as changed:
            play.navigate(conn,access(data),session['token'],'next',request_key='second',expected_version=0)
        assert changed.value.code=='play_state_changed'
        with pytest.raises(ServiceError):
            play.navigate(conn,access(data),session['token'],'prev',request_key='first')
        play.navigate(conn,access(data),session['token'],'pause',paused=True)
    with db.open_db(data.cfg.db_path) as reopened:
        restored=play.session_state(reopened,access(data),session['token'])
        assert restored['cursor']==0 and restored['paused'] is True
        second=play.navigate(reopened,access(data),session['token'],'next')
        assert second['image_id']==data.images['n03']
        end=play.navigate(reopened,access(data),session['token'],'next')
        assert end['done'] is True and end['cursor']==1 and end['paused'] is True


def test_revoked_scope_cannot_read_or_repeat_old_navigation(collection):
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        session=play.create_for_user(conn,access(data),{})
        play.navigate(conn,access(data),session['token'],'next',request_key='first')
        conn.execute('DELETE FROM library_grants');conn.commit()
        for operation in (lambda:play.session_state(conn,access(data),session['token']),
                          lambda:play.navigate(conn,access(data),session['token'],'next',request_key='first')):
            with pytest.raises(ServiceError) as denied: operation()
            assert denied.value.code=='play_scope_changed'


def test_empty_single_and_missing_records_have_observable_states(collection):
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        empty=play.create_for_user(conn,access(data),{'query':'tag:does-not-exist'})
        state=play.navigate(conn,access(data),empty['token'],'next')
        assert state['done'] and state['cursor']==-1 and state['total']==0
        one=play.create_for_user(conn,access(data),{'query':'file:100%','loop':True})
        for _ in range(3):
            assert play.navigate(conn,access(data),one['token'],'next')['image_id']==data.images['n05']
        conn.execute('UPDATE images SET missing=1 WHERE id=?',(data.images['n05'],));conn.commit()
        assert play.session_state(conn,access(data),one['token'])['unavailable']=='image_unavailable'
        # A fixed position stays visible as unavailable; the client can explicitly skip.
        assert play.queue_items(conn,one['session_id'])==[data.images['n05']]


def test_queue_limit_reports_matching_count_and_actual_count(collection):
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        conn.executemany('INSERT INTO images(library_id,rel_path,filename,ext,size_bytes,width,height,mtime,sha256,phash,imported_at) '
            'VALUES(:library_id,:rel_path,:filename,:ext,:size_bytes,:width,:height,:mtime,:sha256,:phash,:imported_at)',metadata_rows(10003,data.libraries['north']))
        conn.commit()
        session=play.create_for_user(conn,access(data),{'query':'file:image-','order':'sequential'})
        assert session['matched_total']==10003 and session['total']==10000
        assert session['truncated'] is True and session['queue_limit']==10000
        assert session['warnings']==['queue_limit']


@pytest.mark.parametrize('spec',[{'order':'unknown'},{'interval':-1},{'interval':True},
    {'limit':-1},{'sort':'random'},{'size':'arbitrary'},{'captions':'yes'},
    {'media_url':'https://example.org/image'},{'source':{'workflow':'finished'}}, {'query':'rating:bad'}, {'seed':2**53}])
def test_invalid_rules_create_no_session(collection,spec):
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        with pytest.raises(ServiceError): play.create_for_user(conn,access(data),spec)
        assert conn.execute('SELECT COUNT(*) FROM play_sessions').fetchone()[0]==0


def test_cli_rules_and_http_session_resume_share_state(collection):
    data=collection
    saved=cli(data.cfg,'play-rules','save','One','--spec','{"query":"file:100%","interval":0}','--user','reader')
    assert saved.returncode==0,saved.stderr
    rule=json.loads(saved.stdout)
    with TestClient(create_app(data.cfg),headers={'Origin':'http://testserver'}) as client:
        client.post('/api/v1/auth/login',json={'username':'reader','password':data.password})
        session=client.post(f"/api/v1/play/rules/{rule['id']}/start").json()
        url=f"/api/v1/play/sessions/{session['token']}"
        first=client.post(url+'/next',json={'request_key':'first','expected_version':0})
        again=client.post(url+'/next',json={'request_key':'first','expected_version':0})
        assert first.json()==again.json() and first.json()['image']['id']==data.images['n05']
        assert client.post(url+'/pause',json={'paused':True,'expected_version':first.json()['version']}).status_code==200
    observed=cli(data.cfg,'play-sessions','show',session['token'],'--user','reader')
    assert observed.returncode==0,observed.stderr
    assert json.loads(observed.stdout)['paused'] is True


def test_legacy_session_migration_preserves_queue_cursor_and_reports_unknown_scope(tmp_path):
    import sqlite3
    from tests.test_migrations import old_database
    path=old_database(tmp_path/'legacy.db')
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO play_sessions VALUES(3,'fixture-session',7,?, 'random',5,1,1,'2026-10-01T00:00:00+00:00')",(json.dumps({'order':'random','loop':True}),))
        conn.executemany('INSERT INTO play_items VALUES(3,?,?)',[(0,31),(1,47)])
    for _ in range(2):
        with db.open_db(path) as conn:
            state=play.session_state(conn,AccessContext(7),'fixture-session')
            assert state['cursor']==1 and state['total']==2 and state['unavailable']=='image_unavailable'
            assert state['matched_total'] is None and state['truncated'] is None
            assert play.queue_items(conn,3)==[31,47]
            assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_multiple_round_steps_and_rule_updates_do_not_overwrite_other_clients(collection):
    from picshelf import play_rules
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        rule=play_rules.create(conn,access(data),name='Rounds',spec={'query':'file:100%','order':'shuffle_each_round','loop':True})
        session=play_rules.start(conn,access(data),rule['id'])
        play.navigate(conn,access(data),session['token'],'next')
        advanced=play.navigate(conn,access(data),session['token'],'next',step=5)
        assert advanced['cycle']==5 and advanced['cursor']==0
        play_rules.update(conn,access(data),rule['id'],version=1,name='Updated',spec={'query':'fav:'})
        with pytest.raises(ServiceError) as caught:
            play_rules.update(conn,access(data),rule['id'],version=1,name='Stale',spec={})
        assert caught.value.code=='play_rule_changed'


def test_failed_retry_record_rolls_back_cursor_with_its_request(collection):
    import sqlite3
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        session=play.create_for_user(conn,access(data),{})
        def deny_request_record(action,table,*unused):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_INSERT and table=='play_requests' else sqlite3.SQLITE_OK
        conn.set_authorizer(deny_request_record)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                play.navigate(conn,access(data),session['token'],'next',request_key='atomic')
        finally:conn.set_authorizer(None)
        state=play.session_state(conn,access(data),session['token'])
        assert state['cursor']==-1 and state['version']==0
        assert play.navigate(conn,access(data),session['token'],'next',request_key='atomic')['cursor']==0


def test_concurrent_same_navigation_key_advances_once(collection):
    from concurrent.futures import ThreadPoolExecutor
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        session=play.create_for_user(conn,access(data),{})
    def request():
        with db.open_db(data.cfg.db_path) as conn:
            return play.navigate(conn,access(data),session['token'],'next',request_key='shared',expected_version=0)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:request(),range(2)))
    assert results[0]==results[1] and results[0]['cursor']==0
    with db.open_db(data.cfg.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM play_requests').fetchone()[0]==1
