# Usage

Pic Shelf keeps image files in registered libraries and stores metadata in a separate data directory. Indexing an existing directory does not move or rename its files. Managed import copies, hardlinks or moves files into a registered library.

## Commands

Use `picshelf --help` and `picshelf <command> --help` for the complete options. In a source checkout, the executable is `.venv/bin/picshelf`. Global options, including `--data` and `--lang`, precede the command.

```sh
picshelf --data ./data init --admin admin
picshelf library add photos /path/to/images
picshelf scan photos
picshelf ls --library photos --sort path --limit 50
picshelf search -q 'ext:png rating:>=8' --json
picshelf ls --sort path --limit 50 --offset 50 --json
picshelf show 1 --json
picshelf serve
picshelf tui
```

`search` is an alias for `ls` and accepts the same options. Use `--json` for structured search results; human-readable output is not a stable interchange format. Results include `total`, `items`, `limit`, `offset` and `errors`. IDs and offsets are integers; offsets start at zero. Path ordering uses image ID as a tie breaker across libraries. Random sorting generates a new order per request and is intended for discovery; use a playback session when a stable shuffled queue is needed.

The CLI and TUI use local database access. They are administrative tools, not a boundary between untrusted operating-system users. `ls --user <name>` and `play --user <name>` select that user's ratings, favorites and visible libraries.

`picshelf rate <image-id> <0-10> --json` returns the rating summary as JSON.
Its service errors return JSON with `detail` and an `error` object containing
`code`, `retryable`, `next_step` and `objects`, and exit with status 1. For example,
an out-of-range rating has code `invalid_rating`; a missing image has code
`image_not_found`. The ordinary text output remains available without `--json`.
Local administrative rating operations can select the rating owner with `--user`.

## Search

Whitespace combines conditions with AND. Commas within a condition mean OR. Prefix a condition with `-` to exclude it. Quote values containing spaces.

| Expression | Matches |
| --- | --- |
| `sunset` | Exact tag name or filename substring |
| `tag:sunset` | Exact tag name |
| `色彩:蓝色` | Namespaced tag or filename substring |
| `-tag:draft` | Images without that tag |
| `artist:"Jane Doe"` | Artist name or alias substring |
| `album:"Summer trip"` | Album name substring |
| `album:12` | Album ID 12 |
| `album:none` | Images without any album association |
| `rating:>=8` | Your rating at least 8 |
| `avg:>=8` | Average rating at least 8 |
| `fav:` / `fav:false` | Your favorites / non-favorites |
| `ext:png,jpg` | Either file extension |
| `file:portrait` | Filename substring only |

Ratings range from 0 to 10. An unrated image is displayed separately from a zero rating, but numeric search treats an unrated image as zero. Invalid numeric filters and unmatched quotes return an error message and do not silently select every image. `%` and `_` are literal characters in substring searches.

## Import and scanning

```sh
picshelf library add collection /path/to/managed/library --mkdir
picshelf import /path/to/incoming collection            # dry run
picshelf import /path/to/incoming collection --apply    # review and confirm
picshelf import /path/to/incoming collection --mode copy --apply
picshelf import /path/to/incoming collection --mode copy --json
picshelf import-status <plan-id> --offset 0 --limit 300
picshelf import-execute <plan-id> --yes --json
picshelf import-execute <plan-id> --retry --yes --json
picshelf import-cancel <plan-id>
```

The default is `hardlink`. Hardlinks share file contents with their source: editing either link edits the same file. Use `copy` for an independent copy. Cross-device hardlinks fall back to copying. `move` removes the source only after creating the destination. `--yes` explicitly accepts the import confirmation for automation.

Import deduplicates by SHA-256, reserves destination names within each plan and never overwrites a file that appears after planning. A source changed after the dry run requires a new plan. Sources and destinations must not contain one another; links escaping the source or library directory are rejected.

Previews save a complete plan in the data directory without changing source or
destination files. Use the returned ID with `import-status` and `import-execute`
to execute exactly that preview. `import --apply` remains a convenience command
that creates and executes a fresh plan in the same invocation. Creating a preview
and later calling `import --apply` creates a new plan; use `import-execute` to bind
the two steps. JSON output includes pagination and per-item states.

A preview expires after 24 hours. A started operation can be resumed after expiry
only with explicit `--retry`; it still checks the original source fingerprints,
destination identity and current access. Successful items are never repeated.
Cancellation preserves completed work and takes effect between files. Retry
continues unfinished items; source changes, occupied destinations and unrecognized
partial files are conflicts that require inspection or a new preview. Pic Shelf
does not delete these files automatically. Owned partial copies are excluded from
scanning until the import completes. Inspect the plan before resolving a conflict;
keep the source or another verified copy of its content.

Names are normalized to Unicode NFC; reserved names and overlong components are
made portable, and case-insensitive collisions receive a suffix. Directory name
normalization is also shown in the preview. Move rejects symbolic-link sources;
use copy or select the ordinary source file directly. A same-device move preserves
file identity using a hardlink before removing the source. Unsupported hardlinks
fall back to a verified copy. Hardlinks remain shared after import.

Concurrent scans and imports of the same physical root return `library_busy` for
the competing operation; wait and retry. Other libraries remain available. Locks
coordinate processes using the same Pic Shelf data directory. File locking is
currently implemented for POSIX platforms. Do not run separate installations as
uncoordinated writers of the same directory.

Import results distinguish completed files from scan errors. An undecodable file
can have a catalog record without derivatives; it is reported as an error, not as
missing. The plan is `partial` when file operations or the final scan have errors.
CLI execution returns a nonzero status for partial, interrupted or cancelled work.

