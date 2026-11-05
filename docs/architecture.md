# Pic Shelf architecture

**English** | [简体中文](zh/architecture.md)

Pic Shelf is a self-hosted image library with single-machine storage. A Python service layer provides
shared data operations for the Web, CLI and TUI interfaces; originals stay in registered library
directories, while SQLite and derivative images live in the data directory.

## Modules and boundaries

| Module | Responsibility |
| --- | --- |
| `config.py` | Data directory and runtime paths; explicit arguments take precedence over environment variables |
| `db.py` | Per-caller SQLite connections, transaction configuration and versioned migrations |
| `auth.py` | Password verification, signed tokens, group capabilities and library grants |
| `access.py` / `errors.py` | Explicit service identity, live authorization checks and identifiable error categories |
| `catalog.py` | Query input, filtering and rating operations shared by all three interfaces |
| `hashing.py` | SHA-256, 64-bit pHash, EXIF orientation normalization and WebP derivatives |
| `importer.py` | Import preview, de-duplication, cross-platform destination naming and low-level file operations |
| `import_plans.py` | Complete preview snapshots, per-item logs, cancellation, retry and interruption recovery |
| `relocations.py` / `reorganize.py` / `templates.py` | Rebinding roots to verified copies, explicit reorganization that preserves image identity, and deterministic naming |
| `coordination.py` | Cross-process write coordination within one data directory, keyed by physical root |
| `jobs.py` / `worker.py` | Persistent jobs, per-item progress, permission rechecks and a bounded local executor |
| `indexer.py` | Incremental scanning, move detection, missing markers and folder-album maintenance |
| `search.py` | Search syntax, parameterized SQL, visible-image scope and pagination |
| `ops.py` | Rating, favorites, tags, artists, comments, notes and album operations |
| `libsvc.py` | Library registration, visible-scope statistics and duplicate detection |
| `play.py` | Materialized playback queues and cursors |
| `api/` | HTTP parameters, authentication, permissions, error responses and static file serving |
| `cli.py` / `tui.py` | Local administrative entry points; they call the service layer without HTTP |
| `web/js/` | Native ES modules; routing, views, viewer, slideshow and shared focus management |

HTTP reads and validates login credentials; shared services receive an explicit `AccessContext` and
re-read the account and library grants on every operation rather than caching a visible scope.
`catalog.ImageQuery` unifies query filtering across HTTP, CLI and TUI. The rating service checks the
object, the permission and the input together; the lower-level `ops.py` and `search.py` keep compatible
storage interfaces, so callers that use them directly must still check authorization. Existing HTTP
permission helpers now delegate to `access.py`.

The CLI and TUI are treated as local administrative tools that already have access to the data
directory. When a query selects a user's perspective it honours that user's visible scope; local rating
uses an administrative context explicitly and attributes the rating to the selected user. HTTP cannot
enable this local administrative mode from request parameters. File import and scanning keep their own
file invariants; playback owns queue snapshots; `jobs.py` and `worker.py` own persistent jobs and the
local executor. The web frontend has no build step and loads no remote fonts, scripts or analytics.

Native `select` elements and form events are preserved for dropdowns, with shared CSS unifying the
trigger and the option list. Browsers supporting `appearance: base-select` show the application-styled
list; others use the system picker. An expanded list sits in the browser's top layer, so dialog
scrolling never clips it; Escape closes the list first, then the parent dialog.

`source.js` manages image pagination, cancellation and de-duplication by ID. Page offsets advance by the
number of rows the server returned, and resetting or leaving the page cancels old requests and pending
page fetches. Search, filtering and sorting share per-page request management, so a new request on the
same page cancels the previous one; if candidate results fail to load, the current list stays. Refreshing
an already-loaded range larger than one page reads it in segments and replaces the list only after all
segments succeed. Every route creates its own content node, and admin tabs are routed the same way.
Deep links to an image check permission for that image directly instead of paging through to find it.

Ordinary refreshes share an in-flight read; a metadata edit or undo invalidates earlier reads and
re-requests, so pre-edit results are never presented as the post-save state.

