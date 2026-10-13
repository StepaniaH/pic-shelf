# Portable metadata

`picshelf manifest export` writes a versioned JSON snapshot of a user's work and
the shared catalog metadata in selected libraries. It contains no original image
bytes, thumbnails, passwords, password hashes, session credentials, group grants,
server roots or machine keys. It is separate from an administrator's database
backup. Comments, notes, artist links and source URLs are user content: review
them before sharing a manifest.

## Export and restore

```bash
picshelf --data ./data manifest export --library collection --user alice > metadata.json
```

Omit `--library` to include all libraries visible to that user, or repeat it.
Each export is a full snapshot of that scope, not an incremental journal. Personal
ratings, favorites, workflow, comments, notes, saved searches and playback rules
belong to the selected user. Shared tag/artist associations, aliases, source
records, tag evidence and albums are included. Shared records authored by another
user retain their content but export their actor reference as `null`; this format
does not transfer other users' identities or personal records. Deleted comments
and notes retain their deletion marker. Albums spanning unselected images are
omitted. A rule referring by ID to an album, library or artist outside the export
scope must be edited or exported with that scope available.

On the destination, initialize accounts, copy originals separately, register the
new library roots and scan them. Read the source stable IDs in `owner` and
`records.libraries`; read destination numeric IDs with `users list` and
`library list`. Then supply explicit mappings:

```bash
picshelf --data ./restored-data manifest preview metadata.json \
  --mappings '{"libraries":{"SOURCE_LIBRARY_ID":2},"users":{"SOURCE_OWNER_ID":3}}'
picshelf --data ./restored-data manifest show PLAN_ID
picshelf --data ./restored-data manifest execute PLAN_ID --yes
```

Replace the uppercase placeholders with IDs from the export or preview. The
commands produce JSON. The preview includes checksum, mappings, matched image
IDs/relative paths, prospective counts and conflicts. A CLI preview with conflicts
exits 1 and saves its report; nothing is imported. The policy is
`abort_on_conflict`. There is no force-overwrite mode. Plans expire after 24 hours.

Matching uses a previously imported stable ID, an explicit `images` mapping, or
the mapped library's exact relative path. In every case, the recorded hash and
size must match both the indexed record and the actual file bytes. No global
hash-only fallback is used: equal bytes in different paths or libraries can have
different ratings and collection context. Missing files, changed roots, escaping
links, changed bytes or ambiguous identity mappings block execution. Restore the
originals, scan or correct the mapping, then create another preview.

Existing entities with the same name are conflicts, not automatic merges.
Administrators can add `tags`, `artists` or `albums` mappings using the same
stable-ID-to-local-ID structure after reviewing those entities. Existing different
field values still block import. Root-folder labels follow the destination library
name when they originally followed the source library name; custom folder labels
and metadata must agree with the destination. Imported image/entity IDs remain
resolvable across re-export, through stored aliases for the destination's local
identities. Deleting a local record also removes its identity aliases.

Imports require current `tag_edit` capability and edit access to every destination
library, plus the capabilities needed for imported personal actions. Only an
administrator can map ownership to another account, map global tags/artists or
create aliases. Existing global albums require administrator access. Accounts,
roles, roots and grants are never created or changed. A mapped user can remain
unable to browse the destination until an administrator grants access separately.

The preview performs and rolls back the same transactional writes that execution
will perform. Execution rechecks permissions, files and the metadata fingerprint,
then commits all changes together. An intervening metadata change anywhere in the
catalog conservatively invalidates the preview, including a value changed and
changed back. Repeating a successful plan or importing the same unchanged
manifest again does not duplicate comments, sources, aliases or associations.
Execution never replaces a different rating, note, comment or entity definition.

## Format 1

[A small complete example](examples/metadata-v1.json) contains one synthetic
image, a zero rating and a comment. Its identities and timestamp are fixed for
readability. To reproduce its separately stored original, create an empty sample
directory and run this Python snippet inside it:

```python
import base64
from pathlib import Path

Path('sample.png').write_bytes(base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'
))
```

