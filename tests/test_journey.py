"""Fresh-instance journey judged against known file bytes and explicit user edits."""
import asyncio
import json
from pathlib import Path
import secrets
import shutil

from fastapi.testclient import TestClient

from picshelf import db
from picshelf.api import create_app
from picshelf.config import Config
from tests.datasets import catalog_files, file_inventory
from tests.scenario_support import cli


def test_fresh_cli_http_and_tui_collection_survives_migration_and_restore(tmp_path):
    originals=tmp_path/'originals';catalog_files(originals)
    original_inventory=file_inventory(originals)
    cfg=Config(tmp_path/'data')
    password=secrets.token_urlsafe(24)
    def run(*args, expected=0):
        result=cli(cfg,*map(str,args),input_text=password+'\n' if '--password-stdin' in args else '')
        assert result.returncode==expected,(args,result.stdout,result.stderr)
        return result.stdout
    run('init','--admin','curator','--password-stdin')
    run('library','add','indexed',originals/'north','--mode','indexed')
    run('scan','indexed','--json')
    root=tmp_path/'managed'
    run('library','add','collection',root,'--mode','managed','--mkdir')
    plan=json.loads(run('import',originals/'north','collection','--mode','copy','--json'))
    assert plan['stats']['copy']==12 and not list(root.iterdir())
    occupied=root/'01 zero.png';occupied.write_bytes(b'later occupant')
    partial=json.loads(run('import-execute',plan['plan_id'],'--yes',expected=1))
    assert partial['state']=='partial' and occupied.read_bytes()==b'later occupant'
    occupied.unlink()  # Explicitly resolve only the fixture-created conflict.
    assert json.loads(run('import-execute',plan['plan_id'],'--yes','--retry'))['state']=='succeeded'
    repeated=json.loads(run('import',originals/'north','collection','--mode','copy','--json'))
    assert repeated['stats']['skip']==12 and repeated['stats']['copy']==0
    listed=json.loads(run('ls','--library','collection','--sort','path','--json'))
    assert listed['total']==12
    by_path={item['rel_path']:item['id'] for item in listed['items']}
    zero,unrated=by_path['01 zero.png'],by_path['02 unrated.png']
    run('rate',zero,0,'--json');run('favorite',zero);run('tag','add',zero,'organized')
    with TestClient(create_app(cfg),headers={'Origin':'http://testserver'}) as client:
        assert client.post('/api/v1/auth/login',json={'username':'curator','password':password}).status_code==200
        batch=client.post('/api/v1/batches/preview',json={'image_ids':[zero,unrated],'action':{'kind':'rating','value':7}}).json()
        assert client.post(f"/api/v1/batches/{batch['id']}/execute",json={'confirm':True}).status_code==200
        run('rate',zero,9,'--json')
        undone=client.post(f"/api/v1/batches/{batch['id']}/undo",json={'confirm':True}).json()
        assert undone['state']=='undo_conflict'
        assert client.get(f'/api/v1/images/{zero}').json()['my_rating']==9
        # The released detail/summary interface uses -1 for an absent rating.
        assert client.get(f'/api/v1/images/{unrated}').json()['my_rating']==-1
        with db.open_db(cfg.db_path) as observed:
            assert observed.execute('SELECT 1 FROM ratings WHERE image_id=? AND user_id=1',(unrated,)).fetchone() is None
        observed.close()
        assert client.post(f'/api/v1/images/{zero}/comments',json={'body':'Known journey comment'}).status_code==200
        assert client.get('/').status_code==200  # Static HTTP entry, not a browser claim.
    run('rate',zero,0,'--json')
    query=json.loads(run('queries','save','Organized','--query','tag:organized','--kind','smart'))
    assert [item['id'] for item in json.loads(run('queries','run',query['id']))['items']]==[zero]
    rule=json.loads(run('play-rules','save','Watch organized','--spec',json.dumps({'query':'tag:organized','source':{'library_id':listed['items'][0]['library_id']},'order':'sequential'})))
    session=json.loads(run('play-rules','start',rule['id']))
    assert session['total']==1
    run('play-sessions','control',session['token'],'pause')
    assert json.loads(run('play-sessions','show',session['token']))['paused']
    (root/'renamed').mkdir();(root/'01 zero.png').rename(root/'renamed/first.png')
    run('scan','collection','--json')
    assert json.loads(run('show',zero,'--json'))['rel_path']=='renamed/first.png'
    copied=tmp_path/'relocated';shutil.copytree(root,copied)
    relocation=json.loads(run('paths','preview','collection',copied))
    run('paths','execute',relocation['id'],'--yes')
    document=json.loads(run('manifest','export','--library','collection'))
    target=Config(tmp_path/'target-data');newroot=tmp_path/'target-originals';shutil.copytree(copied,newroot)
    def target_run(*args):
        result=cli(target,*map(str,args),input_text=password+'\n' if '--password-stdin' in args else '')
        assert result.returncode==0,(args,result.stdout,result.stderr)
        return result.stdout
    target_run('init','--admin','restored','--password-stdin')
    target_run('library','add','Restored collection',newroot,'--mode','managed')
    target_run('scan','Restored collection','--json')
    file=tmp_path/'metadata.json';file.write_text(json.dumps(document))
    mappings={'users':{document['owner']:1},'libraries':{document['records']['libraries'][0]['id']:1}}
    migrated=json.loads(target_run('manifest','preview',file,'--mappings',json.dumps(mappings)))
    assert migrated['preview']['ready'],migrated['preview']['conflicts']
    target_run('manifest','execute',migrated['id'],'--yes')
    target_images=json.loads(target_run('ls','--sort','path','--json'))
    new_zero=next(item['id'] for item in target_images['items'] if item['rel_path']=='renamed/first.png')
    restored_image=json.loads(target_run('show',new_zero,'--json'))
    assert restored_image['my_rating']==0 and restored_image['favorite']
    with db.open_db(target.db_path) as conn:
        assert conn.execute('SELECT body FROM comments WHERE image_id=?',(new_zero,)).fetchone()[0]=='Known journey comment'
    conn.close()
    archive=tmp_path/'backup.zip';target_run('export',archive,'--with-thumbs')
    assert json.loads(target_run('backup','inspect',archive))['validated']
    restored_cfg=Config(tmp_path/'backup-restored')
    result=cli(restored_cfg,'import-backup',str(archive));assert result.returncode==0,result.stderr
    assert json.loads(cli(restored_cfg,'check','--full').stdout)['counts']=={}
    from picshelf.tui import PicShelfTUI
    async def keyboard():
        app=PicShelfTUI(restored_cfg)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause();await pilot.press('/',*list('file:first'),'enter');await pilot.pause()
            await pilot.press('r','end','backspace','1','enter');await pilot.pause()
            if app.screen.query('#confirm-yes'):
                await pilot.click('#confirm-yes');await pilot.pause(.5)
            assert app.conn.execute('SELECT value FROM ratings WHERE image_id=? AND user_id=1',(new_zero,)).fetchone()[0]==1
    asyncio.run(keyboard())
    assert file_inventory(originals)==original_inventory
    assert len(list(newroot.rglob('*.png')))==12
