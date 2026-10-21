# Metadata and provenance

Each image has a catalog ID that links its file to ratings, tags, artists and
notes. Scanning preserves the ID when a moved file has an unambiguous content
match. [Works](works.md) group related files while keeping their individual metadata.

| Term | Meaning |
| --- | --- |
| Artist | A named creator with optional aliases, links and a note |
| Tag | A shared named label; a namespace such as `color:blue` is part of the name |
| Album | A folder-derived or manually maintained group of image records |
| Provenance | An attributed statement about where an image came from |
| Organization state | A user's explicit `inbox` or `done` flag for an image |

## Names and aliases

New display names use Unicode NFC and trim surrounding whitespace. Matching
additionally folds case, collapses whitespace and trims spaces around a namespace
colon. Display spelling remains separate from the matching key. Existing names
are preserved on upgrade, including names that now share a key. An exact display
name still selects its existing entry; otherwise an ambiguous match returns
`name_ambiguous`. Use the exact spelling or an ID to disambiguate.

Structured aliases participate in tag search, artist search, suggestions and
name-based association. Existing free-text artist aliases remain unchanged and
continue to support substring search. Structured aliases are returned separately
as `name_aliases`. Search results follow the current library permissions.

Administrators can preview an alias or transfer selected associations between
two existing tags or artists. Each preview lists source, target, image IDs,
existing associations and a 24-hour expiry. Execution requires the saved plan ID
and explicit confirmation. It rechecks the account, permissions and relevant
metadata under a database write transaction. Changed names, aliases or selected
associations require a new preview. Unselected associations remain untouched;
existing target associations retain their author and timestamp. The source
entity remains in the catalog, even when it has no remaining associations.
Repeated execution of a completed plan returns its recorded result.

```sh
picshelf metadata alias-preview tag 12 'alternate name'
picshelf metadata merge-preview artist 4 9 101 105
picshelf metadata execute <plan-id> --yes
```

## Sources and time

Source records preserve an original filename, collected time, creation time,
URL and note independently. `origin` distinguishes `manual`, `imported` and
`extracted` statements; the creator and recording time are retained. Source URLs are stored as text.

Managed imports record the original filename before normalization and the time
the file operation completed. Source directories are excluded from the image's
provenance response. Repeated execution does not duplicate this record. Scanning
an existing library does not invent its original name or collection history.
Legacy records without reliable collection times remain unknown.

Times supplied for collection or creation must include a timezone. Storage uses
UTC ISO 8601 instants, and the Web metadata panel displays UTC explicitly. Unknown
times remain empty; naive local times are rejected. `images.imported_at` retains
its released meaning, the time the image first entered the catalog. It is labeled
**First indexed** in the viewer. File modification time remains an epoch value;
neither field is substituted for the source's collected or creation time.

```sh
picshelf metadata source 101 --original-filename 'original.png' \
  --url 'https://example.org/work/123' --note 'Provided by the artist' \
  --collected-at '2026-10-10T12:00:00+08:00'
picshelf metadata show 101 --user reader > image-metadata.json
```

`metadata show` and `GET /api/v1/images/{id}/metadata` return the same
`picshelf.image-metadata/1` JSON structure. It includes library name, relative path,
content hash, provenance, the selected user's organization state, tag evidence
and structured aliases. It contains local IDs and is a record export, not yet a
standalone import/reconstruction command. Database backups preserve these tables;
original files still need their own backup.

## Tag evidence and organization

Tag evidence records the origin (`manual`, `imported` or `suggested`), source
reference, author, timestamp and confirmation state. Existing image-tag links
are backfilled as confirmed manual evidence without changing the links. Imported
or suggested labels remain unconfirmed until an editor explicitly accepts them;
unconfirmed evidence does not appear as a tag in ordinary search. Confirmation
retains the original origin and records who confirmed it. Removing a tag retires
its active evidence; an old confirmation request cannot silently add it back.

Every user starts with `inbox` for every image. Only an explicit organization
action changes this flag. `done` is independent of rating zero, no rating,
favorites and file availability. Missing files retain their metadata and personal
state. A batch requires 1–1000 explicit image IDs and validates the entire set
before writing; an inaccessible ID causes no partial change. No new images are
included implicitly.

```sh
picshelf organize 101 105 --state done --user reader
picshelf organize 101 --state inbox --user reader
```

Viewing provenance and changing personal state require access to the image.
Shared source and tag evidence changes require `tag_edit` and at least `edit`
library access. Alias and association-merge plans require an administrator.

Additive API endpoints under `/api/v1`:

- `GET /images/{id}/metadata`
- `POST /metadata/workflow` with `image_ids` and `state`
- `POST /images/{id}/sources` with optional source fields
- `POST /images/{id}/tag-claims` with `name`, `origin` and `source_ref`
- `POST /tag-claims/{id}/confirm`
- `POST /metadata/aliases/preview` with `kind`, `entity_id` and `alias`
- `POST /metadata/merges/preview` with `kind`, `source_id`, `target_id` and `image_ids`
- `GET /metadata/plans/{id}`
- `POST /metadata/plans/{id}/execute` with `confirm: true`
