# Usage

Pic Shelf stores originals in registered library directories and keeps the catalog
and thumbnails in a separate data directory.

## Web browsing

The sidebar groups pages into **Browse** (**Images**, **Favorites**, **Albums**),
**Organize** (**Inbox**, **Tags**, **Artists**) and **Settings** (**Saved queries**,
**Playback rules**, **Batch history** and **Admin**). **Library** has no menu item:
use the **Pic Shelf** logo in the top-left to return to it — it lists your libraries
and recently added images. Use the side navigation on desktop or the menu button on a phone.
Mixed-library grids show library names below filenames so identical filenames
remain distinguishable. Selection starts afresh when you open another collection.

- Press `/` to search by filename or tag. Expand **Filter**, choose a field and
  value, then **Apply filter** to update the results. **Edit search expression**
  accepts the full query syntax. Failed requests keep the current results.
- **Save query** names the current search or saves it as a smart album. Display
  preferences are under **Display settings** in that dialog.
- Choose the sort order and comfortable or compact density above the grid. The
  browser remembers density; saved searches can specify their own preference.
- Scroll near the end to load the next page. If loading fails, use **Retry**;
  failed thumbnails have a separate **Retry thumbnail** button. Arrow keys move
  focus through the grid; Home and End reach the first and last loaded image.
- Click an image, or focus it and press Enter or Space. In the viewer, open
  **More actions → Keyboard shortcuts** for the complete list of available keys
  and their input-field restrictions.
  **Back** in the top-left — or Escape — returns to the grid.
- Viewing the image is the primary action in the viewer. Its information-panel
  actions use unfilled text controls; the Escape shortcut stays in **Back**'s
  tooltip rather than its visible label.
- The details panel is closed until you choose **Info**; **Hide info** (**Hide** on
  narrow screens) closes it. It opens with one summary line — when the file entered
  the library (UTC), its pixel size and its file size; times without a timezone show
  — rather than a guess. The file name stays in the viewer's top bar and is not
  repeated in the panel.
- Every row in the panel carries its own label: **My rating** 0–10 (drag the slider
  or use the arrow keys; the current value and **Clear rating** sit beside it, with
  the average across users below when there is one), **Tags**, **Artists**,
  **Albums** and the organization state. Rows with nothing recorded show only their
  add action, such as **Add tags**, instead of an empty value. Comments and notes
  keep their counts, and **File details** holds the path and low-level fields.
  **More actions** contains the transparency grid and the original download.
- Click **Select** to organize several images. Tick the checkbox on an image to select
  it, Shift-click a checkbox to select a range, and scrolling or loading more images
  keeps your selection. Clicking the image itself still opens it in the viewer. Preview
  the operation before applying it. On **Images**, **Favorites**, and **Inbox**, a selection
  bar under the heading shows how many images are selected; **Done selecting** leaves
  selection mode and clears the selection.
- **Slideshow** opens interval and order settings. **More settings** contains
  filters, image size, captions and reusable playback rules.

Escape — or **Back** in the viewer's top-left — closes the current dialog and returns
focus. Unsaved viewer input stays
with its image until you close the viewer; closing asks before discarding drafts.
Failed saves keep your input. Retry a failed read, and check the current image
before repeating a write whose result is uncertain.
Session expiry closes open media and dialogs, clears unsaved input and returns
to sign-in.

After saving changes in image details, closing the viewer refreshes the current
filter and sort while retaining the loaded image range. Batch completion and
undo refresh that range too. If the image leaves the current results, keyboard
focus moves to the next remaining image.

The viewer preloads up to two adjacent previews; slideshows preload the next one.
Switching still checks current access. Original sizing and Download fetch the
original file. Reduced-data browser settings disable preloading. Albums, artists
and tags support name search and pagination.
Account actions are under your name in the top bar; administrative tools are in
**Admin**, under **Settings** — start with library setup.

## Appearance

Open your account menu and choose **Appearance**. Choose **System**, **Dark** or
**Light** independently of the palette. The nine palettes are Built-in, Gruvbox,
Catppuccin, Solarized, Nord, Dracula, Tokyo Night, Macchiato and Mocha; each has
light and dark colors. Palette previews follow the current color mode.

Changes apply immediately and stay in this browser across reloads and sign-ins.
Other open tabs in the same browser update too. System mode follows changes to
your operating system's appearance. The viewer keeps a neutral dark background
behind images while its toolbar and details panel follow your theme.

## Albums and tags