Register and scan that directory, then map the example's library and owner IDs
as described above. Use a disposable instance to try it. A rating record of `0`
is intentional; an unrated image would have no rating record.

Top-level fields are `type` (`picshelf.metadata`), `format` (integer `1`),
`exported_at`, `owner`, `records` and `checksum`. Stable identities are 32 lowercase
hexadecimal characters, generated independently of local row numbers and file
hashes. References in records use those identities, not SQLite IDs.

`checksum` is SHA-256 over UTF-8 JSON of all other top-level fields, recursively
sorted by key, with separators `,` and `:`, literal Unicode, and no NaN/infinity.
Array order is preserved. This detects accidental changes; it is not a signature
or proof of authorship. Unknown top-level, record-table and record-field names
are included in checksum validation and reported in `unknown_fields`. They are
not imported. Unsupported format versions are rejected before writes.

All listed record tables are required, including empty arrays. Exported nullable
fields are explicitly `null`; omitted columns are not treated as defaults. Times
are ISO 8601 strings with a timezone. Personal rating `0` is distinct from no
rating record. `confirmed` and `deleted` are integer `0`/`1` markers.

| Records | Fields |
| --- | --- |
| `users` | `id`, `username`, `display_name`; exactly the export owner |
| `libraries` | `id`, `name`, `description` |
| `images` | `id`, `library_id`, `rel_path`, `sha256`, `size_bytes` |
| `tags` | `id`, `name`, `namespace`, `category`, `description`, `created_at` |
| `artists` | `id`, `name`, `aliases`, `links`, `note`, `created_at`; links retain their existing JSON-string representation |
| `albums` | `id`, `library_id`, `name`, `kind`, `rel_dir`, `category`, `cover_image_id`, `description`, `created_at` |
| `tag_aliases`, `artist_aliases` | `id`, `entity_id`, `name`, `match_key`, `created_by`, `created_at` |
| `image_tags` | `image_id`, `tag_id`, `added_by`, `added_at` |
| `image_artists` | `image_id`, `artist_id` |
| `album_images` | `album_id`, `image_id`, `position`, `added_at` |
| `ratings` | `image_id`, `user_id`, `value`, `updated_at` |
| `favorites` | `image_id`, `user_id`, `created_at` |
| `image_workflow` | `image_id`, `user_id`, `state`, `updated_at` |
| `comments` | `id`, `image_id`, `user_id`, `body`, `created_at`, `updated_at`, `deleted` |
| `notes` | Comment fields plus `geometry` |
| `image_sources` | `id`, `image_id`, `origin`, `original_filename`, `collected_at`, `created_time`, `url`, `note`, `created_by`, `created_at` |
| `tag_claims` | `id`, `image_id`, `tag_id`, `origin`, `confirmed`, `source_ref`, `created_by`, `created_at`, `confirmed_by`, `confirmed_at`, `retired_at` |
| `saved_queries` | `id`, `owner_id`, `name`, `kind`, `query`, `sort`, `display`, `created_at`, `updated_at` |
| `play_rules` | `id`, `owner_id`, `name`, `spec`, `created_at`, `updated_at` |

Queries are exported as arrays of `{kind, values, neg}` clauses. A numeric album
reference becomes `{"album":"STABLE_ALBUM_ID"}` in `values`. Other values remain
strings, including literal names. Import reconstructs an escaped expression with
destination album IDs. Playback specifications use the same clause array for
`query` and stable references for `source.library_id`, `source.album_id` and
`source.artist_id`. Interval/order/seed/display preferences retain their existing
meaning. Active playback queues and navigation sessions are not exported.

The CLI accepts at most 20 MiB and 50,000 records per manifest. HTTP requests also
retain the server's 1 MiB request-body limit. Large catalogs can export separate
libraries; rules must remain within each chosen scope. Automatic sidecar writing
and incremental synchronization are not part of this format.

HTTP equivalents are `GET /api/v1/manifests/export` (repeat `library_id`),
`POST /api/v1/manifests/preview` with `manifest` and `mappings`,
`GET /api/v1/manifests/plans/{id}` and
`POST /api/v1/manifests/plans/{id}/execute` with `{"confirm":true}`.