`image-grid.js` mounts only the visible region and nearby rows once more than 240 cards exist, keeping
at most 240 recent cards for scroll-back reuse. Selection is held as a set of image IDs; keyboard focus
and scroll position are independent of which cards are mounted, and the browsing position is
recalculated when the column count changes. Approaching the bottom loads the next page and waits to
retry after a failure. Leaving the page cancels requests and releases observers and caches.

Image summaries join the library name in paged queries, so mixed-library grids show the library below
the filename while card height stays fixed. Bulk status reads carry a request generation and
cancellation signal; cancel, resume or undo invalidates earlier reads. Batch history filters by creator
and current library permission in SQL, then pages in a stable order, and detail reads recheck permission.

The viewer and slideshow share the decode and preload caches in `viewer-load.js`, keeping at most three
previews per open view and reusing in-flight requests. Originals are never preloaded, and closing the
view releases the cache. Playback responses include the media information for the next item in the
current queue, but a cross-round resample does not predict the next image. Each transition still reads
the currently authorized metadata before publishing the matching image and captions.

`theme.js` is a local script that runs before the stylesheets, reading `picshelf.appearance` before the
first paint and resolving colour mode and palette into shared CSS variables. Each of the nine palettes
has a light and a dark variant; the presets use the corresponding Navidrome Stat values. System changes
and storage events from same-origin tabs update the whole interface, and the session's choice survives
when storage is disabled. `appearance.js` offers native radio cards in the account menu; a choice takes
effect immediately and changes neither the account nor the server configuration. The image canvas stays
neutral and dark, while toolbars, details, forms and menus follow the current theme.

`dialog.js` manages nested dialogs and focus. When a session expires, dialogs close from the top down,
the viewer and playback layer release images, drafts and timers, and the sign-in page appears. After
cancelling playback settings, a late session-creation response never opens the playback layer.

`libraries.js` handles library registration and settings, while `importer.js` handles source selection,
preview, execution and result navigation. A library manager can import from the library page, and the
destination list is queried under current administrative permission; source-directory authorization is
still verified per request on the server. Search filters load candidate results first and replace the
grid on success; saving a query uses its own dialog, with the raw expression and display settings
expanded on demand.

The import form is reused for adding a library, creating an album, and adding to an existing collection
in an in-place dialog. When creating a collection, the destination identity is resolved at preview time;
the same dialog keeps source selection, preview and execution state, and normal use does not change the
route. Content and keyboard order match: name and destination, source selection, selected thumbnails, a
single advanced option, and the submit button. The select button only chooses images, while the primary
button switches between create and preview as the selection changes; thumbnail object URLs are released
when the selection is replaced and when the dialog closes. Closing the dialog aborts unfinished source
requests and releases staged uploads that never executed; already-queued jobs continue. After a completed
close, the original list refreshes while keeping filters, sorting and the page, and focus returns to the
triggering button. Old deep links still open the dialog.

Album import stores `album_id` in an immutable preview and rechecks album edit permission and its library
when creating, reading, executing per file and publishing results. Only manual albums in the same library
are allowed. After the import scan, valid images in the current library are matched by the content hash
recorded in the preview, so duplicates that already exist can still join the album, while failed or
undecodable images are left out. Association happens inside a background job, so closing the browser
does not lose the target album.

Web import prefers native file and folder selection plus drag and drop. `import-files.js` preserves
relative directories and reads every batch of a dropped directory in a loop. Browser sources stream
through `uploads.py`, one file at a time, into a private staging area inside the data directory and then
reuse the fixed snapshot and background import; server-side absolute paths are never guessed from browser
filenames. Server sources browse through `source_access.browse`, and non-administrators may only enter
approved source roots whose physical identity still matches. Administrators can browse before creating a
library; a new album checks permission against the currently selected library, and browsing or cancelling
creates nothing. The web app hardlinks or copies by default; move requires an explicit choice under
advanced options.

Upload records bind the creator and destination library, and writes, sealing and cleanup use a
cross-process lock. An import start is committed inside the same upload lock, and cleanup or discard
rechecks the state under the lock, so a staged source that has already started importing is never
removed. Each request rechecks current administrative permission, and relative paths forbid traversal
and symbolic-link escapes. Receiving limits are 256 MiB per file, 10,000 files or 2 GiB per request, and
at most four unfinished uploads per account. Upload bodies bypass the 1 MiB buffering limit for ordinary
JSON, and the receiver validates the declared length against the actual traffic; origin checks and login
authentication still apply. After a complete import the staged copy is released and the result kept;
staged uploads that never started expire after 24 hours and are cleaned by a later upload, while partial
results from started imports are kept for retry.

