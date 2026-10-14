# Reproducible environments and packages

`pyproject.toml` defines the package's dependency ranges. `uv.lock` records the
resolved application, development and browser dependencies with artifact hashes.
The build backend is pinned independently. Use the lock for a repeatable checkout:

```sh
uv sync --locked --extra dev --extra browser --python 3.11
make check
make migrations
make verify
```

Browser tests additionally require an installed Chromium executable. Runtime
does not need a browser or `httpx2`; the latter is a development dependency for
the current [Starlette TestClient](https://starlette.dev/testclient/).
Plain `httpx` remains available for independent socket and asynchronous tests.

`requirements/runtime.txt` is the hash-pinned runtime export consumed by Docker:

```sh
uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  --output-file requirements/runtime.txt
uv build --out-dir ./data/packages
```

After an intentional dependency update, review both lock files and run the
affected tests and package checks. Do not edit generated hashes by hand. Pin a
container base digest through `--build-arg PYTHON_IMAGE=python@sha256:DIGEST` when
that base has been reviewed; the default `python:3.11-slim` tag can change.
The application dependency export does not freeze OS packages or that base tag.

## Verify installed artifacts

Create independent environments, install the hash-pinned runtime dependencies,
then install the wheel or source archive with `--no-deps`. For example:

```sh
uv venv ./data/package-check/wheel --python 3.11
uv pip install --python ./data/package-check/wheel/bin/python \
  --require-hashes -r requirements/runtime.txt
uv pip install --python ./data/package-check/wheel/bin/python --no-deps \
  ./data/packages/pic_shelf-0.0.1-py3-none-any.whl
python tests/package_smoke.py --python ./data/package-check/wheel/bin/python \
  --root ./data/package-check/wheel-run --output ./data/package-check/wheel.json
```

Use a new `--root` each time. Repeat with the `.tar.gz` in another environment.
The driver clears `PYTHONPATH`, runs from a directory without the source package,
checks the actual import location and static resources, and exercises initialization,
indexed/managed libraries, copy preview/execution, zero rating, metadata export,
archive inspection, restore and read-only checking. It confirms original bytes
and independent copy identity, interrupted pending jobs and session invalidation
after restore. A frozen schema-v1 database is also upgraded through the installed
package, checking image identity, zero ratings, favorites, memberships, grants
and the private pre-migration snapshot. An installed package requires an explicit data path.

Wheel and source archive have been run on macOS arm64 with Python 3.11.15.
Lock resolution includes markers for other Python versions and platforms; that is
not runtime validation. Linux container lifecycle, other architectures, Windows
and other Python interpreters need their own runs before support claims. Container
configuration can be checked with `docker compose config -q` without a daemon;
build, mounts, non-root execution, shutdown and recovery require a running daemon.

Packages exclude runtime data, caches, collaboration files and internal planning
documents. Web assets ship in the wheel and require no Node build. Build artifacts,
installation fixtures and test reports belong under ignored `data/`.
