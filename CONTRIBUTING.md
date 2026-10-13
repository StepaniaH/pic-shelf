# Contributing

Pic Shelf is a Python application with a static JavaScript frontend. No frontend build step is required.

## Development setup

```sh
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
```

Use a separate data directory for development. Initialize it with `picshelf --data <directory> init --admin <name>`, then use the same `--data` argument for subsequent commands. `picshelf serve --reload` reloads the API during development.

The CLI and TUI operate as a local administrator. The HTTP API authenticates each request and passes an explicit `AccessContext` to shared services. Query and rating operations live in `picshelf/catalog.py`; current account and library permissions are checked in `picshelf/access.py`. Keep transport validation in `picshelf/api/` and business invariants in services. Low-level storage helpers retain their existing interfaces and require the caller to enforce access.

## Browser tests

Browser tests are optional and launch an isolated local service with generated image fixtures. They do not use an existing library.

```sh
uv pip install --python .venv/bin/python -e ".[dev,browser]"
.venv/bin/python -m playwright install chromium
PICSHELF_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_web.py -q
```

To use an existing Chromium executable, set `PICSHELF_TEST_BROWSER` to its path. Browser tests cover both languages, desktop and phone layouts, pagination, ratings, tagged images, and nested dialogs.

## Changes

- Add regression tests for changes to imports, scanning, permissions, search and playback. Use generated fixtures; do not commit personal images or runtime data.
- Keep Web, TUI, CLI and API behavior consistent. Document changes to commands or responses in `docs/usage.md`.
- Keep Chinese message IDs and add English translations to `picshelf/i18n.py` or `picshelf/web/js/i18n.js` as appropriate. Avoid interpolating user input as HTML or Rich markup.
- Database changes must append a migration in `picshelf/db.py`. Preserve existing image IDs and their ratings, tags, notes and album associations.
- Include the behavior change and actual validation results in the pull request description. Use concise English commit messages with a Conventional Commits prefix.
