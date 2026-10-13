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
