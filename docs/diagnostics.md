# Local diagnostics

```sh
picshelf --data ./data doctor
picshelf --data ./data doctor --full > ./data/diagnostics.json
```

`doctor` reports application/Python/SQLite versions, expected and observed schema,
directory accessibility hints, dependency versions, program availability, stored
job-state counts, integrity counts and logical file byte totals. Exit 0 means no
actionable findings; exit 1 includes a next step. Machine keys and codes stay the
same in Chinese and English. An uninitialized directory is reported without
creating a database, signing key or directory. It does not migrate or repair data.

The report omits usernames, library names, absolute paths, hostnames, original
filenames, source URLs, notes, passwords, hashes, Cookie/Bearer values and signing
keys. It is generated locally and never uploaded. Review any report before sharing
it. Program availability means an executable is on PATH; it does not prove Docker's
daemon, a player's window or a terminal graphics protocol works. Writability is
an OS access hint, not a write probe. Stored job states are not changed or reconciled
by diagnostics; use `job list` for current process recovery.

Run `check --full` for a detailed local file report when aggregate findings need
investigation. That report includes library names and image IDs and is intended
for the local administrator. Restore originals from a separate backup, then scan;
rebuild derivatives only through the explicit `rebuild` command. Keep originals
and current database snapshots before attempting a repair.

`picshelf serve` disables raw HTTP access logs. Application operation/error logs
contain a server-generated request ID, route template, status, error code and
elapsed time, without URL queries, headers, body or exception text. The same
request ID is returned in `X-Request-ID` for correlation. Normal read requests
are quiet. Custom server runners have their own logging settings; configure them
before sharing logs. Diagnostics and logs do not add telemetry.
