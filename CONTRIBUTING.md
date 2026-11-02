# Contributing

Pic Shelf uses Python and native JavaScript modules. The frontend has no build step.

## Setup

```sh
uv sync --locked --extra dev --extra browser --python 3.11
.venv/bin/python -m playwright install chromium
```

Use a separate development data directory. Initialize it with
`picshelf --data <directory> init --admin <name>` and pass the same directory to
subsequent commands. `picshelf serve --reload` reloads the API during development.

## Verification

```sh
make check     # Python regressions and whitespace
make verify    # Python, JavaScript, loopback HTTP and Chromium suites
```

Full verification requires Node.js and Chromium. It uses generated fixtures and
fails if a required suite fails or skips tests. Exit 1 indicates failure; exit 2
indicates an incomplete check. Keep the checkout unchanged during the run. Reports
are saved under the ignored `data/verification/` directory.

Run individual suites with `make http`, `make browser`, `make modules` or
`make migrations`. Set `PICSHELF_TEST_BROWSER` to use another Chromium executable.
See [packaging](docs/packaging.md) for installation and container checks, and
[performance](docs/performance.md) for benchmarks. CI runs `make verify` on Linux.

## Changes

- Add regression coverage for imports, scanning, permissions, queries and playback.
  Keep generated data, reports and personal images out of version control.
- Keep Web, TUI, CLI and API behavior consistent; update the relevant usage guide.
- Use Chinese message IDs and add English translations. Render user input as text.
- Keep transport validation in `picshelf/api/` and business rules in shared services.
  HTTP operations use `AccessContext`; CLI and TUI use local administrator access.
- Append database migrations in `picshelf/db.py`. Preserve image IDs and metadata.
- Describe the behavior change and actual test results in pull requests. Use
  concise English commits with a Conventional Commits prefix.

## Maintainer releases

Releases are tag-only: pushing a `v*` tag creates the GitHub release from
`CHANGELOG.md` and publishes the image. Nothing is published from a branch.

Before tagging:

1. Move the notes out of `## [Unreleased]` into a dated `## [X.Y.Z]` section and set
   the same version in `pyproject.toml` and `picshelf/__init__.py`.
2. Run `make check` and `make verify` on a clean checkout of `main`.
3. Confirm the repository has valid `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets.
4. Create and push an annotated tag:

   ```sh
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

The tag publishes `stepaniah/pic-shelf:X.Y.Z` and moves `latest`, and the same image to
`ghcr.io/stepaniah/pic-shelf`. Only tag a prerelease when moving `latest` is intended.
Do not move or reuse a published tag; roll back by deploying an earlier version tag.
