"""Exercise an installed distribution without importing the source checkout."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
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
    probe = subprocess.run([str(python),'-c', 'import json,picshelf;from pathlib import Path;print(json.dumps({"version":picshelf.__version__,"module":picshelf.__file__,"static":(Path(picshelf.__file__).parent/"web/js/viewer.js").is_file()}))'],cwd=root,env=env,text=True,capture_output=True,check=True)
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
    return {'installed_version':info['version'],'source_imported':False,'static_resources':True,
            'images':2,'managed_copy_independent':True,'rating_zero_preserved':True,
            'backup_inspected_and_restored':True,'read_only_check_passed':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--python',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.python.absolute(),args.root)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
