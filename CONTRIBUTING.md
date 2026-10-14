# Contributing

Pic Shelf is a Python application with a static JavaScript frontend. No frontend build step is required.

## Development setup

```sh
uv sync --locked --extra dev --python 3.11
.venv/bin/python -m pytest tests/ -q
```

Use a separate data directory for development. Initialize it with `picshelf --data <directory> init --admin <name>`, then use the same `--data` argument for subsequent commands. `picshelf serve --reload` reloads the API during development.

Use `make check` for the default Python regression suite and `make verify` for
complete local acceptance. The complete command runs regular tests, Node.js
module contracts, real loopback HTTP tests and Chromium tests. Each required
suite must collect tests and pass without skips. Exit code 1 means failure;
2 means an incomplete check, such as a missing runtime or skipped test. Other
suites continue when one environment is unavailable. Logs, JUnit reports and
the code/dependency summary are saved under the ignored `data/verification/`.
Run verification while the checkout is stable; changing code during the run
makes the result incomplete.

`make http`, `make browser` and `make modules` run individual required suites
with the same gate. `make migrations` and `make package` provide focused
checks. Installation, Docker lifecycle and personal image quality are separate
checks and are not implied by `make verify`. See [packaging](docs/packaging.md) for lock maintenance and independent
wheel/source installation, and [performance](docs/performance.md) for reproducible
benchmarks. Keep generated data and reports outside version control.

The CLI and TUI operate as a local administrator. The HTTP API authenticates each request and passes an explicit `AccessContext` to shared services. Query and rating operations live in `picshelf/catalog.py`; current account and library permissions are checked in `picshelf/access.py`. Keep transport validation in `picshelf/api/` and business invariants in services. Low-level storage helpers retain their existing interfaces and require the caller to enforce access.

## Browser tests

Browser tests are optional and launch an isolated local service with generated image fixtures. They do not use an existing library.

```sh
uv sync --locked --extra dev --extra browser
.venv/bin/python -m playwright install chromium
PICSHELF_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_web.py -q
make verify
```

To use an existing Chromium executable, set `PICSHELF_TEST_BROWSER` to its path. Browser tests cover both languages, desktop and phone layouts, pagination, ratings, tagged images, and nested dialogs.

The GitHub workflow uses the same `make verify` command with locked dependencies
on Linux. The setup follows the [uv GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/).

## Changes

- Add regression tests for changes to imports, scanning, permissions, search and playback. Use generated fixtures; do not commit personal images or runtime data.
- Keep Web, TUI, CLI and API behavior consistent. Document changes to commands or responses in `docs/usage.md`.
- Keep Chinese message IDs and add English translations to `picshelf/i18n.py` or `picshelf/web/js/i18n.js` as appropriate. Avoid interpolating user input as HTML or Rich markup.
- Database changes must append a migration in `picshelf/db.py`. Preserve existing image IDs and their ratings, tags, notes and album associations.
- Include the behavior change and actual validation results in the pull request description. Use concise English commit messages with a Conventional Commits prefix.
