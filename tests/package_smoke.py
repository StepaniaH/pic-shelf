"""Exercise an installed distribution without importing the source checkout."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess


def run(python, root):
    root = root.resolve(); root.mkdir(parents=True)
    env = dict(os.environ, PICSHELF_LANG='zh', NO_COLOR='1', TMPDIR=str(root))
    env.pop('PYTHONPATH', None); env.pop('PICSHELF_DATA', None)
    def command(*args, input_text='', success=True):
        result = subprocess.run([str(python), '-m', 'picshelf', *map(str,args)],cwd=root,env=env,input=input_text,text=True,capture_output=True,timeout=40)
        if success:
            assert result.returncode == 0, (args,result.stdout,result.stderr)
        return result
    probe_code = '''
import json
from pathlib import Path
import picshelf
from picshelf.db import SCHEMA_VERSION

web = Path(picshelf.__file__).parent / 'web'
required = ('index.html', 'css/app.css', 'js/app.js', 'js/viewer.js', 'js/theme.js',
            'js/appearance.js', 'js/importer.js', 'js/import-files.js', 'favicon.ico',
            'icons/favicon-32.png', 'icons/apple-touch-icon.png', 'site.webmanifest')
manifest = json.loads((web / 'site.webmanifest').read_text())
static = all((web / name).is_file() for name in required)
static = static and all((web / icon['src']).is_file() for icon in manifest['icons'])
print(json.dumps({'version': picshelf.__version__, 'module': picshelf.__file__,
                  'schema': SCHEMA_VERSION, 'static': static}))
'''
    probe = subprocess.run([str(python), '-c', probe_code], cwd=root, env=env,
                           text=True, capture_output=True, check=True)
    info=json.loads(probe.stdout)
    assert Path(info['module']).is_relative_to(python.parent.parent) and info['static'] and info['version']=='0.0.1'
    assert command('init',success=False).returncode == 1  # Installed defaults never write site-packages.
    source=root/'originals';source.mkdir()
    data=root/'data';restored=root/'restored'
    original=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')
    (source/'sample.png').write_bytes(original)
    digest=hashlib.sha256(original).hexdigest()
    password=secrets.token_urlsafe(24)
    command('--data',data,'init','--admin','admin','--password-stdin',input_text=password+'\n')
    command('--data',data,'library','add','indexed',source,'--mode','indexed')
    command('--data',data,'scan','indexed','--json')
    command('--data',data,'library','add','managed',root/'managed','--mode','managed','--mkdir')
    preview=json.loads(command('--data',data,'import',source,'managed','--mode','copy','--json').stdout)
    assert preview['stats']['copy']==1 and not (root/'managed/sample.png').exists()
    executed=json.loads(command('--data',data,'import-execute',preview['plan_id'],'--yes').stdout)
    assert executed['state']=='succeeded'
    images=json.loads(command('--data',data,'ls','--library','managed','--json').stdout)
    assert images['total']==1
    image_id=images['items'][0]['id']
    command('--data',data,'rate',image_id,'0','--json')
    command('--data',data,'tag','add',image_id,'sample')
    exported=json.loads(command('--data',data,'manifest','export','--library','managed').stdout)
    assert exported['records']['ratings'][0]['value']==0
    queued=json.loads(command('--data',data,'scan','indexed','--background','--json').stdout)
    archive=root/'backup.zip'
    command('--data',data,'export',archive,'--with-thumbs')
    inspected=json.loads(command('--data',data,'backup','inspect',archive).stdout)
    assert inspected['validated'] and not inspected['contains']['originals']
    command('--data',restored,'import-backup',archive)
    shown=json.loads(command('--data',restored,'show',image_id,'--json').stdout)
    assert shown['my_rating']==0 and shown['sha256']==digest
    assert (source/'sample.png').read_bytes()==original and (root/'managed/sample.png').read_bytes()==original
    assert (source/'sample.png').stat().st_ino != (root/'managed/sample.png').stat().st_ino
    report=json.loads(command('--data',restored,'check','--full').stdout)
    assert not report['counts']
    assert json.loads(command('--data',restored,'doctor','--full').stdout)['ready']
    with sqlite3.connect(restored/'picshelf.db') as conn:
        assert conn.execute('SELECT state FROM jobs WHERE id=?',(queued['id'],)).fetchone()[0]=='interrupted'
        assert conn.execute('SELECT MIN(token_version) FROM users').fetchone()[0]>0
    upgrade=root/'upgrade';upgrade.mkdir()
    with sqlite3.connect(upgrade/'picshelf.db') as conn:
        conn.executescript((Path(__file__).parent/'fixtures/schema_v1.sql').read_text())
        conn.execute("INSERT INTO users VALUES(7,'legacy','invalid-fixture-hash','Legacy',1,0,'2026-10-01T00:00:00+00:00')")
        conn.execute("INSERT INTO groups(id,name,created_at) VALUES(13,'legacy-group','2026-10-01T00:00:00+00:00')")
        conn.execute('INSERT INTO user_groups VALUES(7,13)')
        conn.execute("INSERT INTO libraries VALUES(19,'legacy',?,NULL,'2026-10-01T00:00:00+00:00')",(str(source),))
        conn.execute("INSERT INTO library_grants VALUES(13,19,'manage')")
        conn.execute("INSERT INTO images(id,library_id,rel_path,filename,ext,size_bytes,mtime,sha256,imported_at) VALUES(31,19,'sample.png','sample.png','png',?,?,?,'2026-10-01T00:00:00+00:00')",(len(original),(source/'sample.png').stat().st_mtime,digest))
        conn.execute("INSERT INTO ratings VALUES(31,7,0,'2026-10-01T00:00:00+00:00')")
        conn.execute("INSERT INTO favorites VALUES(31,7,'2026-10-01T00:00:00+00:00')")
        conn.commit()
        before={name:list(conn.execute(f'SELECT * FROM {name}')) for name in ('ratings','favorites','user_groups','library_grants')}
    upgraded=json.loads(command('--data',upgrade,'ls','--json').stdout)
    assert upgraded['items'][0]['id']==31 and upgraded['items'][0]['my_rating']==0
    with sqlite3.connect(upgrade/'picshelf.db') as conn:
        upgraded_schema=int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        assert upgraded_schema==info['schema']
        assert {name:list(conn.execute(f'SELECT * FROM {name}')) for name in before}==before
    snapshots=list((upgrade/'migration-backups').glob('*.db'))
    assert len(snapshots)==1 and snapshots[0].stat().st_mode & 0o077==0
    with sqlite3.connect(snapshots[0]) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='1'
        assert {name:list(conn.execute(f'SELECT * FROM {name}')) for name in before}==before
    return {'installed_version':info['version'],'source_imported':False,'static_resources':True,
            'images':2,'managed_copy_independent':True,'rating_zero_preserved':True,
            'backup_inspected_and_restored':True,'read_only_check_passed':True,
            'restored_jobs_interrupted':True,'restored_sessions_invalidated':True,
            'upgrade_from_schema':1,'upgraded_schema':upgraded_schema,'upgrade_metadata_preserved':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--python',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.python.absolute(),args.root)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
