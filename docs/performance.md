# Performance and similarity

Measure metadata scale separately from original-image decoding. The reproducible
drivers create their own fixtures under the report directory:

```sh
python -m tests.benchmarks.catalog --output ./data/benchmarks/catalog.json
python -m tests.benchmarks.catalog --distribution shared-segment --output ./data/benchmarks/shared.json
python -m tests.benchmarks.organization --count 50000 --output ./data/benchmarks/organization.json
python -m tests.benchmarks.browse --output ./data/benchmarks/browse.json
python -m tests.benchmarks.browse --without-prefetch --output ./data/benchmarks/browse-no-prefetch.json
python -m tests.benchmarks.pipeline --output ./data/benchmarks/pipeline.json
python -m tests.benchmarks.tui --output ./data/benchmarks/tui.json
```

The catalog driver uses 10,000 generated rows by default, fixed seed `20261013`,
20 tag groups, known rated/unrated subsets and 30 rounds. It checks expected
result IDs using arithmetic membership rules. `new_connection_page_cache` creates
fresh SQLite connections; it does not flush OS or disk caches. The pipeline
driver uses six generated geometric JPEG/PNG/BMP/TIFF/WebP images and checks that
original bytes remain unchanged. Reports and generated fixtures belong in the
ignored data directory.

The browser driver requires the browser extra and Chromium. It creates 50,000
metadata rows sharing a generated 1280×960 preview, loads 5,040 results into the
grid, scrolls through 100 animation frames, and measures eight slideshow switches.
The isolated server delays preview responses by 150 ms. `--without-prefetch`
provides a control using the same image loader and server latency.

## Reference measurements

Measured on macOS arm64, 10 logical CPUs, Python 3.11.15 and SQLite 3.53.1:

| Workload | Observed result |
| --- | --- |
| 10,000 rows, common warm queries, 30 rounds | p95 approximately 1–10 ms |
| Tag filter, 10,000 rows, 30 rounds | median 8.16 ms before resolving names once, 1.26 ms after; identical result IDs |
| Path sort at offset 5,000, same dataset | median 7.59 ms before paging IDs first, 1.52 ms after; identical result IDs |
| Rating / average sort, 20,000 rows, offset 10,000, 120 results | median 38.64 / 39.48 ms before paging IDs first, 8.68 / 10.79 ms after; identical result IDs |
| Sequential queue, 10,000 IDs from 20,000 tagged rows | median 63.69 ms before shared source filtering and ID-only selection, 8.93 ms after; identical order |
| Task submit/status, 30 rounds | p95 below 0.3 ms / 0.05 ms |
| Small 10-image export alongside 10,000 unrelated rows | p95 12.82 ms before SQL scoping, 0.41 ms after |
| Preview metadata revision, same database | p95 71.75 ms before full-table hashing, below 0.01 ms with tracked revision |
| 50,000 uniform synthetic fingerprints, radius 8, three warm runs | median 8.66 s with sparse segment candidates, 1.34 s with bitmap candidates; identical groups |
| 10,000 fingerprints sharing 16 bits, radius 8, three warm runs | median 1.80 s with sparse segment candidates, 0.23 s with bitmap candidates; identical 92 groups |
| Six geometric images, 42 million pixels, 36.2 MB | Initial scan 0.83 s; warm full scan 0.64 s; process peak RSS approximately 385 MB |
| Query during the other library's decode, 30 rounds | p95 1.06 ms |
| Cached thumbnail through ASGI, 30 rounds | p95 2.48 ms; response 1,076 bytes |
| Per-item progress for 200 generated import items | 40,200 row writes before incremental updates, 400 after; identical final counters |

The progress comparison counts SQLite `total_changes` in memory, excluding file
journal updates and initial/final reconciliation.

The 20,000-row sort and queue comparison uses seven warm samples after one warmup.
The catalog driver also checks rating and average pages, sequential queues and
tag-filtered queues against independently computed membership and ordering.

With the same six geometric image workload, three independent process runs
before and after sharing decoded pixels measured median initial scans of
0.762 s and 0.674 s, and warm full scans of 0.635 s and 0.558 s. Median process
peak RSS stayed approximately 380 MB. Both pipelines produced 33,116 bytes of
derivatives. Full rescans decode each image once and perform two content reads
for integrity; new-path and move matching retain their separate hash verification.

