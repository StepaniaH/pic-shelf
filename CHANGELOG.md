# Changelog

User-facing changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.0.4] - 2026-11-05

Maintenance release. The interface and behaviour are unchanged from 0.0.3; this build exists so
the published version points at the exact revision that was tested.

## [0.0.3] - 2026-11-05

The interface was redesigned around one rule: content is the subject, controls stay quiet.

### Changed

- The sidebar groups pages into Browse, Organize and Settings. Library is reached from the
  logo, and the image grid is named Images.
- List and grid pages use text actions instead of filled buttons and outlined boxes; sort and
  density selectors carry a background instead of a border, while the search field keeps its
  outline as the primary input.
- The image, favorites and inbox grids show a selection bar under the heading with the selected
  count and a single exit action.
- The viewer labels every panel row, and its shortcuts are listed in one place on screen.
- Album cards show the image count and album type once, without repeating the album name.
- Admin uses text tabs with an underline for the current section. Libraries are listed as
  records with their actions on the right, and adding a library sits at the end of the list.
- Tag rows reveal Manage tag on hover or keyboard focus. On touch devices an actions button
  opens it, and tapping the tag name still lists its images.
- The library home page uses the same quiet controls for adding, importing and viewing all.
- On a phone the search field collapses to an icon in the top bar. Tapping it opens the field and
  focuses it; Enter submits and closes it, and closing keeps the query. Desktop keeps the field
  in the bar.

### Fixed

- Counted English strings use the singular for one item, including templates that count two
  different sets in one sentence.
- Undoing a batch restores every card that was removed instead of leaving the list one short.

### Upgrading

No database changes. Interface changes apply on reload; no configuration or migration is
required.

## [0.0.2] - 2026-11-02

First published build. Everything below landed between the initial commit and this release.

### Added

- Managed libraries: import by copy, hard link or move; drag images or folders into collection dialogs, or browse an approved server folder without typing a storage path.
- Light, dark and system appearance modes with nine color palettes and application icons.
- Background jobs for scan, import, rebuild and large metadata batches, with progress that survives a restart.
- Library purpose, explicit organization state (inbox / done) and source records per image.
- Portable metadata manifests, validated database archives, root rebinding and naming templates.
- Saved queries, smart albums, resumable playback rules and work/file-version relationships.
- Local redacted diagnostics and storage categories, including cache cleanup candidates without deleting them.
- Library, artist and favorite filters from URL query parameters.

### Changed

- Large image grids stay bounded, adjacent slideshow previews preload, and collection pickers and task histories are paged instead of loading full journals.
- Derived images and fingerprints are versioned; EXIF orientation, alpha and color behavior are preserved.
- Each scanned image is decoded and normalized once, and source changes are verified before derivatives are published.
- Imports refresh only the paths they touched instead of walking the whole library.
- Web and TUI navigation, selection and editing were reworked; repository checks now verify installed packages and locked dependencies.
- The Docker instructions create the data directory before the first build, run the first start in the foreground, and pass the initial password on standard input.
- Administrators must choose a password of at least eight characters.

### Fixed

- Files that are not regular files — pipes, sockets, devices — are reported and skipped instead of stalling an import preview.
- Permissions and revoked sessions are rechecked on every request; server-side import sources are restricted.
- Library memberships are preserved when editing a group; album permissions are rechecked when portable metadata reuses an album.
- Tag additions are atomic, batch undo is conflict-safe, and malformed or out-of-range identifiers are rejected before any database write.
- Slideshow start uses native browser timers; pending TUI previews are invalidated when playback access fails.
- Job progress updates incrementally, and file verification runs outside database write transactions.
- Catalog query rules are shared across transports while URL parsing and JSON type checks stay intact.
- Scan recovery messages are actionable, and task kinds, phases and item states are translated in the TUI.
- Grayscale and RGB PNG transparent colors survive derivatives and fingerprints.
- Slideshow, navigation and task labels are complete in English.

### Upgrading

Existing databases upgrade through additive migrations and receive a consistent pre-upgrade snapshot. Stop older processes before upgrading. Sign in again after the authentication upgrade or a database restore. Historical libraries keep their paths and unspecified purpose; choose a purpose explicitly before reorganization. Fingerprint algorithm version 3 requires an explicit rebuild of older records. Originals stay outside database archives and portable metadata.

## [0.0.1]

Initial commit. Not published.
