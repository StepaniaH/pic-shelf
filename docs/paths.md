# Library paths and naming

Changing the data directory does not change a library's original-file root. Use
root rebinding after independently copying originals to a new disk, mount or
container path. Physical copying and rebinding are separate operations.

## Rebind a verified copy

```sh
picshelf paths preview collection /path/to/copied-images
picshelf paths show PLAN_ID
picshelf paths execute PLAN_ID --yes
picshelf check --full
```

Preview matches exact relative paths, sizes and SHA-256 values. It lists missing
files, changed contents, extra files and equal-content candidates for missing
paths. Candidates are informational: no hash-only or ambiguous fallback assigns
identities. Symlinks, unreadable files and overlapping registered roots block
rebinding. The old mount may be offline; it does not need to be copied again.

The saved plan fixes the target's physical identity and complete inventory.
Changing either files or metadata requires a new preview. Plans expire after
24 hours. Execution only updates the library root and its physical identity in
one database transaction; image IDs and organization stay attached. It does not
copy, rename or delete files, scan extra files or change grants. Repeating a
successful plan returns that result. Target paths are server paths; HTTP root
rebinding requires an administrator.

## Name new managed imports

```sh
picshelf import /path/to/incoming collection --mode copy --json \
  --template '{artists}/{tags}/{date}/{stem}{ext}' \
  --template-fields '{"artists":["Jane Doe"],"tags":["landscape","night"],"date":"2026-10-13"}'
picshelf import-execute PLAN_ID --yes
```

Inspect every destination in the returned plan. Template fields are explicit
naming inputs for this import; they do not add catalog tags or artist links.
Without a template, existing directory-preserving import behavior remains.

| Field | Rule |
| --- | --- |
| `artists`, `tags` | Arrays sorted by normalized name, joined with ` + `; empty arrays use `unknown-artist`/`untagged` |
| `source` | Explicit label; missing value uses `unknown-source` |
| `date` | ISO calendar date; missing value uses `undated` |
| `stem`, `ext` | Original filename stem and extension; the final filename always retains the extension |

Values are normalized to NFC and sanitized inside each path component. A slash
inside a tag, artist or source becomes `_`; it cannot introduce a directory.
Reserved filenames, trailing dots and excessive lengths follow the existing
portable filename rules. Case/Unicode collisions receive numbered suffixes.
Templates allow at most eight components and 1,600 UTF-8 bytes after rendering;
absolute paths, `..`, Python attribute access, conversions and format specs are
refused. Execution uses saved paths and never recomputes them from later metadata.

## Reorganize an existing managed library

Back up originals and metadata first. Reorganization is a file move, separate from
ordinary metadata undo. Indexed and unspecified-purpose libraries refuse it.

```sh
picshelf paths reorganize collection --template '{artists}/{tags}/{stem}{ext}'
picshelf import-status PLAN_ID
picshelf import-execute PLAN_ID --yes --background
picshelf job list
```

The preview reads each image's current artist/tag associations and its first
source record. Source note or URL supplies the label; collection time supplies
the UTC date. No source means the documented fallback. All paths, including
collisions, are fixed in the saved plan. Existing targets are never overwritten,
and equal bytes keep their separate image IDs and organization. Metadata changes
before the first execution require another preview.

The existing background task and per-file journal provide progress, cancellation
and retry. Cancellation takes effect between files; successful moves remain.
If a process exits after moving a file but before publishing its index path,
scanning reports unfinished output and avoids assigning a new identity. Retry
the original plan with `import-execute PLAN_ID --yes --retry` or retry its job.
File conflicts retain both known content and later edits; resolve them before
retrying. Reorganization does not add import-source metadata or change original
collection timestamps. Folder albums update on scan; manual albums remain intact.

HTTP equivalents are `POST /api/v1/paths/relocate`,
`GET /api/v1/paths/relocate/{id}`, and explicit confirmed execution at
`POST /api/v1/paths/relocate/{id}/execute`.
`POST /api/v1/paths/reorganize` returns a saved import plan; use the import/job
endpoints to confirm, monitor, cancel and retry it. Import preview accepts
optional `template` and `template_fields` with the same semantics as the CLI.