TUI playback was measured separately with 100, 500 and 10,000 generated queue
items. The 500-row page previously read 502 image details and issued 3,048
SELECTs per same-page switch, including settled UI events. Reusing the current
summary page reduces this to two current-image detail reads and 56 SELECTs.
Thirty switch samples measured handler p95 of 0.74, 0.83 and 4.45 ms respectively
on macOS arm64, Python 3.11.15 and Textual 8.2.8. The handler budget is 10 ms.
Pages hold at most 500 rows, with authorization checked on each navigation.

These local measurements use warm OS caches and generated data. Geometric images
are highly compressible; fingerprints test grouping correctness, not perceptual
quality. ASGI and TUI handler timings exclude transport and rendering. End-to-end
original-image switching and OS-cold reads need separate measurement. Working budgets are
300 ms p95 for common warm 10,000-row queries and 500 ms p95 for task submission/status.

In Chromium 153 at 1280×800, the 50,000-row browser workload measured initial
5,040-card rendering at 138 ms before windowing and 5 ms after. Mounted cards fell
from 5,040 to 36 initially and 52 while scrolling; scroll-frame p95 was 18 ms.
With the same 150 ms preview delay, median slideshow switching was 175 ms without
prefetch and 19 ms with prefetch. These measurements cover synthetic previews,
not the decode cost or network throughput of a photographic collection.

## Similarity semantics

Exact duplicates group by SHA-256 within current visible libraries. Similarity
groups by the first representative in image-ID order, with every member at or
below the chosen 64-bit Hamming radius. It is not transitive clustering and does
not identify a work, crop or original. Different fingerprint algorithm versions
are never compared. Files and organization are never merged or removed.

Radius 0 uses the exact fingerprint cache. Radii 1–5 split the 64 bits into
`radius + 1` disjoint segments; a qualifying pair shares at least one segment.
Radii 6–8 use sixteen 4-bit segments and bitmaps to count matching segments across
representatives. A pair within radius `r` shares at least `16-r` segments, so this
also retains every possible match. Both paths check the full distance in original
representative order. Duplicate fingerprints reuse their already chosen group.
Bitmap positions are local to each algorithm version. Small collections, large
shared sparse buckets and radii above 8 use the ordinary representative scan.
The sparse path remains faster at small radii; the bitmap path avoids large
candidate unions at radii 6–8.

Independent reference tests cover all indexed radii, exact-distance boundaries,
uniform hashes, identical values, duplicates, shared segments, chains and mixed
algorithm versions. Worst-case runtime remains quadratic; the index adds linear
memory. In the 50,000-row duplicate-heavy workload, both paths take about 57 ms.

## Query execution

Tag and artist filters resolve matching names once, then read their indexed image
associations. Time, path and rating sorts page image IDs before computing display
summaries. Sequential playback requests only IDs; browsing and playback share
source-filter compilation. Counts and rows share a read snapshot. Tag lists load aliases in one
query for the whole page. These changes use existing indexes; query plans are
included in catalog reports.

Album lists page identities before counting visible members or choosing cover
images. Web lists and pickers fetch 101 rows to display 100 and detect another
page. `GET /albums` supports `q`, `limit`, `offset` and `access=edit`; omitting
`limit` preserves the full-list API behavior. Explicit covers remain separate
from `preview_image_id`, which falls back to the first visible member.

Batch previews select only image IDs, check each library once, then read original
values and field revisions in groups of 500. The fixed selection, authorization
and snapshots share a write transaction. Status polling omits journal values;
counts and current library access are still checked. On 50,000 generated images
and 2,500 albums, 30 warm runs measured a 0.63 ms median for a 101-album page and
45.7 ms for a 10,000-image tag preview. Status JSON was 517 bytes, compared with
125,008 bytes including the first 200 journal rows. The organization benchmark
checks membership and undo; these timings exclude transport and rendering.

Job history applies owner and current library-management permissions in SQL
before pagination. Only the requested page loads job details and rechecks access.
Web status polling and task lists omit item journals. For a completed task with
12,000 generated file results, 50 warm local reads measured a median of 0.238 ms
with the first 100 items and 0.027 ms for the status summary. Compact JSON fell
from 12,285 to 414 bytes; journal queries fell from two to zero. These measurements
exclude HTTP transport and rendering. Expanding details still reads a paginated
journal with current authorization checks.

Metadata export scopes records in SQLite and enforces the record limit while
loading. Preview conflict detection uses a transactional catalog revision that
includes identity aliases and field versions. Changing a value and changing it
back still invalidates the old preview.
