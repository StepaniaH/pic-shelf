"""Capacity diagnostics keep cache candidates local, bounded and read-only."""
import json

from picshelf import checks, db, diagnostics
from tests.scenario_support import build_catalog, metadata_snapshot


def test_storage_accounts_for_cache_backups_and_development_without_deleting(tmp_path):
    data = build_catalog(tmp_path / 'catalog')
    root = data.cfg.data_dir
    orphan = data.cfg.thumbs_dir / 'unused.webp'
    orphan.write_bytes(b'orphan-cache')
    temporary = data.cfg.thumbs_dir / '.encode-active.webp'
    temporary.write_bytes(b'in-progress')
    backup = root / 'exports/catalog.zip'
    backup.write_bytes(b'backup')
    development = root / 'maintenance/report.txt'
    development.parent.mkdir(); development.write_bytes(b'development')
    outside = tmp_path / 'private-secret-file'
    outside.write_bytes(b'private-value')
    (root / 'outside-link').symlink_to(outside)
    with db.open_db(data.cfg.db_path) as conn:
        before = metadata_snapshot(conn)
        report = checks.check(data.cfg)['storage']
        assert report['bytes']['derivatives_unreferenced'] == orphan.stat().st_size
        assert report['bytes']['derivatives_temporary'] == temporary.stat().st_size
        assert report['bytes']['backups'] == backup.stat().st_size
        assert report['bytes']['development'] == development.stat().st_size
        assert report['total_bytes'] == sum(report['bytes'].values())
        assert report['cleanup_preview']['candidates'] == [{'path': 'media/thumbs/unused.webp', 'bytes': orphan.stat().st_size}]
        assert report['skipped_links_or_special_files'] == 1
        assert not report['cleanup_preview']['apply_supported']
        assert report['journal_rows']['jobs'] == 0
        assert 'private' not in json.dumps(report) and str(root) not in json.dumps(report)
        assert diagnostics.doctor(data.cfg)['storage']['cleanup_preview']['total_candidates'] == 1
        assert metadata_snapshot(conn) == before
    assert orphan.exists() and temporary.exists() and outside.read_bytes() == b'private-value'


def test_cleanup_preview_is_bounded_and_excludes_referenced_outputs(tmp_path):
    data = build_catalog(tmp_path)
    for number in range(205):
        (data.cfg.thumbs_dir / f'unused-{number}.webp').write_bytes(b'x')
    result = checks.check(data.cfg)['storage']['cleanup_preview']
    assert result['total_candidates'] == 205 and len(result['candidates']) == 200 and result['truncated']
    assert all(item['path'].startswith('media/thumbs/unused-') for item in result['candidates'])
