"""Reproducible metadata workload; results are stored outside version control."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import resource
import secrets
import statistics
import time

from picshelf import auth, catalog, db, jobs, libsvc, manifest, ops
from picshelf.access import AccessContext
from picshelf.config import Config
from tests.datasets import metadata_rows, SEED


def measure(operation, rounds):
    values = []
    for _ in range(rounds):
        start = time.perf_counter()
        operation()
        values.append((time.perf_counter() - start) * 1000)
    ordered = sorted(values)
    return {'rounds': rounds, 'median_ms': statistics.median(values),
            'p95_ms': ordered[min(len(ordered)-1, int(len(ordered)*.95))], 'max_ms': max(values)}


def run(root, count, rounds, distribution='uniform'):
    cfg = Config(root / 'data').ensure()
    originals = root / 'originals'
    originals.mkdir()
    with closing(db.open_db(cfg.db_path)) as conn:
        auth.ensure_default_groups(conn)
        user = auth.create_user(conn, 'benchmark', secrets.token_urlsafe(24), is_admin=True)
        library = libsvc.create_library(conn, 'Generated metadata', str(originals))
        rows = list(metadata_rows(count, library))
        rng = random.Random(SEED)
        fingerprints = [rng.getrandbits(64) for _ in range(100)]
        if distribution != 'uniform':
            for index, row in enumerate(rows):
                value = fingerprints[index % 100] if distribution == 'duplicates' else rng.getrandbits(48) << 16
                row['phash'] = f'{value:016x}'
        fields = list(rows[0])
        start = time.perf_counter()
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", [list(row.values()) for row in rows])
        conn.executemany('INSERT INTO ratings VALUES(?,?,?,?)', [(number+1,user,number%11,db.utcnow()) for number in range(count) if number%3])
        tag_ids = [ops.add_tags(conn, 1, [f'group-{index}'], user)[0]['id'] for index in range(20)]
        conn.execute('DELETE FROM image_tags')
        conn.executemany('INSERT INTO image_tags VALUES(?,?,?,?)', [(number+1,tag_ids[number%20],user,db.utcnow()) for number in range(count)])
        conn.commit()
        insert_ms = (time.perf_counter()-start)*1000
        access = AccessContext(user)
        specs = {'path': catalog.ImageQuery(sort='path', limit=120),
                 'path_offset': catalog.ImageQuery(sort='path', limit=120, offset=count//2),
                 'tag': catalog.ImageQuery(query='tag:group-7', sort='path', limit=120),
                 'unrated': catalog.ImageQuery(query='rated:false', sort='path', limit=120),
                 'zero': catalog.ImageQuery(query='rated:true rating:0', sort='path', limit=120)}
        expected = {'path': list(range(1,count+1)), 'path_offset': list(range(1,count+1)),
                    'tag': [n+1 for n in range(count) if n%20==7],
                    'unrated': [n+1 for n in range(count) if n%3==0],
                    'zero': [n+1 for n in range(count) if n%3 and n%11==0]}
        result = {'queries': {}, 'query_plans': {}}
        for name, spec in specs.items():
            observed = catalog.query_images(conn, access, spec)
            assert observed['total'] == len(expected[name])
            assert [image['id'] for image in observed['items']] == expected[name][spec.offset:spec.offset+spec.limit]
            result['queries'][name] = measure(lambda: catalog.query_images(conn, access, spec), rounds)
        result['query_plans']['path'] = [tuple(row) for row in conn.execute('EXPLAIN QUERY PLAN SELECT id FROM images WHERE missing=0 ORDER BY rel_path,id LIMIT 120')]
        def new_connection():
            with closing(db.open_db(cfg.db_path)) as cold:
                catalog.query_images(cold, access, specs['path'])
        result['new_connection_page_cache'] = measure(new_connection, rounds)
        serial = iter(range(rounds))
        result['task_submit'] = measure(lambda: jobs.submit(conn, access, 'scan', library, request_key=f'measure-{next(serial)}'), rounds)
        job_id = conn.execute('SELECT id FROM jobs ORDER BY created_at,id LIMIT 1').fetchone()[0]
        result['task_status'] = measure(lambda: jobs.get_job(conn, access, job_id), rounds)
        small_root = root / 'small-scope'
        small_root.mkdir()
        small_library = libsvc.create_library(conn, 'Small export scope', str(small_root))
        small_rows = list(metadata_rows(10, small_library))
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", [list(row.values()) for row in small_rows]); conn.commit()
        assert len(manifest.export(conn, access, [small_library])['records']['images']) == 10
        result['small_manifest_export'] = measure(lambda: manifest.export(conn, access, [small_library]), rounds)
        result['preview_fingerprint'] = measure(lambda: manifest._fingerprint(conn), rounds)
        start = time.perf_counter()
        similar = libsvc.similar_dupes(conn, threshold=8)
        result['similarity'] = {'elapsed_ms': (time.perf_counter()-start)*1000,
                                'groups': len(similar), 'result_sha256': hashlib.sha256(json.dumps(similar, sort_keys=True).encode()).hexdigest(),
                                'distribution': distribution + ' deterministic fingerprints; metadata only'}
        conn.execute("UPDATE jobs SET state='cancelled'"); conn.commit()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'environment': {'python': platform.python_version(), 'sqlite': __import__('sqlite3').sqlite_version,
                            'system': platform.system(), 'architecture': platform.machine(), 'cpu_count': os.cpu_count(),
                            'peak_rss_bytes': rss if platform.system() == 'Darwin' else rss*1024},
            'dataset': {'kind': 'generated metadata, no decoded images', 'count': count, 'seed': SEED, 'distribution': distribution},
            'budget_ms': {'warm_query_p95': 300, 'task_submit_status_p95': 500},
            'insert_ms': insert_ms, 'measurements': result,
            'limitations': ['OS and disk caches were not flushed', 'No original decode or browser interaction in this workload']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=30)
    parser.add_argument('--distribution', choices=['uniform', 'duplicates', 'shared-segment'], default='uniform')
    args = parser.parse_args()
    if not 1000 <= args.count <= 100000 or args.rounds < 30:
        parser.error('use 1,000–100,000 records and at least 30 rounds')
    root = args.output.parent / ('catalog-' + str(time.time_ns()))
    report = run(root, args.count, args.rounds, args.distribution)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report['measurements'], ensure_ascii=False))
