# Pic Shelf deployment guide (Docker)

**English** | [简体中文](zh/deployment.md)

For self-hosted deployment: the service listens on **loopback only** by default and is not exposed.
For remote access, use a private network such as Tailscale (see “Networking and access”).

## Quick start

```bash
git clone https://github.com/StepaniaH/pic-shelf.git
cd pic-shelf

# 1) Build the image, then create the data directory (write access: see UID/GID below)
docker compose build
mkdir -p data

# 2) Initialize once; the web service is not running yet
docker compose run --rm picshelf picshelf init --admin admin

# 3) Start the service (loopback only by default)
docker compose up -d
```

Step 2 prompts for the administrator password. Watch the output: if `init` fails, the reason appears there.

## Directories and volumes

| Host | Container | Purpose |
|---|---|---|
| `./data` | `/data` | Database, thumbnails and previews, signing key, playback playlists (must be persisted) |
| Your image directories (any number) | Any path, e.g. `/libraries/photos` | Image libraries; `:ro` is enough for indexed mode |

Add library directories under `volumes:` in `docker-compose.yml`, then register them in the container:

```bash
docker compose exec picshelf picshelf library add photos /libraries/photos
docker compose exec picshelf picshelf scan photos           # incremental scan
```

Managed imports (hardlink / copy / move) need the library directory to be **writable**: drop `:ro`, or
mount a separate writable directory for managed libraries.

## Networking and access

- Default `127.0.0.1:39431` — reachable from this machine only (recommended).
- **Tailscale / LAN access**: change the left-hand side of the port mapping, for example
  `100.x.y.z:39431:39431` (tailnet address) or `192.168.x.y:39431:39431`.
- **Reverse proxy**: Pic Shelf serves plain HTTP; terminate TLS in front (Caddy, Nginx, …) and point
  the upstream at `host-ip:39431`.
- Never map the port straight to the public internet. The service has no telemetry and no outbound
  calls; the authentication model assumes trusted use on a private network.

A TLS-terminating deployment should set `PICSHELF_PUBLIC_ORIGIN` to the exact site origin the browser
uses (no path), so state-changing requests match and Secure cookies are enabled. Plain local HTTP needs
nothing. `picshelf serve` ignores proxy headers by default; to accept them, `PICSHELF_TRUSTED_PROXIES`
takes explicit proxy IPs or networks and rejects `*`. The proxy must overwrite forwarding headers and
block direct upstream access. See [authentication and sharing](security.md) for configuration, session
upgrade effects, and import source permissions.

## Permissions (UID/GID)

The container runs as UID 1000, and `./data` must be writable by it. If your host UID is not 1000,
choose one:

```bash
sudo chown -R 1000:1000 ./data     # option A: give the data directory to UID 1000

# option B: uncomment user: in docker-compose.yml and set your own UID:GID
```

## Upgrading

```bash
git pull
docker compose up -d --build        # rebuild and replace; database migrations run on startup
```

All state lives in `./data`, so rebuilding the container leaves it untouched.

## Backup and restore

Verify the archive with `backup inspect` first, then stop the web service, workers, TUI, and any other
database connection: a restore must not run while anything else holds the data directory. With the
service stopped, use a separate `docker compose run --rm picshelf picshelf import-backup ...`. Full
restore boundaries are in [backup notes](backups.md); for a different mount path see
[path verification](paths.md); for troubleshooting export [redacted diagnostics](diagnostics.md).

- **Application level**: `docker compose exec picshelf picshelf export /data/exports/backup.zip [--encrypt --passphrase-stdin]`
  (a consistent database snapshot, optionally with thumbnails; originals are not included and need their
  own backup). Restore with `picshelf import-backup`; stop writes before restoring. `--force` first keeps
  the existing data through the SQLite backup interface, including committed WAL content. After a
  database restore, run `scan <library> --full` to rebuild derivatives.
- **Volume level**: stop the service, snapshot the `./data` directory (derivatives and signing key
  included), and back up the original directories separately. A running SQLite database cannot be
  backed up by copying the main file alone.

## Troubleshooting

- **Page errors / cannot sign in**: confirm step 2 (`init`) ran — it creates the administrator and the
  signing key.
- **Scanning a large library is slow**: the first scan decodes every image to build thumbnails; time
  depends on image size, format, disk, and CPU. Incremental scans skip files whose content is unchanged
  and whose derivatives are complete.
- **Port already in use**: change the left-hand port in compose, e.g. `127.0.0.1:39441:39431`.
- **Using the CLI / TUI**: run every CLI command as `docker compose exec picshelf picshelf <command>`.
  The TUI needs a real terminal, so running it from a host virtualenv is more practical.

## Without Compose (equivalent `docker run`)

```bash
docker build -t pic-shelf:local .
mkdir -p data  # prepare write access as described under UID/GID
docker run --rm -it -v "$PWD/data:/data" pic-shelf:local picshelf init --admin admin
docker run -d --name picshelf --restart unless-stopped \
  -p 127.0.0.1:39431:39431 \
  -v "$PWD/data:/data" \
  pic-shelf:local
```