Scanning preserves image IDs, ratings, tags and notes when an unchanged file moves to a different path. Missing images are marked as missing, not erased. Read or decode errors are reported without treating an existing image as deleted. Folder albums follow current directory membership; manual albums remain under user control.

Incremental scans reuse files with unchanged size and modification time when both derivative images exist. Use `scan <library> --full` when file contents may have changed without changing these attributes, or to rebuild all hashes and thumbnails. Animated formats use the first frame for hashing and derivatives; the original file remains available.

`dupes` groups byte-identical files. `dupes --mode similar --threshold 8` uses a 64-bit perceptual hash: each member is within the threshold of the first image in its group. This is a visual suggestion, not proof of identical content. Groups are not transitive and may omit similarities between members of different groups. No files are automatically deleted.

## Viewing

Web navigation remains available on phone-sized screens. Image cards show the complete composition. Use Enter or Space to open a focused image; Escape closes the topmost dialog and restores focus. In the viewer, left/right arrows navigate, `f` toggles favorites, `i` toggles details and `z` changes sizing. Original sizing loads the original image. Ratings include zero; Clear removes your rating.

The TUI shows the current range and total, with `[` / `]` to change pages and `b` to toggle navigation. Narrow terminals initially hide the navigation pane. Other shortcuts: `/` search, `j` / `k` move, `r` rate, `t` add tags, `f` favorite, `c` comment, `n` note, `p` preview, `g` refresh and `q` quit. `s` starts a slideshow; Space pauses and resumes it. Optional terminal previews use `chafa` or `img2txt` when installed.

Playback queues are limited to 10,000 images. Both `shuffle` and `random` build a shuffled queue without replacement; looping repeats that queue. Reshuffling creates a new one. The Web slideshow pauses its timer while the tab is hidden.

## HTTP API

Interactive documentation is available at `/api/docs`; the machine-readable schema is `/api/openapi.json`. The API prefix is `/api/v1`. Sign in with `POST /auth/login`; browsers use the HttpOnly session cookie and scripts can use the returned token as a Bearer token. Never put tokens in URLs.

| Operation | Endpoint |
| --- | --- |
| Health and version | `GET /health` |
| Visible catalog statistics | `GET /stats` |
| Libraries and albums | `GET /libraries`, `GET /albums` |
| Search and pagination | `GET /images?q=...&sort=path&limit=50&offset=0` |
| Image metadata | `GET /images/{id}` |
| Authenticated media | `GET /media/{thumb,preview,original}/{id}` |
| Tags and artists | `GET /tags`, `GET /artists` |
| Import preview / execution | `POST /import/plan`, `POST /import/execute` |
| Saved import plan / pages | `GET /import/plans/{plan-id}?offset=0&limit=300` |
| Execute or retry the saved plan | `POST /import/plans/{plan-id}/execute` with `confirm: true`, optionally `retry: true` |
| Cancel at the next file boundary | `POST /import/plans/{plan-id}/cancel` |
| Playback queue | `POST /play/sessions` |

Image counts and associated tags/artists follow the caller's library grants and exclude missing images. Administrators can also browse unassigned tags and artists. The global user count is only exposed to administrators. Authentication is required for metadata and all media; health is public. Import execution requires management access and explicit confirmation.

`POST /import/plan` includes a persistent `plan_id`. The saved execution endpoint
accepts only confirmation and retry options; it does not accept replacement source,
library or naming options. The Web importer uses this endpoint and invalidates its
preview when options change. The legacy `/import/execute` endpoint explicitly uses
`execution_semantics: "immediate_plan"`: it creates a new plan from its input.
Plan access is limited to its creator and administrators, and current library
management access is checked again before execution and between items.

Shared service errors preserve the existing `detail` message and HTTP status, and
add `error.code`, `error.retryable`, `error.next_step` and `error.objects`. Codes
such as `permission_denied`, `authentication_required`, `image_not_found` and
`invalid_rating` do not change with the response language. Permission checks
resolve the current account and grants at each service operation. Older endpoints
may still return only `detail`; framework input validation retains its existing
response shape. Search responses also include `error_codes` alongside the existing
localized `errors`; malformed expressions use `invalid_query` and return no items.

## Backups

```sh
picshelf export ./data/exports/catalog.zip --with-thumbs
picshelf export ./data/exports/catalog.fernet --encrypt
picshelf import-backup ./data/exports/catalog.zip --force
```

Exports contain a consistent database snapshot and optionally derivatives. They do not contain original images or the session-signing key. Back up the library directories separately. Stop active writers before restoring. `--force` first saves a consistent copy of the current database, including committed WAL contents. Restore loads the database; scan each library with `--full` to rebuild derivatives. A plain ZIP contains metadata and password hashes, so store it with the same care as the live data directory.

## Database upgrades

Stop running Pic Shelf services and commands before upgrading. Opening an older
database applies the pending schema migrations in one transaction. Before changing
an existing database, Pic Shelf creates an owner-only snapshot in
`<data-directory>/migration-backups/`. It includes committed WAL data. If a
migration fails, the previous schema and metadata remain intact. A second open of
an up-to-date database does not create another snapshot.

An older program refuses a newer schema with `unsupported_schema`. Use a compatible
program or restore the pre-upgrade snapshot; do not edit the version number by
hand. Restoring an incompatible backup also fails before replacing the current
catalog. Snapshot files include account data and need the same protection as the
live database. They do not include original images or signing keys.

To rehearse recovery, copy a snapshot into a new, empty data directory as
`picshelf.db`, then open that directory with the compatible program and verify it.
For an in-place rollback, stop all writers and use SQLite's backup API to restore
the snapshot into the target database; do not replace a live database file while
its WAL is active. See the [SQLite backup documentation](https://www.sqlite.org/backup.html).
