# Performance and similarity

Measure metadata scale separately from original-image decoding. The reproducible
drivers create their own fixtures under the report directory:

```sh
python -m tests.benchmarks.catalog --output ./data/benchmarks/catalog.json
python -m tests.benchmarks.catalog --distribution shared-segment --output ./data/benchmarks/shared.json
python -m tests.benchmarks.pipeline --output ./data/benchmarks/pipeline.json
```

The catalog driver uses 10,000 generated rows by default, fixed seed `20261013`,
20 tag groups, known rated/unrated subsets and 30 rounds. It checks expected
result IDs using arithmetic membership rules. `new_connection_page_cache` creates
fresh SQLite connections; it does not flush OS or disk caches. The pipeline
driver uses six generated geometric JPEG/PNG/BMP/TIFF/WebP images and checks that
original bytes remain unchanged. Reports and generated fixtures belong in the
ignored data directory.

## Reference measurements

Measured on macOS arm64, 10 logical CPUs, Python 3.11.15 and SQLite 3.53.1:

| Workload | Observed result |
| --- | --- |
| 10,000 rows, common warm queries, 30 rounds | p95 approximately 1–10 ms |
| Task submit/status, 30 rounds | p95 below 0.3 ms / 0.05 ms |
| Small 10-image export alongside 10,000 unrelated rows | p95 12.82 ms before SQL scoping, 0.41 ms after |
| Preview metadata revision, same database | p95 71.75 ms before full-table hashing, below 0.01 ms with tracked revision |
| 10,000 uniform synthetic fingerprints, radius 8 | 1.69 s before candidate indexing, 0.36 s after; identical groups |
| 10,000 fingerprints sharing 16 bits, radius 8 | 1.67 s before, 1.78 s after; identical 92 groups |
| Six geometric images, 42 million pixels, 36.2 MB | Initial scan 0.83 s; warm full scan 0.64 s; process peak RSS approximately 385 MB |
| Query during the other library's decode, 30 rounds | p95 1.06 ms |
| Cached thumbnail through ASGI, 30 rounds | p95 2.48 ms; response 1,076 bytes |
| Per-item progress for 200 generated import items | 40,200 row writes before incremental updates, 400 after; identical final counters |

The progress comparison uses SQLite `total_changes` in memory, excluding file
journal updates and initial/final reconciliation. The regression test also
checks failed-to-successful retries and repeated notifications.

These are observations, not capacity or hardware guarantees. Synthetic
fingerprints do not assess perceptual recommendation quality. Geometric images
are highly compressible and do not represent photography or illustration. ASGI
measurements exclude sockets, browser decoding, rendering and interaction. The
150 ms adjacent-image switching target requires an actual browser measurement.
The working budgets remain 300 ms p95 for warm common 10,000-row queries and
500 ms p95 for task submission/status; OS-cold disk behavior is not established.

## Similarity semantics

Exact duplicates group by SHA-256 within current visible libraries. Similarity
groups by the first representative in image-ID order, with every member at or
below the chosen 64-bit Hamming radius. It is not transitive clustering and does
not identify a work, crop or original. Different fingerprint algorithm versions
are never compared. Files and organization are never merged or removed.

Radius 0 uses the exact fingerprint cache. For radii 1–8, the 64 bits are split
into `radius + 1` disjoint segments. A qualifying pair must share at least one
segment, so the candidate set is complete; candidates are checked in original
representative order. Duplicate fingerprints reuse their already chosen group.
Large shared buckets use the ordinary representative scan; larger radii retain
that scan. Independent reference tests cover uniform hashes, identical values,
many duplicates, shared segments, chains and algorithm versions. Worst-case
runtime remains quadratic, and the segment index adds linear memory.

Scoped metadata export filters records in SQLite before allocating the result
and enforces its complete record limit while loading. Preview conflict detection
uses the transactional catalog revision, including identity aliases and field
versions. Values changed and changed back still invalidate the old plan. No new
query index was added for the measured query workload, which already meets its
budget; query plans remain in generated reports for further investigation.