Open **Albums → New album** to choose a library and name. Add images through
**Select → Add to manual album** in a grid, or **Add to manual album** in image
details. The picker can create an album without leaving your selection.
**Edit album** changes its name, category and description. A cover is chosen from
the album's visible images until you set one explicitly. Click an album name in
image details to browse it, set that image as its cover, or remove the image.
Folder albums follow the scanned directories; their membership is automatic.

On **Tags**, administrators can open **Manage tag** to rename a shared tag or
preview and add an alias. The action appears when you hover over a tag row or
reach it with the keyboard. On a touch screen, open the tag row's actions button, then choose
**Manage tag**; tapping the tag name still shows its images. Names cannot take another tag's name or alias. Aliases
work in search and image association operations. Use [batch organization](batches.md)
to add or remove tags on selected images; undo preserves later edits.

## Add images in the web app

Choose **Add library** on the library home screen or in Admin. The dialog
defaults to receiving imports: enter a name, then choose images or a folder, or
drop them directly into the same dialog. Preview, execution and results stay in
that dialog; closing it returns focus to the original page and refreshes its list.

- **Browse an existing directory → Add and scan** registers the directory,
  scans it in the dialog, then refreshes the current page. Choose this purpose
  and an existing server directory in the dialog's advanced options. Originals
  stay in that directory.
- **Receive imports** creates a destination and imports in the same dialog.
  Only the name is required: leave
  **Storage location** blank to allocate a library below the application data
  directory. An explicit server directory can still be used. Without a file
  selection, **Create** creates an empty library.

To add more files to a managed library, choose **Import images** in its page or
management entry. This opens a dialog over the current page without navigation.
Choose images or a folder from your computer, or drag images and folders into
the drop area. Folder structure is preserved; non-image files are ignored.
Choose the destination library and **Preview import**. Local files are uploaded
to the server for preview; your computer's originals remain untouched.

For files already on the server, choose **Choose a folder on the server** and
browse to the source. The automatic mode tries hard links and falls back to
copying across disks or when hard links are unavailable. **Advanced options**
contains manual server paths, explicit copy/move modes and filename cleanup.
Web imports default to automatic mode, even if the library's CLI/API default is
move. Local uploads always preserve originals.

The preview shows relative filenames, duplicate skips and destination name
changes. Expand **Full paths** to check server directories. **Change selection**
returns to the file picker; changed settings require a new preview. The Add
library and New album dialogs show the name and destination first, then file
selection and selected-image thumbnails. Local files, folders and server folders
are chosen in the same area. One **Advanced options** section follows the selected
content, above the final action. It holds storage and import settings for libraries,
and category, description and import settings for albums. Server copy/move options
appear when importing from a server folder.

**Start import** runs the saved preview. Moving files requires an additional
confirmation inside the dialog. After completion, **Done** closes the dialog and
refreshes the underlying list; partial failures keep the failure details and a
retry button. **Import more** starts another preview in place. Closing an
unexecuted upload preview aborts pending upload requests and discards its staging
files. Already queued imports continue in the background after closing.

The **New album** dialog also accepts optional images when the selected library
is managed and you have manage access. Selected files import into that library
and join the new manual album automatically. Existing editable manual albums
provide the same **Import images** dialog. Duplicate content already in the
library is added to the album without another copy. Ordinary album creation and
editing still work with edit access; importing additionally requires manage
access. File and folder albums continue to follow scanning automatically.

Users with management access to a library can scan and import through its page.
An active scan remains in the page address, so reloading restores its progress.
Retrying a submission after a lost response reuses the same task. Failed or
cancelled scans keep their details and allow another scan.
Server source folders must be approved by an administrator; moving also requires
source permission. Managers can upload their own selected files without a server
source grant. Each upload accepts up to 10,000 images, 2 GiB total and 256 MiB
per image. Up to four unfinished uploads are allowed per account. Replacing an
upload preview discards its staging files. Successful imports release staging
files; abandoned uploads and unstarted previews expire after 24 hours and are
cleaned when another upload starts. Started partial imports retain staging for
retry. Account and group administration remains separate. See
[Security](security.md) for source authorization.

| Task | Guide |
| --- | --- |
| Batch changes and undo | [Batch organization](batches.md) |
| Accounts, permissions and remote access | [Security](security.md) |
| Sources, aliases and organization state | [Metadata](metadata.md) |
| Move a library or rename files | [Paths and naming](paths.md) |
| Link versions of a work | [Works](works.md) |
| Transfer metadata | [Portable metadata](metadata-format.md) |
| Investigate errors and storage use | [Diagnostics](diagnostics.md) |

## Commands

