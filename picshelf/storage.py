"""Read-only apparent file sizes and bounded cache cleanup candidates."""
from collections import Counter
import os
from pathlib import Path
import sqlite3
import stat

JOURNALS = ('jobs', 'job_items', 'import_plans', 'import_plan_items',
            'metadata_batches', 'metadata_batch_items', 'play_requests')
DEVELOPMENT = {'maintenance', 'verification', 'benchmarks', 'packages', 'package-check'}


def inspect_storage(cfg, conn, referenced):
    sizes, files = Counter(), Counter()
    candidates = []
    unreferenced = skipped = unreadable = 0
    root = cfg.data_dir.resolve()
    def walk_error(_):
        nonlocal unreadable
        unreadable += 1
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        skipped += sum((Path(directory) / name).is_symlink() for name in dirs)
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            try:
                observed = path.lstat()
                if not stat.S_ISREG(observed.st_mode) or not path.resolve().is_relative_to(root):
                    skipped += 1
                    continue
                rel = path.relative_to(root)
                if rel.as_posix() == cfg.db_path.name:
                    category = 'database'
                elif rel.as_posix() == cfg.db_path.name + '-wal':
                    category = 'wal'
                elif rel.as_posix() == cfg.db_path.name + '-shm':
                    category = 'shared_memory'
                elif rel.parts[0] == 'media':
                    if name.startswith('.'):
                        category = 'derivatives_temporary'
                    elif path.resolve() in referenced:
                        category = 'derivatives_referenced'
                    else:
                        category = 'derivatives_unreferenced'
                        if path.suffix == '.webp':
                            unreferenced += 1
                            if len(candidates) < 200:
                                candidates.append({'path': rel.as_posix(), 'bytes': observed.st_size})
                elif rel.parts[0] in ('exports', 'migration-backups') or name.startswith('picshelf.db.bak-'):
                    category = 'backups'
                elif rel.parts[0] in DEVELOPMENT:
                    category = 'development'
                else:
                    category = 'other_data'
                sizes[category] += observed.st_size
                files[category] += 1
            except OSError:
                unreadable += 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    journal_rows = {name: conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                    for name in JOURNALS if name in tables}
    try:
        page_bytes = {row[0]: row[1] for row in conn.execute('SELECT name,SUM(pgsize) FROM dbstat GROUP BY name')
                      if row[0] in JOURNALS}
    except sqlite3.Error:
        page_bytes = None
    return {'read_only': True, 'measurement': 'apparent bytes by file path; hardlinks counted per path',
            'bytes': dict(sizes), 'files': dict(files), 'total_bytes': sum(sizes.values()),
            'unreadable_entries': unreadable, 'skipped_links_or_special_files': skipped,
            'journal_rows': journal_rows, 'journal_table_page_bytes': page_bytes,
            'cleanup_preview': {'candidates': candidates, 'total_candidates': unreferenced,
                                'truncated': unreferenced > len(candidates), 'apply_supported': False,
                                'requires_reference_and_identity_recheck': True}}
