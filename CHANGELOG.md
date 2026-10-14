# Changelog

## Unreleased

- Preserve fixed import previews and recover file operations after cancellation
  or interruption. Scan, import, rebuild and large metadata batches support
  persistent local background jobs.
- Distinguish library purpose, file errors and missing originals. Version derived
  images and fingerprints, preserve EXIF orientation/alpha/color behavior, and
  refuse ambiguous moves or replaced roots.
- Add source records, explicit organization state, conflict-safe batch undo,
  saved queries and resumable playback rules. Keep zero ratings separate from
  unrated images and retain existing random/shuffle semantics.
- Add portable metadata, validated database archives, verified root rebinding,
  naming templates and manual work/file-version relationships.
- Recheck current permissions, invalidate revoked sessions, restrict server import
  sources, and stabilize JSON errors, numeric bounds and noninteractive CLI use.
- Improve Web/TUI navigation, selection and editing; add local redacted diagnostics
  and reproducible dependency/package checks.
- Preserve unrelated memberships when editing a group, parse alternatives in the
  search condition editor, and stop random browsing at the current sample.
- Accept library, artist and favorite filters from URL query parameters.
- Reject out-of-range identifiers and malformed nested batch actions before
  database operations.
- Fix slideshow startup with native browser timers and complete English task
  and navigation labels.
- Update job progress incrementally and keep file verification outside database
  write transactions while rechecking catalog changes before publishing.

Existing databases upgrade through additive migrations and receive a consistent
pre-upgrade snapshot. Stop older processes before upgrading. Sign in again after
the authentication upgrade or a database restore. Historical libraries keep their
paths and unspecified purpose; choose a purpose explicitly before reorganization.
Old fingerprint records require an explicit rebuild to use the current algorithm.
Originals remain outside database archives and portable metadata. Development
package version remains `0.0.1`; this entry does not announce a published release.