Use `picshelf --help` and `picshelf <command> --help` for the complete options. In a source checkout, the executable is `.venv/bin/picshelf`. Global options, including `--data` and `--lang`, precede the command.

```sh
export PICSHELF_DATA=/path/to/writable/picshelf-data
picshelf init --admin admin
picshelf library add photos /path/to/images --mode indexed
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

## CLI and HTTP contracts

`ls`/`search`, `show`, `rate`, `scan` and `import` support `--json`. The `batch`,
`queries`, `play-rules`, `play-sessions`, `job`, `metadata`, `manifest` and `import-sources`
groups, plus `organize`, `rebuild`, `library configure`, `import-status`,
`import-execute` and `import-cancel`, produce JSON by default. Errors in these
modes are also JSON on stdout; readable error recovery, prompts and progress use stderr. `--help` always
produces help text. Other commands retain their human-readable output.
`play --player list` writes one original-file path per stdout line and its queue
summary to stderr (filenames containing newlines are not an unambiguous record
format; use image IDs from JSON search for arbitrary filenames).

| Exit code | Meaning |
| --- | --- |
| 0 | Command succeeded; background submission was accepted; status was read |
| 1 | Operation failed, was cancelled, or completed only partly, including undo conflicts |
| 2 | Command syntax, option type/range, JSON input or search expression is invalid |
| 130 | Interrupted by Ctrl-C; inspect the saved job or batch before retrying |
| 143 | Worker stopped by SIGTERM |

A successful status command can report a failed job: inspect `state`. A successful
background submission does not mean the work has finished. Synchronous execution
returns nonzero for partial work and retains its structured per-item result.
Business input checks retain exit 1, such as `rate 12 11 --json` with
`invalid_rating`. Machine keys, IDs, enum values and error codes do not depend on
language; `detail` and `next_step` can change language. Do not parse those strings.

Non-interactive commands never prompt for confirmation or a password. Inspect an
import preview before executing with `--yes`; batch execution and undo also
require `--yes`. Use `--password-stdin` for account passwords and
`--passphrase-stdin` for backup passphrases, passing secrets through a protected
input stream, not command-line arguments. Interactive prompts use stderr and
masked password input. Terminal playback requires a TTY; pipelines can use
`--player list`. A refused non-interactive confirmation does not execute the work.

The compatible HTTP entry point remains `/api/v1`. `/api/docs` and
`/api/openapi.json` describe input types and bounds. Numeric IDs are positive
signed 64-bit integers; offsets range from 0 to 9223372036854775807. JSON boolean
fields require `true`/`false`; they do not accept strings or numbers. JSON IDs and
ratings require integers, not booleans, fractional numbers or numeric strings.
Image pages allow `limit=0..10000`; job/batch/query limits are endpoint-specific.
Unsupported sorting and out-of-range HTTP parameters return 422. Search grammar
errors retain the existing 200 response with empty `items`, `errors` and
`error_codes`. Result ordering and live pagination are described below.

An error retains its existing `detail` field (validation errors retain an array)
and includes `error: {code, retryable, next_step, objects}`. Status 401 means sign
in; 403 means insufficient capability; 404 hides absent or inaccessible objects;
409 means changed state; 410 means an expired plan. Object-specific service codes
remain available. Framework errors use `authentication_required`,
`permission_denied`, `not_found`, `state_conflict` or `invalid_arguments`.
Unknown failures use `internal_error`, without raw exception text. Check the
current state before retrying an uncertain write; only playback navigation and
operations explicitly supporting request keys provide retry deduplication.

Rating `0` is a real rating; `null` clears it. Existing image summaries use `-1`
for an unrated `my_rating`, while `avg_rating` uses `null` when no votes exist.
Dates are UTC ISO 8601 strings; search accepts ISO dates and its documented
relative-date forms. Fixed import plans, batches and playback queues preserve
their target IDs; saved searches are evaluated against current data and access.

These stream and interaction conventions follow the
[Command Line Interface Guidelines](https://clig.dev/) while retaining the
existing command and response formats.

## Library purpose and first use

Explicit `--data` takes precedence over `PICSHELF_DATA`. A source checkout keeps
its repository-local `data/` default, independent of the current directory. A
wheel installation requires one of these explicit settings; it never creates a
catalog under site-packages or silently falls back to a different location when
the selected directory is not writable. Use the same data path for Web, CLI and
TUI. `picshelf --data /path/to/writable/data init --admin admin` initializes it.

Register the directory that the Pic Shelf process can access. Web paths are
server paths, not local browser paths or uploads. In Docker, use the path inside
the container after mounting the host directory. Directories must exist for Web
registration; CLI `--mkdir` can create one. Registration rejects unreadable roots,
symlink loops, duplicate physical directories and parent/child overlaps. Internal
directory symlinks are not traversed during scanning.

| Purpose | Original directory | Import behavior |
| --- | --- | --- |
| `indexed` | Read access is sufficient; catalog metadata and derivatives go into the data directory | Import is refused; scan files already in place |
| `managed` | Read and write access required | Preview and confirm hardlink, copy or move into the library |
| Unspecified (legacy) | Existing path and behavior retained | Original import behavior, with hardlink as the initial default |

Existing libraries remain unspecified after upgrade. CLI/API callers that omit
`mode` retain this compatibility behavior. New Web registrations explicitly
choose a purpose. Selecting a purpose does not move, rename or rewrite originals.
Default import mode is a separate preference; explicit `import --mode` overrides
it. A saved preview retains its chosen mode even if the preference later changes.
Changing the library to `indexed` blocks unfinished imports before they write.
Purpose changes and active scans/imports share the same library lock.

```sh
picshelf library add archive /path/to/originals --mode indexed
picshelf library add collection /path/to/collection --mkdir --mode managed --default-import-mode copy
picshelf import /path/to/incoming collection --json
picshelf import-execute <plan-id> --yes --json
picshelf library configure collection --default-import-mode hardlink
picshelf library configure archive --mode indexed
```

An empty catalog offers **Add library**. Choose **Browse an existing directory**
to index files in place, or **Receive imports** for a managed library. In the
import form, choose **Preview import**, review the files, then **Start import**.
Scan actions show background progress under Admin → Background tasks;
expand **Task details** for the task ID and file results. Indexed libraries remain editable for ratings,
tags and other metadata even when the original directory is read-only.

API creation accepts `mode: "indexed" | "managed" | null` and
`default_import_mode: "hardlink" | "copy" | "move"` at `POST /libraries`.
`PATCH /libraries/{id}` can update these settings but does not accept `root_path`.
Import requests may omit `mode` to use the library preference. Path errors include
an actionable `error.code` such as `library_path_invalid`, `library_root_overlap`
or `library_root_unwritable`; importing into an indexed library returns
`library_indexed` with HTTP 409.

## Metadata and organization

See [Metadata and provenance](metadata.md) for Unicode name matching, aliases,
source records, tag evidence, personal inbox/done state and confirmed association
merges. The viewer can mark organization state and add provenance; CLI `metadata`
commands expose the same records and saved previews.

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
| `rated:false` | Explicitly unrated by you |
| `rated:true rating:0` | Explicitly rated zero by you |
| `width:>=1920 height:>=1080` | Decoded dimensions in pixels |
| `orientation:portrait` | Height greater than width (`landscape` and `square` also supported) |
| `imported:>=now-7d` | First cataloged within the last seven days or later |
| `imported:>=2026-10-13T00:00:00+08:00` | First cataloged at or after this instant, with an explicit time zone |
| `workflow:inbox` / `workflow:done` | Your organization state |
| `missing:true` / `missing:any` | Missing records / both present and missing records |
| `avg:>=8` | Average rating at least 8 |
| `fav:` / `fav:false` | Your favorites / non-favorites |
| `ext:png,jpg` | Either file extension |
| `file:portrait` | Filename substring only |

Ratings range from 0 to 10. An unrated image is displayed separately from a zero rating, but numeric search treats an unrated image as zero. Invalid numeric filters and unmatched quotes return an error message and do not silently select every image. `%` and `_` are literal characters in substring searches.

Empty values (except the existing `fav:` shorthand), unknown enum values, invalid
numeric bounds, malformed comma lists and expressions over 4096 characters are
errors. The entire expression fails; a valid branch does not hide an invalid OR
branch. An empty expression intentionally means all visible, present images.
Unknown colon prefixes retain their existing namespaced-tag/filename meaning;
they are never ignored as filters. Use `tag:namespace:name` when a namespace
collides with a reserved field. The condition editor accepts only fields from
the server's grammar and uses the same parser as CLI, TUI and HTTP search.
Its value input supports comma-separated alternatives: `png,jpg` selects either
extension, while `"comma,name",night sky` selects either of those two literal
tag names. Spaces within a value are preserved.

Quote a comma to treat it literally, such as `file:"comma,name.png"`; unquoted
commas remain OR. Queries accept up to 128 conditions and 100 values per
condition. Pixel comparisons use integers from 0 to 1,000,000,000; rating
comparisons use finite numbers from 0 to 10. Dates use comparisons with an
ISO 8601 instant containing a time zone, `now`, `now-Nd` or `now-Nh` (N up to
36500). Relative dates are evaluated once per read. `imported` refers to first
catalog insertion, including directory scans; it does not mean file mtime,
creation date or the time of a later import of the same image. Missing records
are excluded by default and cannot play until their original files return.

Static result sets have stable offset pagination for every non-random sort,
using the image ID to break ties. Each response reads its count and rows from
one database snapshot. Separate pages use the current catalog. The Web grid
deduplicates overlapping pages, but scanning or edits can still shift the results;
refresh to see the current order. Random discovery takes a new sample
on each request and may repeat; the Web view offers refresh instead of appending
random pages. The viewer stops at the end of that sample and displays its size
separately from the total matching count. Playback creates a separate queue.

Collection sorting is kept in the page URL and survives refresh. Applying a search
filter adds a history entry; use the browser's Back and Forward buttons to revisit
earlier filters.

### Saved queries and smart albums

Open **Saved queries** in Web, or search and expand the rule editor. Save a name,
expression, sort and display preferences. Smart albums evaluate the rule on
each read; they do not modify manual or folder albums. New matches appear and
current permissions filter every result. Rules are personal, including for
administrators; the local CLI may explicitly select a user's perspective.
Open or reload a rule to use its latest saved version. Invalid rules remain
editable and report errors instead of appearing as successful empty albums.
Saving checks the editing version to avoid overwriting another tab's changes.

```sh
picshelf queries save 'Unrated portraits' --kind smart --query 'rated:false orientation:portrait' --sort path
picshelf queries list
picshelf queries show 1
picshelf queries run 1 --limit 60 --offset 0
picshelf queries edit 1 --version 1 --changes '{"query":"rated:false"}'
picshelf queries export 1 > query.json
```

These commands emit JSON. `--user NAME` selects the same personal ratings,
favorites and permissions as that user. Export contains the expression (with
its original date offset), sort and preferences, not a frozen image list.
HTTP provides `/api/v1/queries`, `/{id}`, `/{id}/images` and `/{id}/export`;
PATCH updates require `version`. `/api/v1/search/fields` describes condition
fields and POST `/api/v1/search/append` safely appends one condition. Its
`values` accepts an array of literal strings or a string using the
condition editor's comma and quote syntax. Legacy
`GET /images` keeps `errors` and `error_codes`; invalid expressions return no
items. CLI search emits the same JSON error fields and exits nonzero.

## Import and scanning

```sh
picshelf library add collection /path/to/managed/library --mode managed --mkdir
picshelf import /path/to/incoming collection            # dry run
picshelf import /path/to/incoming collection --apply    # review and confirm
picshelf import /path/to/incoming collection --mode copy --apply
picshelf import /path/to/incoming collection --mode copy --json
picshelf import-status <plan-id> --offset 0 --limit 300
picshelf import-execute <plan-id> --yes --json
picshelf import-execute <plan-id> --retry --yes --json
picshelf import-cancel <plan-id>
```

The default is `hardlink`. Hardlinks share file contents with their source: editing either link edits the same file. Use `copy` for an independent copy. Cross-device hardlinks fall back to copying. `move` removes the source only after creating the destination. Only regular files are read: pipes, sockets and device files are reported as errors and skipped, and an open descriptor is rechecked so a file replaced after inspection cannot block the preview. `--yes` explicitly accepts the import confirmation for automation.

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

### Scan safety and derivative versions

A one-to-one content match preserves the image record when a file moves. If
several missing records or new paths share the same bytes, scanning reports an
ambiguous move. It keeps the original IDs and manual metadata, marks the absent
paths missing, and leaves ambiguous new paths unassigned. Restore their original
paths and scan again before reorganizing them individually.

A successful initial scan records the physical root directory identity. Replacing
the directory, changing a root symlink, or losing a mount stops missing-file
classification. An upgraded catalog with no recorded directory identity also
refuses to mark everything missing when its first verification finds no known
content. Check the mount and restore verifiable original files before retrying.
A genuinely empty directory with the same recorded identity can mark files missing.

Read/decode errors use `scan_error_code` and `scan_error_detail`, independently of
`missing`. A successful later scan clears these errors. Original files and manual
metadata remain intact. New corrupt files can have records without derivatives.

```bash
picshelf rebuild collection --outdated-only
picshelf rebuild collection --outdated-only --background
picshelf scan collection --full --json
```

Derivative and fingerprint algorithm versions are stored separately. Legacy
records use version 0; startup does not rebuild them. Version 3 keeps EXIF orientation
and sRGB color conversion, and fixes alpha loss for grayscale/RGB PNG transparent
colors (tRNS). Both version numbers increase because the derived pixels and hashes
of these inputs change. Its 64-bit perceptual hash composites
transparency onto white; comparisons do not mix different algorithm versions.
The explicit outdated-only rebuild updates version 0–2 records while preserving
image IDs, ratings and other manual relationships. Startup and an unchanged-file
incremental scan keep their existing outputs. Deleting a derivative and rescanning
repairs it without changing image IDs.

Both outputs use versioned filenames. The catalog references them only after
encoding and source checks succeed, so a failed preview write preserves the previous
visible derivatives. Unreferenced complete derivatives can remain after failure;
they are cache data, not copies of the original collection.

| Input | Derived output |
| --- | --- |
| JPEG/JFIF, PNG, BMP, WebP | First image; EXIF orientation, aspect ratio and alpha where present |
| GIF / animated WebP | First frame; original animation remains available |
| TIFF | First page; original multipage file remains available |
| AVIF | Available when the installed Pillow build includes its decoder |
| Grayscale / untagged CMYK | Basic RGB conversion; untagged CMYK has no calibrated color guarantee |
| Embedded ICC | Converted to sRGB with LittleCMS; invalid profiles produce an actionable item error |

The browser must support the original format to display it directly; WebP
thumbnails and previews provide a common derived format. Professional color
reference coverage is separate from generated geometry/color tests.

Files above 256 MiB encoded size or 33,554,432 decoded pixels are rejected before
pixel decoding. The pixel bound uses a 512 MiB working-input budget at 16 bytes per
pixel; native codec RSS is not a hard-capped process limit. Background concurrency
remains two tasks. Large files can require an independent reduced copy; Pic Shelf
never resizes or rewrites their originals.

### Background tasks

The Web administrator's scan and import actions submit persistent tasks. Open
**Background tasks** to inspect progress, item results, cancellation and retry.
Leaving the page does not cancel the task. Progress polling backs off from half a
second to three seconds. The original synchronous API routes remain available.

```bash
picshelf scan collection --json                 # wait for completion
picshelf scan collection --background --json    # queue only
picshelf import-execute <plan-id> --yes --background --json
picshelf worker                                # run a local worker until Ctrl+C
picshelf worker --once                         # process at most one available task
picshelf job list
picshelf job status <job-id> --offset 0 --limit 100
picshelf job cancel <job-id>
picshelf job retry <job-id>                     # wait for completion
picshelf job retry <job-id> --background
```

Background CLI submission requires a running Web service or `picshelf worker` to
consume the queue. It does not install a system daemon. At most two tasks execute
per data directory, with at most one writing each physical library root. A busy
library stays queued without preventing another library from proceeding. Queries
and playback remain available while files are processed.

Persistent tasks need an active account to own them. The existing local `scan`
command also works before an administrator is created; in that case it runs
synchronously without a task record.

States are `queued`, `running`, `cancelling`, `cancelled`, `succeeded`, `partial`,
`failed` and `interrupted`. Completion counts describe file or path items; an import
also exposes final scan errors separately. Cancel takes effect between files and
preserves completed work. Scan retries use their original path set, so new files
require a new scan. Import retries use the original preview. A worker exit is
detected by its released task lock; interrupted work requires explicit retry.
Completed tasks return their existing result instead of executing again.

Task results are visible only to their creator or an administrator with current
management access to the library. Access is checked again during execution;
removing access or disabling the account stops subsequent file operations.
Idempotency keys belong to the submitting account and must not be reused for a
different operation. Polling returns current state, not a guarantee of an active
worker; queued CLI tasks need a consumer.

Restore refuses to run while scans, imports or task execution hold the maintenance
lock. Stop the Web service, TUI and other metadata writers before restoring; the
lock does not coordinate unrelated tools that open the SQLite file directly.

`dupes` groups byte-identical files. `dupes --mode similar --threshold 8` uses a 64-bit perceptual hash: each member is within the threshold of the first image in its group. This is a visual suggestion, not proof of identical content. Groups are not transitive and may omit similarities between members of different groups. No files are automatically deleted.

## Viewing

Press F1 for terminal help. The list shows its range, total and selected count.
Use `[` / `]` to page, `b` for navigation, `d` for full details, `/` to search
and Escape to return to the list. Narrow terminals initially hide navigation
and details; long names remain available in the detail pane. `NO_COLOR=1` keeps
terminal color optional.

`x` selects the current image across pages; Ctrl+X clears the selection. `r`
rates (including 0; `-` clears), `t` adds tags (quote names containing spaces),
`f` favorites, `a` links an existing artist, `l` adds to a manual album, and `w`
sets `done` or `inbox`. Selected images use a fixed batch preview with paths and
count. Cancel is focused by default. Batch writes run in the existing worker
queue. With no selection, rating, favorites, tags, comments (`c`) and notes (`n`)
keep their direct single-image behavior.

Ctrl+S queues a scan of the selected library or the current image's library.
Ctrl+J opens jobs and batches, including progress, cancel, retry and conflict-aware
undo. Enter opens paginated item results. Workers use the same two execution
slots as Web and CLI. Exiting stops this TUI's workers at safe boundaries;
interrupted operations can be resumed from their saved state.

`s` starts a fixed playback session; arrows navigate and Space pauses or resumes.
Ctrl+P reopens the latest session paused, including one created in Web or CLI.
Saved queries and playback rules appear in navigation. Search, prompts and help
pause playback; resume explicitly with Space. Missing queue entries pause with a
manual next-image action. `g` refreshes, `q` exits normally (status 0), and Ctrl+C
exits with status 130 after terminal cleanup.

`p` enables optional `chafa` or `img2txt` previews. Rendering runs asynchronously
with a 20-second timeout; moving or closing cancels the old process. Missing or
failed renderers leave text-based organization available. This does not imply
support for every terminal's image protocol.

Playback queues are limited to 10,000 images. Both `shuffle` and `random` keep
their original behavior: a shuffled queue without replacement, repeated on
loop. `shuffle_each_round` shuffles the same selected members at each forward
wrap. `sample` draws with replacement, so an image can appear several times;
looping draws a fresh round from the same candidate snapshot. Previous/goto
navigate within the current round. New images never enter a running snapshot.
The explicit `reshuffle` action re-evaluates the stored query and source and
resets the cursor; start a saved rule again to apply edits to that rule.

Responses include `matched_total`, `total`, `candidate_count`, `queue_limit`,
`truncated` and machine-readable `warnings`. A requested smaller count is an
intentional selection. The 10,000-image queue cap reports `queue_limit` when it
restricts the requested result. For `sample`, the candidate snapshot is also
capped at 10,000: a uniform reservoir considers every match, and each draw is
independent within that fixed reservoir. `candidate_limit` reports this case
even when fewer draws were requested. Narrow the query for a complete desired
candidate set. A `seed` integer (0 through 2^53−1, exact in JavaScript) makes an unchanged candidate
set reproducible; forward rounds use seed + round. Random modes select from
matches independently of the sequential sort option.

The Web **Playback rules** page saves personal query/source, sort, mode, count,
interval, loop, image size and caption settings. A session copies these settings
and the selected IDs; later rule edits have no effect on it. Current image
metadata and file availability are still checked when displayed. Rules never
store media URLs. **New rule** opens a form for saving a named rule and choosing
its library. Export preserves the rule itself.

Starting a slideshow from the image viewer begins at that image in the new queue.
Turn off **Start from this image** to begin at the first queue item. If the current
image is excluded by the filters or queue limit, the form stays open so those
settings can be adjusted. The starting image belongs to the session, not the
saved rule. The local CLI supports
the same rules and session controls:

```sh
picshelf play-rules save 'Evening' --spec '{"query":"fav:","order":"shuffle_each_round","interval":5,"loop":true}'
picshelf play-rules list
picshelf play-rules show 1
picshelf play-rules start 1
picshelf play-sessions show <session-token>
picshelf play-sessions control <session-token> next --request-key first --expected-version 0
picshelf play-sessions control <session-token> pause
picshelf play-sessions control <session-token> goto --position 0
picshelf play-rules edit 1 --version 1 --name 'Evening' --spec '{"query":"fav:","interval":8}'
picshelf play-rules export 1 > playback.json
```

Rule/session commands emit JSON and accept `--user NAME`. Counts range from
0 (all, within the cap) to 1,000,000 requested items; intervals are whole seconds
from 0 (manual) to 86,400. `sort` accepts non-random image sorts; `order` controls
randomness. `size` is `fit`, `fill` or `original`; `captions` is a boolean.
The existing `play --player mpv|terminal|list` entry remains available for its
original three ordering modes and materialized playlist. The TUI uses the same persistent playback sessions and can resume the
latest session paused with Ctrl+P.

Web playback starts its timer after a successful image decode. It stops while
loading, paused or hidden, and restarts with a full interval when visible. Manual
navigation keeps the pause state. A missing/failed image pauses with retry and
next-image controls, so an all-broken queue cannot spin endlessly. Pause and exit
keeps the session. Resume reloads its persisted cursor and pauses first, including
after a browser or connection failure; it never silently creates a new queue.
The last session reference is kept per signed-in user in local browser storage;
the reference alone does not authorize access. Current library permissions are
required for reading and navigation. A revoked queue must be replaced with a
new permitted session. No viewing-history or “recently unseen” query is recorded.

HTTP adds `/api/v1/play/rules` with get/edit/start/export routes and
`POST /api/v1/play/sessions/{token}/pause`. Navigation optionally accepts
`request_key` and `expected_version`. The cursor change and retry result are one
transaction: repeat the same key and payload after a connection failure. A
different payload with that key conflicts; an outdated version with a new key
requires reloading state. Legacy callers may omit both fields. Upgraded old
sessions retain their queue and cursor; unknown original matching counts are
reported as null with `legacy_scope_unknown`, rather than guessed.

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
| Tags and artists | `GET /tags?q=...&limit=100&offset=0`, `GET /artists` with the same parameters |
| Import preview / execution | `POST /import/plan`, `POST /import/execute` |
| Browse authorized server folders | `GET /import/sources?library_id={id}`, optionally `path` |
| Stage browser files | `POST /import/uploads` with `library_id`; `PUT /import/uploads/{id}/file?path={relative-path}` with raw file bytes and matching `Content-Length` |
| Preview / discard an upload | `POST /import/uploads/{id}/plan` with optional `rename`; `POST /import/uploads/{id}/discard` before execution starts |
| Import into a manual album | Optional `album_id` on `POST /import/plan` or `POST /import/uploads/{id}/plan`; target must belong to the destination library and remain editable through execution |
| Saved import plan / pages | `GET /import/plans/{plan-id}?offset=0&limit=300` |
| Execute or retry the saved plan | `POST /import/plans/{plan-id}/execute` with `confirm: true`, optionally `retry: true` |
| Cancel at the next file boundary | `POST /import/plans/{plan-id}/cancel` |
| Queue a scan / derivative rebuild | `POST /libraries/{id}/{scan-jobs,rebuild-jobs}` with `request_key`, optionally `full` for scans or `outdated_only` for rebuilds |
| Queue the saved import | `POST /import/plans/{plan-id}/jobs` with `confirm: true` and `request_key` |
| Task list / paginated item results | `GET /jobs`, `GET /jobs/{id}?offset=0&limit=100` |
| Task status without item journals | `GET /jobs?include_items=false`, `GET /jobs/{id}?include_items=false` |
| Cancel / retry unfinished task items | `POST /jobs/{id}/{cancel,retry}` |
| Playback queue | `POST /play/sessions`, optionally with `start_image_id` to position the initial cursor |

Image counts and associated tags/artists follow the caller's library grants and exclude missing images. Administrators can also browse unassigned tags and artists. The global user count is only exposed to administrators. Authentication is required for metadata and all media; health is public. Import execution requires management access and explicit confirmation.

`POST /import/plan` includes a persistent `plan_id`. The saved execution endpoint
accepts only confirmation and retry options; it does not accept replacement source,
library or naming options. The Web importer uses this endpoint and invalidates its
preview when options change. The legacy `/import/execute` endpoint explicitly uses
`execution_semantics: "immediate_plan"`: it creates a new plan from its input.
Plan access is limited to its creator and administrators, and current library
management access is checked again before execution and between items.

Each Web task has a detail URL that can be reopened after leaving the importer or
refreshing the page. Task details show the library name, progress, retry controls
and a link to the library. Status polling automatically retries a lost connection;
**Retry** checks immediately. File results load only when **Task details** is
expanded. `include_items=false` omits item rows and their pagination fields while
preserving status, result and error fields; access is checked on every request.

## Backups

```sh
picshelf export ./data/exports/catalog.zip --with-thumbs
picshelf export ./data/exports/catalog.fernet --encrypt
picshelf import-backup ./data/exports/catalog.zip --force
```

Exports contain a consistent database snapshot and optionally derivatives. They do not contain originals, the signing key or configuration. Back up library directories separately. Use `backup inspect` before an offline restore; `--force` first saves the current database, including committed WAL. Included derivatives are restored; omitted derivatives can be recreated with `rebuild`. Sessions are invalidated, and restored pending jobs require explicit retry. `check --full` reports integrity and file problems without repair. See [Backup and recovery](backups.md) for limits, legacy compatibility and recovery steps.

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
