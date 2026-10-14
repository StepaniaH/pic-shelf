"""Local, redacted diagnostics; never upload or modify catalog contents."""
from collections import Counter
import importlib.metadata
import os
import platform
import shutil
import sqlite3

from . import __version__, checks, db
from .errors import ServiceError
from .i18n import t


def doctor(cfg, *, full=False):
    report = {'type': 'picshelf.diagnostics', 'format': 1, 'version': __version__,
              'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
              'system': platform.system(), 'architecture': platform.machine(),
              'schema': {'expected': db.SCHEMA_VERSION, 'observed': None},
              'data': {'exists': cfg.data_dir.is_dir(), 'database_exists': cfg.db_path.is_file(),
                       'readable': os.access(cfg.data_dir, os.R_OK),
                       'writable_hint': os.access(cfg.data_dir, os.W_OK)},
              'capabilities': {name: bool(shutil.which(name)) for name in ('mpv','chafa','img2txt','docker')},
              'dependencies': {}, 'jobs': {}, 'integrity': None, 'issues': [], 'uploaded': False}
    for name in ('fastapi','pydantic','pillow','textual','uvicorn','cryptography'):
        try:
            report['dependencies'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report['dependencies'][name] = None
    if report['data']['exists'] and not report['data']['writable_hint']:
        report['issues'].append({'code':'data_directory_unwritable',
                                'next_step':t('请修复目录权限，或明确指定可写目录：picshelf --data <数据目录> init')})
    if not cfg.db_path.is_file():
        report['issues'].append({'code':'not_initialized','next_step':t('请先运行 picshelf init --admin <管理员名>')})
    else:
        try:
            checked = checks.check(cfg, full=full)
            report['schema']['observed'] = checked['schema']
            report['integrity'] = {'counts':checked['counts'],'bytes':checked['bytes'],
                                   'libraries':dict(Counter(library['state'] for library in checked['libraries'])),
                                   'full_hash_check':full}
            report['storage'] = {**checked['storage'], 'cleanup_preview': {
                key: value for key, value in checked['storage']['cleanup_preview'].items() if key != 'candidates'}}
            if set(checked['counts']) - {'derivative_unreferenced'}:
                report['issues'].append({'code':'integrity_findings','next_step':t('请运行 check --full 查看具体问题；先核对备份，再单独修复')})
            conn = sqlite3.connect(cfg.db_path.resolve().as_uri()+'?mode=ro',uri=True)
            try:
                if checked['schema'] >= 3:
                    known={'queued','running','cancelling','cancelled','succeeded','partial','failed','interrupted'}
                    for state,count in conn.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'):
                        key=state if state in known else 'unknown'
                        report['jobs'][key]=report['jobs'].get(key,0)+count
            finally:
                conn.close()
        except ServiceError as exc:
            known = {'invalid_schema','unsupported_schema','operation_busy','coordination_unavailable','database_integrity'}
            report['issues'].append({'code':exc.code if exc.code in known else 'catalog_check_failed',
                                    'next_step':exc.next_step if exc.code in known else t('请停止服务并检查数据库备份；不要删除原图或数据库')})
        except (sqlite3.Error, OSError):
            report['issues'].append({'code':'catalog_check_failed','next_step':t('请停止服务并检查数据库备份；不要删除原图或数据库')})
    report['ready'] = not report['issues']
    return report
