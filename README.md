<img src="picshelf/web/icons/icon-192.png" width="80" height="80" alt="Pic Shelf">

# Pic Shelf

**English** | [简体中文](README.zh-CN.md)

A self-hosted image library with Web, TUI and CLI interfaces. Browse your images,
organize them with tags and albums, and play slideshows from saved searches.
No telemetry, no external services.

[Usage](docs/usage.md) · [Deployment](docs/deployment.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)

## Features

- Index an existing directory or import images by copy, hardlink or move.
- Rate, favorite and organize images with tags, artists, albums and notes.
- Edit multiple images with a preview and undo; keep track of what needs organizing.
- Search by names, ratings, dimensions and dates; save searches as smart albums.
- Save slideshow settings and resume playback in Web, TUI or mpv.
- Link file versions, move library directories, export metadata and back up the catalog.
- Share libraries with user and group permissions. See [security](docs/security.md).

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/StepaniaH/pic-shelf.git
cd pic-shelf
uv sync --locked --extra dev --python 3.11
export PICSHELF_DATA="$PWD/data"
.venv/bin/picshelf init --admin admin
.venv/bin/picshelf library add photos /path/to/images --mode indexed
.venv/bin/picshelf scan photos
.venv/bin/picshelf serve
```

Replace `/path/to/images` with your image directory. Initialization prompts for
an administrator password of at least eight characters. Open the address printed by `serve`, or run
`.venv/bin/picshelf tui` for the terminal interface.

Originals stay in the registered directory. The data directory holds the database
and thumbnails. Use the same data directory for every command; installed packages
require `PICSHELF_DATA` or `--data`. See [installation](docs/packaging.md).

To import into a managed library, see [importing](docs/usage.md#import-and-scanning).
The web app accepts dropped files, a folder, or an approved server path; hard links
are used when source and library are on the same filesystem, otherwise copies.

## Docker

```yaml
services:
  picshelf:
    image: stepaniah/pic-shelf:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:39431:39431"
    volumes:
      - ./data:/data
      - /path/to/images:/libraries/photos:ro
    user: "1000:1000"
```

`user` is your own UID and GID (`id -u`, `id -g`); `./data` must be writable by it.
Create the directory before the first start, initialize once, then run:

```sh
mkdir -p data
docker compose run --rm picshelf picshelf init --admin admin
docker compose up -d
```

Add one volume line per library you want to index. The image is also published to
`ghcr.io/stepaniah/pic-shelf`. See [deployment](docs/deployment.md) for remote access,
upgrades and permission details.

## Keeping your collection

Back up originals separately from the [catalog backup](docs/backups.md).
[Portable metadata](docs/metadata-format.md) transfers tags and other organization
between installations. [Diagnostics](docs/diagnostics.md) help investigate missing
files, scan errors and storage use.

Animated and multipage images use their first frame or page for previews. Format
support depends on Pillow; see [supported formats](docs/usage.md#scan-safety-and-derivative-versions).
Video and comic archives are not supported.
