"""Separate geometric decode load, concurrent catalog reads and cached ASGI media."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import platform
import resource
import secrets
import threading
import time

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from picshelf import auth, catalog, db, indexer, libsvc
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.config import Config
from tests.benchmarks.catalog import measure
from tests.datasets import metadata_rows


def run(root, rounds=30):
    cfg = Config(root / 'data').ensure()
    files, source = [], root / 'mixed'
    source.mkdir()
    for index, (format, suffix, size) in enumerate([
        ('JPEG','jpg',(4000,2000)), ('PNG','png',(3000,2000)),
        ('BMP','bmp',(3000,2000)), ('TIFF','tif',(2000,3000)),
        ('WEBP','webp',(2000,2000)), ('PNG','png',(4000,3000)),
    ]):
        path = source / f'{index}.{suffix}'
        with Image.new('RGB', size, (40+index*20,90,180)) as image:
            ImageDraw.Draw(image).rectangle((100,50,size[0]//2,size[1]//2),fill=(230,40,60))
            image.save(path,format=format)
        files.append({'path': path.name,'format': format,'pixels': size[0]*size[1],
                      'bytes': path.stat().st_size,'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    fake = root / 'metadata-only'
    fake.mkdir()
    with closing(db.open_db(cfg.db_path)) as conn:
        auth.ensure_default_groups(conn)
        password = secrets.token_urlsafe(24)
        user = auth.create_user(conn,'benchmark',password,is_admin=True)
        meta_library = libsvc.create_library(conn,'Metadata',str(fake))
        decode_library = libsvc.create_library(conn,'Geometric images',str(source))
        rows = list(metadata_rows(10000,meta_library)); fields=list(rows[0])
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",[list(row.values()) for row in rows]);conn.commit()
        result = {}
        started = threading.Event()
        def scan():
            with closing(db.open_db(cfg.db_path)) as worker:
                started.set(); begin=time.perf_counter()
                result['scan'] = indexer.scan_library(worker,cfg,decode_library)
                result['first_scan_seconds'] = time.perf_counter()-begin
        worker = threading.Thread(target=scan)
        worker.start(); started.wait()
        spec = catalog.ImageQuery(library_id=meta_library,sort='path',limit=120)
        result['queries_during_other_library_decode'] = measure(lambda: catalog.query_images(conn,AccessContext(user),spec),rounds)
        worker.join()
        assert result['scan']['added'] == len(files) and not result['scan']['errors']
        start=time.perf_counter(); second=indexer.scan_library(conn,cfg,decode_library,full=True)
        result['warm_full_scan_seconds'] = time.perf_counter()-start
        assert second['updated'] == len(files) and not second['errors']
        image_id=conn.execute('SELECT id FROM images WHERE library_id=? ORDER BY id LIMIT 1',(decode_library,)).fetchone()[0]
    with TestClient(create_app(cfg)) as client:
        token=client.post('/api/v1/auth/login',json={'username':'benchmark','password':password}).json()['token']
        client.headers['Authorization']='Bearer '+token
        def cached_media():
            response=client.get(f'/api/v1/media/thumb/{image_id}')
            assert response.status_code == 200 and response.headers['content-type'].startswith('image/webp')
            return len(response.content)
        result['cached_thumb_bytes']=cached_media()
        result['cached_thumb_asgi']=measure(cached_media,rounds)
    for file in files:
        assert hashlib.sha256((source / file['path']).read_bytes()).hexdigest() == file['sha256']
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'files': files,'total_pixels':sum(file['pixels'] for file in files),
            'total_bytes':sum(file['bytes'] for file in files),'peak_rss_bytes':rss if platform.system()=='Darwin' else rss*1024,
            'derivative_bytes':sum(path.stat().st_size for path in cfg.media_dir.rglob('*.webp')),
            'measurements':result,'limitations':['Generated geometric images, no photographic/illustration quality claims','OS caches were not flushed','ASGI timings exclude sockets, browser decode, DOM and image switching']}


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run(args.output.parent / ('pipeline-'+str(time.time_ns())))
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key not in ('files',)},ensure_ascii=False))
