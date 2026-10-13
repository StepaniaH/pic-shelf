# Generated catalog

`catalog.json` specifies 24 images, two libraries and three users. Search results
are hand-authored sets, independent of the query implementation. The reader can
see only the north library; the outsider cannot see either library. Ratings and
favorites belong to the reader. Credentials are generated at runtime.

`tests/datasets.py` creates small images and observes their bytes and file identity
without importing application code. It also provides filesystem boundary samples,
EXIF and transparency samples, fault scenarios, and deterministic metadata rows for
1,000 or 10,000 record measurements (100,000 is optional). Metadata rows do not
represent image decoding throughput. Synthetic ICC samples are not photographic
color references. Filesystem probes report case, Unicode, hardlink, symlink and
permission capabilities; unsupported semantics must not be reported as tested.

Keep generated large datasets, manifests, screenshots, and run logs in an ignored
data directory. Real material is read only from `PICSHELF_TEST_MATERIALS`, when
explicitly provided; tests that write files must use independent copies.

Run process, service, and Textual scenarios with:

```sh
.venv/bin/python -m pytest tests/test_scenarios.py -q
PICSHELF_HTTP_TESTS=1 .venv/bin/python -m pytest tests/test_scenarios.py -q
PICSHELF_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_web.py -q
```

The HTTP option uses a real loopback server. Browser tests require Chromium. Report
blocked launches separately from failed business assertions; do not convert a
failure into a passing test by weakening its expected result.

`schema_v1.sql` is a frozen compatibility fixture from the released v1 schema.
Do not regenerate it from current migrations. Migration tests populate independent
nonconsecutive IDs and relationships, then compare the old data and structure
after upgrades, failures, process interruption, and snapshot restoration.
