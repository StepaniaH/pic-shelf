"""Search expectations are hand-authored against the fixed catalog manifest."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from picshelf import catalog, db, indexer, ops, search
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import draw_image
from tests.scenario_support import build_catalog, cli, metadata_snapshot


@pytest.fixture()
def collection(tmp_path):
    data = build_catalog(tmp_path)
    with db.open_db(data.cfg.db_path) as conn:
        conn.execute("UPDATE images SET imported_at='2000-01-01T00:00:00+00:00'")
        conn.execute("UPDATE images SET width=40,height=80,imported_at='2026-10-12T16:00:00+00:00' WHERE id=?", (data.images['n01'],))
        conn.execute("UPDATE images SET width=40,height=40,imported_at='2026-10-12T23:59:59+08:00' WHERE id=?", (data.images['n02'],))
        conn.execute("UPDATE images SET imported_at='2026-10-14T00:00:00+00:00' WHERE id=?", (data.images['n03'],))
        conn.execute("UPDATE images SET missing=1 WHERE id=?", (data.images['n12'],))
        conn.execute("INSERT INTO image_workflow VALUES(?,?, 'done',?)", (data.images['n01'],data.users['reader'],db.utcnow()))
        conn.commit()
    return data


def result(conn, data, query, **kwargs):
    return search.search_images(conn,data.users['reader'],[data.libraries['north']],query=query,**kwargs)


@pytest.mark.parametrize(('kind', 'value', 'expected'), [
    ('ext', 'png, jpg', ['png', 'jpg']),
    ('tag', 'red,blue', ['red', 'blue']),
    ('tag', '"comma,name",night sky', ['comma,name', 'night sky']),
    ('tag', r'comma\,name,blue', ['comma,name', 'blue']),
    ('tag', 'night sky -missing:any', ['night sky -missing:any']),
])
def test_condition_editor_parses_or_without_injecting_conditions(kind, value, expected):
    expression = search.append_condition('rated:true', kind=kind, values=value)
    assert search.parse(expression).clauses == [
        {'kind': 'rated', 'values': ['true'], 'neg': False},
        {'kind': kind, 'values': expected, 'neg': False},
    ]


@pytest.mark.parametrize('value', ['red,', ',red', 'red,,blue', '"unclosed', 'red\\'])
def test_condition_editor_rejects_empty_or_unclosed_values(value):
    with pytest.raises(ServiceError):
        search.append_condition('', kind='tag', values=value)


@pytest.mark.parametrize('expression', ['tag:', 'file:', 'artist:', 'album:', 'ext:', 'rating:>11',
    'rating:0,nope', '-rating:bad', 'avg:-1', 'fav:perhaps', 'fav:true,', 'width:-1',
    'height:nan', 'orientation:round', 'workflow:finished', 'missing:maybe',
    'imported:2026-10-13', 'imported:>=now-999999d', '-', '""', 'x'*4097,
    'album:999999999999999999999', 'file:\x00'])
def test_invalid_expression_never_widens_results(collection, expression):
    with db.open_db(collection.cfg.db_path) as conn:
        got = result(conn,collection,expression)
        assert got['errors'] and got['items'] == [] and got['total'] == 0
        assert got['error_codes'] == ['invalid_query'] * len(got['errors'])


@pytest.mark.parametrize(('expression','keys'), [
    ('rating:0', ['n01','n02']), ('rated:false',['n02']), ('rated:true rating:0',['n01']),
    ('width:40 height:>=40',['n01','n02']), ('orientation:portrait',['n01']),
    ('orientation:square',['n02']), ('workflow:done',['n01']),
    ('missing:true',['n12']), ('missing:any file:twelve',['n12']),
    ('imported:>=2026-10-13T00:00:00+08:00',['n01','n03']),
    ('tag:色彩:红色,色彩:蓝色 -file:unrated',['n01','n03','n04']),
    ('色彩:红色',['n01','n03']), ('tag:"night sky"',['n01','n04']),
    ('file:"comma,name.png"',['n08']), ('file:%,_',['n05','n06']),
])
def test_explicit_conditions_match_fixed_sets(collection, expression, keys):
    with db.open_db(collection.cfg.db_path) as conn:
        got = result(conn,collection,expression)
        assert not got['errors']
        assert {r['id'] for r in got['items']} == {collection.images[k] for k in keys}


def test_relative_time_is_recomputed_and_absolute_timezone_preserved(collection):
    with db.open_db(collection.cfg.db_path) as conn:
        first = result(conn,collection,'imported:>=now-1d',now=datetime(2026,10,13,16,tzinfo=timezone.utc))
        second = result(conn,collection,'imported:>=now-1d',now=datetime(2026,10,14,16,tzinfo=timezone.utc))
        assert {r['id'] for r in first['items']} == {collection.images['n01'],collection.images['n03']}
        assert [r['id'] for r in second['items']] == [collection.images['n03']]


def test_saved_smart_album_is_dynamic_owned_and_permission_filtered(collection):
    from picshelf import saved_queries
    data=collection; access=AccessContext(data.users['reader'])
    with db.open_db(data.cfg.db_path) as conn:
        before=metadata_snapshot(conn)
        rule=saved_queries.create(conn,access,name='Night sky',query='tag:"night sky"',sort='path',
                                  kind='smart',display={'density':'compact','show_filename':False})
        assert metadata_snapshot(conn) == before
        assert [r['id'] for r in saved_queries.run(conn,access,rule['id'])['items']] == [data.images['n01'],data.images['n04']]
        draw_image(data.roots/'north'/'13 new.png',40)
        indexer.scan_library(conn,data.cfg,data.libraries['north'])
        added=conn.execute("SELECT id FROM images WHERE filename='13 new.png'").fetchone()[0]
        ops.add_tags(conn,added,['night sky'],data.users['reader'])
        assert [r['id'] for r in saved_queries.run(conn,access,rule['id'])['items']] == [data.images['n01'],data.images['n04'],added]
        for other in ('curator','outsider'):
            with pytest.raises(ServiceError) as denied:
                saved_queries.get(conn,AccessContext(data.users[other]),rule['id'])
            assert denied.value.status == 404
        conn.execute('DELETE FROM library_grants');conn.commit()
        assert saved_queries.run(conn,access,rule['id'])['items'] == []
        exported=saved_queries.export(conn,access,rule['id'])
        assert exported['rule']['query'] == 'tag:"night sky"'
        assert exported['rule']['display'] == {'density':'compact','show_filename':False}
        assert 'items' not in exported and 'owner_id' not in exported['rule']


def test_invalid_saved_rule_remains_editable_without_becoming_empty_success(collection):
    from picshelf import saved_queries
    data=collection; access=AccessContext(data.users['reader'])
    with db.open_db(data.cfg.db_path) as conn:
        with pytest.raises(ServiceError):
            saved_queries.create(conn,access,name='Broken',query='rating:')
        rule=saved_queries.create(conn,access,name='Fix me',query='rated:false')
        conn.execute('UPDATE saved_queries SET query=? WHERE id=?',('rating:bad',rule['id']));conn.commit()
        assert saved_queries.get(conn,access,rule['id'])['errors']
        with pytest.raises(ServiceError) as broken:
            saved_queries.run(conn,access,rule['id'])
        assert broken.value.code == 'invalid_query'
        repaired=saved_queries.update(conn,access,rule['id'],query='rated:true rating:0',version=rule['version'])
        assert not repaired['errors']
        with pytest.raises(ServiceError) as stale:
            saved_queries.update(conn,access,rule['id'],query='',version=rule['version'])
        assert stale.value.code == 'saved_query_changed'
        assert [r['id'] for r in saved_queries.run(conn,access,rule['id'])['items']] == [data.images['n01']]


def test_condition_builder_uses_server_parser_and_does_not_inject_more_terms(collection):
    query=search.append_condition('rated:true',kind='tag',values=['night sky','comma,name'],neg=False)
    parsed=search.parse(query)
    assert not parsed.errors
    assert parsed.clauses == [{'kind':'rated','values':['true'],'neg':False},
                              {'kind':'tag','values':['night sky','comma,name'],'neg':False}]
    with pytest.raises(ServiceError):
        search.append_condition('',kind='unknown',values=['true'])
    with pytest.raises(ServiceError):
        search.append_condition('rating:',kind='fav',values=['true'])


def test_cli_api_and_tui_share_explicit_rating_and_errors(collection):
    from picshelf import saved_queries
    from picshelf.tui import PicShelfTUI
    data=collection
    created=cli(data.cfg,'queries','save','Unrated','--query','rated:false','--kind','smart','--user','reader')
    assert created.returncode == 0, created.stderr
    rule=json.loads(created.stdout)
    with TestClient(create_app(data.cfg),headers={'Origin':'http://testserver'}) as client:
        assert client.post('/api/v1/auth/login',json={'username':'reader','password':data.password}).status_code == 200
        for lang in ('zh','en'):
            got=client.get(f"/api/v1/queries/{rule['id']}/images",headers={'Accept-Language':lang})
            assert got.status_code == 200 and [i['id'] for i in got.json()['items']] == [data.images['n02']]
        bad=client.post('/api/v1/search/append',json={'query':'','kind':'unknown','values':['yes']})
        assert bad.status_code == 400 and bad.json()['error']['code'] == 'invalid_query'
    bad=cli(data.cfg,'search','--query','tag:','--user','reader','--json')
    assert bad.returncode != 0 and json.loads(bad.stdout)['error_codes'] == ['invalid_query']
    # The current TUI acts as the local administrator, whose personal rating
    # is deliberately different from the reader's unscored image above.
    with db.open_db(data.cfg.db_path) as conn:
        ops.set_rating(conn,data.images['n02'],data.users['curator'],0)
    async def scenario():
        app=PicShelfTUI(data.cfg)
        async with app.run_test(size=(100,30)) as pilot:
            await pilot.pause()
            await pilot.press('/')
            for char in 'rated:true rating:0': await pilot.press(char)
            await pilot.press('enter');await pilot.pause()
            assert [i['id'] for i in app.current_items] == [data.images['n02']]
    asyncio.run(scenario())


def test_hidden_album_metadata_cannot_influence_visible_search(collection):
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        secret=ops.create_album(conn,'South only',data.libraries['south'])
        # Model an old cross-library association, which the current write path refuses.
        iid=data.images['n05']
        conn.execute('DELETE FROM album_images WHERE image_id=?',(iid,))
        conn.execute('INSERT INTO album_images(album_id,image_id,position,added_at) VALUES(?,?,0,?)',
                     (secret['id'],iid,db.utcnow()));conn.commit()
        for query in ('album:"South only"',f"album:{secret['id']}"):
            assert result(conn,data,query)['items'] == []
        assert [i['id'] for i in result(conn,data,'album:none file:05')['items']] == [iid]


def test_search_errors_do_not_create_playback_or_batch_work(collection):
    from picshelf import batches, play
    data=collection
    with db.open_db(data.cfg.db_path) as conn:
        access=AccessContext(data.users['reader'])
        before=metadata_snapshot(conn)
        with pytest.raises(ServiceError):
            play.create_session(conn,access.user_id,access.allowed_libraries(conn),{'query':'rating:0,bad'})
        with pytest.raises(ServiceError):
            batches.preview(conn,access,action={'kind':'favorite','value':True},query={'query':'tag:'})
        assert conn.execute('SELECT COUNT(*) FROM play_sessions').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM metadata_batches').fetchone()[0] == 0
        assert metadata_snapshot(conn) == before
        assert play.build_queue(conn,access.user_id,access.allowed_libraries(conn),{'query':'missing:true'}) == []


def test_rule_keeps_date_offset_and_static_paging_then_refreshes(collection):
    from picshelf import saved_queries
    data=collection; access=AccessContext(data.users['reader'])
    with db.open_db(data.cfg.db_path) as conn:
        rule=saved_queries.create(conn,access,name='All records',query='missing:any',sort='path')
        pages=[saved_queries.run(conn,access,rule['id'],offset=n,limit=5) for n in (0,5,10)]
        assert [i['id'] for page in pages for i in page['items']] == [data.images[f'n{n:02}'] for n in range(1,13)]
        assert all(page['pagination']=='live_offset' for page in pages)
        expression='imported:>=2026-10-13T00:00:00+08:00'
        updated=saved_queries.update(conn,access,rule['id'],version=1,query=expression)
        assert saved_queries.export(conn,access,rule['id'])['rule']['query'] == expression
        assert [i['id'] for i in saved_queries.run(conn,access,rule['id'])['items']] == [data.images['n01'],data.images['n03']]
        random=result(conn,data,'',sort='random',limit=3)
        assert random['pagination']=='random_sample' and len({i['id'] for i in random['items']})==3
        for kwargs in ({'sort':'typo'}, {'limit':-1}, {'offset':-1}, {'limit':True}, {'offset':2**63}):
            got=result(conn,data,'',**kwargs)
            assert got['errors'] and not got['items']


def test_saved_query_http_owner_and_concurrent_edit_boundary(collection):
    data=collection
    with TestClient(create_app(data.cfg),headers={'Origin':'http://testserver'}) as client:
        client.post('/api/v1/auth/login',json={'username':'reader','password':data.password})
        rule=client.post('/api/v1/queries',json={'name':'Reader rules','query':'rated:false'}).json()
        assert client.patch(f"/api/v1/queries/{rule['id']}",json={'version':1,'query':'rated:true rating:0'}).status_code==200
        assert client.patch(f"/api/v1/queries/{rule['id']}",json={'version':1,'query':''}).status_code==409
        assert client.patch(f"/api/v1/queries/{rule['id']}",json={'version':2,'query':None}).status_code==400
        for username in ('outsider','curator'):
            client.post('/api/v1/auth/login',json={'username':username,'password':data.password})
            for suffix in ('','/images','/export'):
                assert client.get(f"/api/v1/queries/{rule['id']}"+suffix).status_code==404
            assert client.patch(f"/api/v1/queries/{rule['id']}",json={'version':2,'name':'Taken'}).status_code==404