Display names keep their original text, and `names.py` unifies NFC, whitespace and case matching; old
names are never merged automatically. `metadata.py` manages sources, per-user organization state and tag
evidence, and `entity_edits.py` verifies the preview against current associations inside one transaction
before transferring explicitly selected relationships. Source entities are retained, and the author and
timestamp of associations already on the destination stay unchanged; details and sources follow current
library permission.

## Data flow

When the Web app creates a library that receives imports, the name is required and the server-side
location may be left empty. With no location, `libsvc.create_managed_library` allocates a separate
directory under `libraries/` in the data directory, still running overlap checks and normal
authorization; indexed libraries that browse an existing directory still require an explicit path.

```text
external directory → import preview → confirm → hardlink / copy / move → library directory
library directory → scan → SHA-256 / pHash / derivative → image index + folder albums
search conditions + user's visible libraries → parameterized SQL → paged results
search conditions + user's visible libraries → materialized playback queue → Web / TUI / mpv
```

Scanning handles new paths in two phases: it records the images already seen, then matches the SHA-256 of
each new path against records not seen this round. Only when exactly one old record and one new path match
is the path updated, preserving the ID and every manual relationship. Multiple identical contents that
cannot be matched uniquely are reported as `ambiguous_moves`: old records and relationships are kept, and
the new path is neither assigned arbitrarily nor added automatically. Scanning continues normally once the
original path returns. Records whose original path has genuinely disappeared are marked missing.

A library stores its physical directory identity after the first successful verification, and later scans
check it before file boundaries and missing-day decision. If the directory is replaced or the mount is
offline, scanning stops rather than treating a new empty directory as the original library. An older
library without a recorded identity, whose first verification finds no known content, is also not marked
missing in bulk. Read and decode errors are recorded separately in `scan_error_code` and
`scan_error_detail`, manual relationships are preserved, and the error clears after a later successful
read. A failed subtree keeps its old index.

Folder albums are maintained by the scanner, including clearing albums whose images have all moved away
and repairing covers. The scanner never changes manual albums. Incremental decisions use file size,
modification time and derivative presence; content rewritten in place with unchanged attributes requires
a full scan.

An import plan persists every item, the options, the creator, the expiry time and the identities of the
source and destination roots. The Web app executes by saved plan ID; the CLI can execute the same
snapshot with `import-execute`. Source files added after the preview do not enter an old plan. Filenames
are normalized for Unicode NFC, case collisions, reserved names and component length, and every name
change is shown in the preview.

File operations use exclusive creation and a per-item log to record destination identity, verifying
source hash and file attributes. A failed copy resumes only when the identity still matches and the
content is a prefix of the expected source; files modified later, or whose ownership cannot be
confirmed, are kept and reported as conflicts rather than cleaned up automatically. The destination
write and the log commit precede removing the source in move mode. Successful items are never executed
twice, and cancellation takes effect between files. Created files that never completed are not treated
as successful images by a scan. A decode error keeps the file record and the original bytes and makes
the import report partial failure.

A library's purpose persists as `indexed` or `managed`; historical libraries keep NULL, and the purpose is
neither guessed nor files moved. The default import mode is stored separately. New registrations reject
duplicates, parent/child overlap and cycles, and an indexed library only requires its original directory
to be readable. Library setting changes and file jobs share a write lock, and the purpose is rechecked
before an unfinished import executes. Installed packages require an explicit data directory; only a
recognizable source checkout keeps the repository's `data/` default.

Scanning and importing within one data directory coordinate through file locks; different library aliases
of the same physical root share a lock, while other libraries continue. Exiting the process releases the
lock, and executing a plan again resumes unfinished items; a plan that expires before it starts must be
redone, while an expired operation that already started can be retried explicitly with current files and
permissions rechecked. A crash window remains between the index and the filesystem, and an unprovable
destination ownership is reported as a conflict. Hardlinks continue to share source content; an
independent copy requires copy mode. A whole directory is never one atomic transaction.

