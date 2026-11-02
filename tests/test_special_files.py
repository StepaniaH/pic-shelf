"""Special filesystem entries must not block import previews or scans."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX named pipes required')
@pytest.mark.parametrize('operation', ['preview', 'scan'])
@pytest.mark.parametrize('entry', ['fifo', 'fifo-link'])
def test_special_image_entry_is_reported_without_blocking(tmp_path, operation, entry):
    script = '''
import os, sys
from pathlib import Path
from PIL import Image
from picshelf import db, importer, indexer, libsvc
from picshelf.config import Config
from picshelf.i18n import set_lang

set_lang('en')
base = Path(sys.argv[1])
source, target = base / 'source', base / 'target'
source.mkdir(); target.mkdir()
Image.new('RGB', (8, 8), 'red').save(source / 'good.png')
pipe = source / ('blocked.png' if sys.argv[3] == 'fifo' else 'pipe')
os.mkfifo(pipe)
if sys.argv[3] == 'fifo-link':
    (source / 'blocked.png').symlink_to(pipe.name)
cfg = Config(base / 'data').ensure()
conn = db.open_db(cfg.db_path)
if sys.argv[2] == 'preview':
    library = libsvc.create_library(conn, 'fixture', str(target))
    result = importer.plan_import(conn, libsvc.get_library(conn, library), source, mode='copy')
    assert result['stats']['copy'] == 1 and result['stats']['error'] == 1, result
    error = next(item for item in result['items'] if item['action'] == 'error')
    assert Path(error['src']).name == 'blocked.png' and 'regular file' in error['reason'], error
    assert list(target.iterdir()) == []
else:
    library = libsvc.create_library(conn, 'fixture', str(source))
    result = indexer.scan_library(conn, cfg, library)
    assert result['added'] == 1 and len(result['errors']) == 1, result
    assert 'blocked.png' in result['errors'][0] and 'regular file' in result['errors'][0], result
    assert [row[0] for row in conn.execute('SELECT filename FROM images')] == ['good.png']
assert pipe.exists() and (source / 'good.png').exists()
conn.close()
print('completed without reading the pipe', flush=True)
'''
    result = subprocess.run(
        [sys.executable, '-B', '-c', script, str(tmp_path), operation, entry],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'completed without reading the pipe'


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX named pipes required')
@pytest.mark.parametrize('reader', ['verified', 'decode'])
def test_source_replaced_with_fifo_is_rejected_before_open(tmp_path, reader):
    script = '''
import os, sys
from pathlib import Path
from picshelf import hashing, import_plans
from picshelf.errors import ServiceError

path = Path(sys.argv[1]) / 'changed.png'
path.write_bytes(b'original')
identity = import_plans._source_identity(path.stat())
digest = hashing.sha256_file(path)
path.unlink()
os.mkfifo(path)
try:
    if sys.argv[2] == 'verified':
        with import_plans._verified_source(path, identity, digest):
            raise AssertionError('opened a pipe')
    else:
        hashing._open_image(path)
except ServiceError as error:
    assert error.code == 'file_not_regular', error.code
else:
    raise AssertionError('a special file was not rejected')
assert path.exists()
print('file_not_regular', flush=True)
'''
    result = subprocess.run(
        [sys.executable, '-B', '-c', script, str(tmp_path), reader],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'file_not_regular'
