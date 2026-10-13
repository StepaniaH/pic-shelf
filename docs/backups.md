# Backup and recovery

A database archive preserves accounts, permissions, image identities and catalog
metadata. It includes password hashes and local library roots. Store it privately;
use `--encrypt` when transferring it. [Portable metadata](metadata-format.md) is
the narrower format for moving one user's organization without account secrets.

| Component | Recovery role |
| --- | --- |
| Original library directories | Original bytes; back these up independently |
| Database archive | Accounts, grants, index and organization; SQLite snapshot includes committed WAL |
| Derivatives | Optional cached thumbnails/previews; can be rebuilt from originals |
| `secret.key` | Machine signing key; excluded from archives; a new instance generates its own |
| Configuration | Data path, origin, proxy trust and mounts; save these separately |
| Metadata manifest | Explicit user-scoped interchange; no originals or credentials |

## Create and inspect

```sh
picshelf export ./data/exports/catalog.zip --with-thumbs
picshelf backup inspect ./data/exports/catalog.zip
picshelf export ./data/exports/catalog.fernet --encrypt
```

In a pipeline, provide the passphrase using `--passphrase-stdin`, through a
protected input stream. Do not place it in command arguments. Export creates a
new file with owner-only permissions and refuses an existing destination. A
complete archive is published atomically. Each database or cache member has a
SHA-256 and byte count in `meta.json`. The original-file inventory describes the
indexed relative paths and hashes; it does not prove that live originals have
been independently copied. Use `check --full` before and after copying originals.

Archives use format 2. Format 1 and older database-only ZIPs remain readable;
inspection reports `legacy_without_checksums: true`. Their database integrity is
checked, but they lack the newer member checksums. Unsupported formats or schemas,
duplicate members, escaping paths, inconsistent inventories and broken database
relationships are refused. Limits are 100,000 members, 4 GiB expanded contents,
20 MiB metadata, and 256 MiB plaintext archive for passphrase encryption.

## Restore offline

1. Copy the original directories from their independent backup.
2. Stop Web, worker, CLI and TUI processes using the destination data directory.
   Also stop external programs accessing SQLite directly.
3. Inspect the archive, then restore to an empty destination:

   ```sh
   picshelf --data ./restored-data backup inspect ./data/exports/catalog.zip
   picshelf --data ./restored-data import-backup ./data/exports/catalog.zip
   ```

4. For an existing destination, review the inspected archive and use `--force`
   only when replacing that database is intended. It creates a consistent
   `picshelf.db.bak-*` snapshot before replacement. Validation and required schema
   upgrades finish on a temporary copy before touching the destination contents.
5. Run `check --full`. If roots moved, use the library relocation workflow before
   scanning. If derivatives were omitted, execute `rebuild <library>` explicitly.

Application database connections hold a shared maintenance lease for their
lifetime. Restore requires an exclusive lease; an active connection returns
`operation_busy` without replacing the database. SQLite's backup API performs the
database replacement in its own destination transaction. A failing copy leaves
the previous database contents available; its safety snapshot remains usable.
See the [SQLite backup API](https://www.sqlite.org/backup.html).

Included derivatives are written to a fresh cache namespace before the database
switch, so existing cached files are preserved. A failure can leave unreferenced
cache files, which `check` reports; it does not remove them. Restored active or
queued file jobs become interrupted and require explicit retry. Sessions are
invalidated on restore even when the destination keeps its signing key. Sign in
again. Losing `secret.key` invalidates sessions but does not lose images or
catalog metadata. Losing an encrypted archive's passphrase prevents decryption.

## Diagnose without repairing

```sh
picshelf check
picshelf check --full
```

These JSON commands open SQLite read-only, do not migrate it, and do not change
missing markers, originals, relationships or cached images. The report covers
database/foreign-key integrity, root identity, missing originals, byte-hash
changes, invalid paths, missing/unreferenced derivatives and invalid album covers.
Byte totals are logical file sizes, not deduplicated physical allocation. Detailed
findings stop at 200; counts still cover the complete checked scope. A concurrently
changed file is reported without treating its transient hash as authoritative.

`check` exits 1 for actionable findings and 0 when the only finding is unused
cached derivatives. Inspect the report before repair. Restore missing originals
from their independent backup; `scan` updates the index only after the root is
available. Use `rebuild <library>` to recreate derivatives as a separate explicit
task. No command here automatically deletes original files or orphan records.