Root rebinding and reorganization previews take a directory snapshot first, then verify file contents
outside the database write transaction; permission, directory identity and revision are rechecked before
saving a plan or publishing a directory change. Other metadata writes may proceed during verification,
and a stale snapshot rejects the old plan in favour of a new preview. General transactions and nested
savepoints are managed by `db.transaction`.

Derivatives are sharded by image ID, and their filenames include the algorithm version and a digest of
the source file, with longest edges of 400 and 1600 pixels. A temporary file in the same directory is
written and flushed, then replaced atomically; index references update only after both outputs and the
source-consistency check succeed. A failed new-content pass never leaves the old index pointing at mixed
output. Previously unreferenced derivatives are kept for a while and can be rebuilt as a runtime cache
after diagnostics.

Scanning calls `hashing.encode_image` to decode, normalize orientation and normalize colour once,
producing the pHash and both derivatives from the same pixels. The entry point returns the content
digest, dimensions, output paths and algorithm version, checking file identity and content before and
after processing. Both outputs are staged first and published only after the source verifies. The older
`phash_file` and `make_derivatives` function interfaces remain compatible.

Encoding version 3 keeps EXIF orientation and sRGB colour handling and fixes alpha loss for greyscale and
RGB PNG transparency (tRNS); the pHash is computed as a 64-bit result from a 32×32 image composited on
white. Both derivatives and pHash versions were raised so old transparency results are not reused;
animated and multipage images still use the first frame or page. The algorithm version is recorded
separately, and similarity comparison never mixes versions. Old records keep their version and valid
output, and installing, starting or an incremental scan of unchanged files never rewrites them
automatically; version 0–2 records are updated only through an explicit `rebuild --outdated-only`, which
preserves image IDs and manual associations. A full scan can still verify files that differ in content
but share size and timestamp.

## Background jobs

Web scanning and importing use a new background entry point that returns a job ID; the older synchronous
HTTP path still returns its original result. The CLI waits by default, and `--background` only queues,
leaving consumption to the web service or `picshelf worker`. A job stores the operator, the initial
library directory identity, parameters, request identifier, work set, status and per-item results.
Creators and administrators can view jobs, but current administrative permission is rechecked on every
read and at every file boundary.

When a scan submission on the library page has an unknown outcome, the page reuses the request
identifier; once the job ID arrives it is written into the page address, so a refresh continues reading
the same job. The button is restored on completion or failure, and the library refreshes on success. A
new scan reuses the progress area while historical jobs remain reachable from the job list.

The Web lifecycle starts at most two local subprocesses; all executors for one data directory share two
execution slots and a physical library file lock. Each executor uses its own database connection. A job
whose lock is still held stays running; a run record whose lock has been released is marked interrupted on
query or executor check and waits for an explicit retry. Stopping the service requests a safe stop, and
file operations terminated after the timeout are recovered from the import log. One failing job does not
stop the queue.

The first scan execution stores its path set, and a retry selects only unfinished or failed paths. The
index and progress for each file are committed as it completes, and a cancellation never rolls back
index entries already finished; missing markers and folder-album materialization happen only after the
selected range completes. Files added later wait for a new scan. An import continues its original
preview, keeping file-operation errors separate from final scan errors.

Per-file progress updates only the matching log item and adjusts job counters within the same transaction
as the status change; start, finish and resume are the only points that synchronize the import log in
full. Execution reuses the immutable preview snapshot while per-file permission, cancellation state and
source-directory authorization are still checked live. A scoped rebuild records traversal errors only for
selected paths. Job history pages in SQL by creator and current administrative permission, then reads
details for that page, avoiding per-row authorization of all history.

Restoring a database takes an exclusive maintenance lock that is mutually exclusive with scanning,
importing and job-queue writes. That file lock cannot constrain other software that opens SQLite
directly, so Web, TUI and other metadata-writing commands must still stop before a restore. Job records
are restored with the database backup; after a restore, check library paths and jobs before an explicit
retry.

## Database (schema v15)

SQLite uses WAL, foreign keys and a busy timeout. The API creates a connection per request; the
closed-thread check exists so FastAPI request dependencies and synchronous handlers can run on different
worker threads, and does not mean connections may be shared freely.

