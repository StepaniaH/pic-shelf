"""Media URL contracts independent of a browser process."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_algorithm_rebuild_changes_gallery_cache_key_but_keeps_original_url():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/api.js").as_uri()
    script = """
globalThis.localStorage = {getItem: () => 'en'};
const {imageMediaUrl} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const image = {id: 7, sha256: '0123456789abcdef0123456789abcdef', derivative_version: 2};
assert.equal(imageMediaUrl('thumb', image), '/api/v1/media/thumb/7?v=0123456789abcdef-d2');
assert.equal(imageMediaUrl('preview', {...image, derivative_version: 3}), '/api/v1/media/preview/7?v=0123456789abcdef-d3');
assert.equal(imageMediaUrl('original', image), '/api/v1/media/original/7?v=0123456789abcdef');
assert.equal(imageMediaUrl('original', {...image, derivative_version: 3}), '/api/v1/media/original/7?v=0123456789abcdef');
assert.equal(imageMediaUrl('preview', {...image, preview_url: '/api/v1/media/preview/7?v=server-version'}), '/api/v1/media/preview/7?v=server-version');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module], text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_user_links_cannot_be_active_code_or_contain_credentials():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/format.js").as_uri()
    script = r"""
const {externalUrl} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
for (const value of ['javascript:alert(1)', 'data:text/html,payload', '//example.org', 'https://user:secret@example.org/', 'https://example.org/\u000aalert', null]) {
  assert.equal(externalUrl(value), null);
}
assert.equal(externalUrl('https://example.org/work?q=hello#details'), 'https://example.org/work?q=hello#details');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_displayed_times_use_explicit_utc_without_guessing_naive_dates():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/format.js").as_uri()
    script = """
const {formatTimestamp} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
assert.equal(formatTimestamp('2026-10-10T21:35:00+08:00'), '2026-10-10 13:35:00 UTC');
assert.equal(formatTimestamp('2020-01-02T03:04:05-05:00'), '2020-01-02 08:04:05 UTC');
assert.equal(formatTimestamp('2020-01-02T03:04:05'), '—');
assert.equal(formatTimestamp(null), '—');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_selection_keeps_explicit_cross_page_ids_and_separates_all_matching():
    node=shutil.which("node")
    if not node:pytest.skip("Node.js is required for selection contracts")
    module=(Path(__file__).parents[1]/"picshelf/web/js/selection.js").as_uri()
    script="""
const {ImageSelection}=await import(process.argv[1]);
const {strict:assert}=await import('node:assert');
const selected=new ImageSelection();
const source={q:'tag:blue',sort:'path',filters:{library_id:3}};
selected.toggle(7,true);selected.toggle(131,true);
assert.deepEqual(selected.payload(source),{image_ids:[7,131]});
selected.loaded([{id:7},{id:9}]);
assert.deepEqual(selected.payload(source),{image_ids:[7,131,9]});
selected.toggle(7,false);
assert.deepEqual(selected.payload(source),{image_ids:[131,9]});
selected.all();
assert.deepEqual(selected.payload(source),{query:{query:'tag:blue',sort:'path',library_id:3}});
selected.clear();assert.deepEqual(selected.payload(source),{image_ids:[]});
"""
    result=subprocess.run([node,"--input-type=module","-e",script,module],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