| Table group | Contents |
| --- | --- |
| `users`, `groups`, `user_groups` | Accounts, groups and capabilities |
| `revoked_tokens`, `login_attempts` | Session-digest revocation and time-limited failed-login counters; the user session version lives on users |
| `import_source_roots` | Source roots non-administrators may import from, their physical identity and move permission |
| `import_uploads` | Browser upload owner, destination library, staging directory, capacity, expiry and preview association |
| `metadata_batches`, `metadata_batch_items`, `metadata_versions` | Fixed selections, chunked transactions, per-item before/after state and storage-layer field versions for conflict detection and undo |
| `saved_queries` | Personal query and smart-album rules, sorting, display preferences and edit version; results are recomputed under current permission |
| `play_rules`, `play_candidates`, `play_requests` | Personal playback configuration, the candidate snapshot taken at start, and atomic navigation retry records; sessions separately store pause, version, round and count bounds |
| `libraries`, `library_grants` | Library directories and group view/edit/manage grants |
| `images` | Relative path, dimensions, file attributes, hashes, derivative paths and missing state |
| `albums`, `album_images` | Folder and manual albums, members and covers |
| `tags`, `image_tags` | Tags and image associations |
| `artists`, `image_artists` | Artist profiles and image associations |
| `ratings`, `favorites` | Per-user ratings and favorites |
| `image_sources`, `image_workflow`, `tag_claims` | Sources, per-user organization state, tag provenance and confirmation or removal records |
| `tag_aliases`, `artist_aliases`, `entity_edit_plans` | Structured aliases and scoped previews of association changes |
| `comments`, `notes` | Comments, text notes and soft deletion; geometry is reserved for future region notes |
| `play_sessions`, `play_items` | Queue conditions, cursor and materialized image order |
| `scans`, `meta` | Scan records and schema version |
| `import_plans`, `import_plan_items` | Immutable import snapshots, owner, execution state and per-item recovery log |
| `jobs`, `job_items` | Local persistent queue, idempotent request identifiers, status, work set and per-item errors |
| `portable_ids`, `portable_import_ids`, `manifest_plans` | Cross-database stable identity, imported identity aliases and scoped metadata import; a global catalog revision prevents preview-then-change-then-change-back |
| `relocation_plans` | Fixed destination root identity and the complete relative file list; reorganization reuses the import per-item log, recording image IDs and directory publication state |
| `works`, `work_images`, `pending_work_images` | Manual works, independent file versions and pending stable references; image metadata is never inherited or merged |

Migrations only append to `MIGRATIONS` and update `SCHEMA_VERSION`; published versions are never
rewritten. Every pending structural change and its version record commit in one `BEGIN IMMEDIATE`
transaction, so a failure or process exit rolls back. Concurrent initialization rechecks the version
after taking the write lock. Migration scripts must not commit or roll back themselves, change PRAGMAs,
attach databases, or call `executescript()`, which would implicitly commit an outer transaction.

Before upgrading an existing database, a consistent SQLite snapshot is written under
`migration-backups/` in the data directory, including committed WAL content and readable only by the
current user. If the snapshot fails, no migration runs. Reopening the current version creates no
snapshot; future versions, corrupted version records and databases from other projects are rejected.
Rolling back requires stopping all writers and restoring the snapshot; there is no general reverse
migration. The old program must stop before upgrading, because a new process's version check cannot
constrain old code that is still running.

Backup and restore use the SQLite backup API, so committed data in the WAL is neither lost nor mixed in.
Application connections hold a shared maintenance lock until closed; restore takes an exclusive lock,
validates archive members, checksums, schema and associations, completes the upgrade in a temporary
database, then saves the current snapshot and restores. Originals, the signing key and configuration are
backed up separately; a restore invalidates sessions and turns pending file jobs into interrupted ones.
The read-only `check` inspects files, derivatives and associations without repairing or deleting.
See [backup and restore](backups.md).

## Permissions and interfaces

Group capabilities are view, rate, comment, note, tag_edit, manage_library and admin. Library grants are
view, edit and manage, where manage includes the others. Global administrators can reach every library;
new libraries grant admins manage and members view by default.

Image queries, media, statistics and related-entity queries all filter by visible libraries. Derivatives
and originals also verify that the path still lies inside its root, preventing an out-of-bounds read
caused by a symbolic link replaced after scanning. The global user count is returned to administrators
only.

Authentication uses a cost-bounded scrypt and HMAC tokens with expiry and a user session version.
Changing a password, enabling or disabling an account and explicit revocation invalidate old sessions;
a single sign-out records the token digest. Cookies are HttpOnly and SameSite=Lax, with Secure enabled
under HTTPS or an explicit HTTPS site configuration; state-changing cookie requests verify origin, while
scripts use Bearer tokens. Failed logins are rate-limited for a short period, and concurrent password
checks and request bodies are bounded. The default bind is loopback, and HTTP itself provides no TLS;
the current model is trusted sharing within a private network.

Changing global names or artist profiles needs the admin capability, and single-image associations need
tag_edit plus an edit library grant. Non-administrator import needs an administrator-approved source
root, and move needs additional approval; preview and per-file execution check directory identity,
symbolic links and current grants. Neither API nor media responses are persistently cached. The full
permission table, upgrade effects and proxy trust configuration are in [security](security.md).

Interface paths and command examples are in [usage](usage.md); deployment and volume configuration are in
[deployment](deployment.md). Interactive API documentation is at `/api/docs`.

## Performance and limits

- A search page holds at most 10,000 images; a normal web page is 120 and a TUI page is 500. Sorting by
  time, path or rating first filters the page's IDs and then reads ratings, tags and other summaries;
  counts and lists use the same read snapshot. Path and rating sorts append an ID tiebreaker so equal
  values cannot make pagination unstable. Sequential playback reads IDs only, sharing the browsing
  source filters.
- Tag and artist searches resolve matching names first, then find images through association indexes.
  Tag lists read aliases in bulk; name lists support `limit` / `offset`, and `%` and `_` in a search are
  matched literally.
- A playback queue holds at most 10,000 images. Shuffle and random are a single non-repeating permutation
  that loops.
- The web viewer locates the current image through the session parameter `start_image_id`; the server
  verifies inside one transaction that the image belongs to the authorized queue and sets the cursor.
  The starting point is not written into play rules and does not change queue order or count limits.
- A TUI playback table holds at most 500 rows per page and reads only names, dimensions, the personal
  rating and favorites through a bounded join. The same page is reused; a different page, account or
  queue, a reshuffle, a metadata revision or an explicit refresh re-queries. The ordinary navigation
  version is not used for invalidation, while account, queue authorization, current details and the
  current file are still rechecked live; a permission failure clears the display and pauses.
- Exact duplicate detection groups by SHA-256. Similar detection keeps the first-representative
  semantics; radii 1–5 use sparse-bit candidates counted by popcount, 6–8 use bit-set counts of matching
  segments, and the full distance is verified in original order. Indexes are isolated by fingerprint
  algorithm version and the worst case stays quadratic. Metadata manifests filter their scope in SQL;
  transaction triggers on every exported table, identity alias and field version update the catalog
  revision, which detects edits made or reverted after a preview. Measurement method and actual limits
  are in [performance](performance.md).
- Background execution runs at most two jobs, and at most one per physical library; a compatible
  synchronous scan occupies an execution slot too, and a nested background scan reuses an existing slot.
  A single file is first checked against a 256 MiB encoded size and a 33,554,432-pixel ceiling (a 512 MiB
  input budget at 16 bytes per pixel). That is a decode admission limit, not a hard OS RSS cap on native
  codecs; actual peaks must be measured per workload. Large files or slow storage extend the per-file
  cancellation wait. The web app polls status with a 0.5 to 3 second backoff and retries after a
  disconnect; lists and polls use `include_items=false` and do not read or count per-item logs, loading
  details by page after expansion. Every read still rechecks permission and process state. Locks cover
  only local processes using the same data directory.
- Large-library performance depends on queries, image sizes and hardware, with no fixed capacity promise
  yet. Full-text indexes, vector indexes and video or archive import should be evaluated per actual need.
- The default data path is anchored to the project in a source checkout; a wheel install must set a
  writable `--data` or `PICSHELF_DATA` explicitly.
- Bilingual support uses Chinese message IDs with an English catalog; the API language is isolated per
  request through a ContextVar. New interactive text must be maintained in both catalogs.
