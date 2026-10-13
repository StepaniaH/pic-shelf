# Authentication and shared-library permissions

Pic Shelf is a private, trusted shared catalog. It has no open registration or tenant isolation. The administrator and anyone who can write the data directory control the server. Run it on a private interface and give its OS account access only to the intended libraries and import directories. Local CLI and TUI commands use the data directory owner's authority; selecting another user for ratings does not make the CLI a remote authentication boundary.

## Access rules

| Operation | Required authority |
| --- | --- |
| Browse, media, search, metadata export | Enabled account, `view` capability and library view access |
| Personal ratings, favorites, comments, notes | Library view plus the corresponding group capability; deleting comments/notes also requires ownership or administrator status |
| Personal inbox/done state | Library view; each account has its own state |
| Image tags, artists and source records | `tag_edit` and library edit access |
| Edit a manual album | `tag_edit`, edit access to its library and every current member's library; newly added images also require edit access |
| Rename tags or edit artist profiles, aliases and association merges | Global `admin` capability |
| Create an unlinked tag or artist | `tag_edit`; existing entries linked only to hidden libraries are not exposed |
| Configure an existing library, scan, import | Library manage access; imports also require source authorization below |
| Register server library paths, approve import sources, manage accounts/groups | Global `admin` capability |
| Read or control persisted jobs/import previews | Creator or administrator, plus current library manage access |
| Read/navigate a playback session | Owner or administrator, plus current access to every library represented in the queue |

Capabilities are read from current group membership on each operation. Library grants alone do not replace `view`. Legacy albums without a library can be read when they contain visible images; editing them requires global administration. Counts and covers respect visible images. Tags and artist names remain shared catalog entities, not private per-user objects.

A playback queue whose library access was revoked returns `play_scope_changed`; create a new session using current access. Its owner can still delete the old session. Background file work checks current authority at each file boundary. Revocation stops subsequent files, not an already completed operation.

## Sessions and upgrades

Cookies are HttpOnly and SameSite=Lax, with a 30-day expiration. Bearer authentication remains available through `Authorization: Bearer <token>`. A password change, administrator password reset, CLI password reset, account enable/disable change, or explicit revocation increments the account session version. All older tokens then fail, including Bearer tokens. Group and library permission changes take effect without waiting for token expiration.

`POST /api/v1/auth/logout` revokes the presented token. `POST /api/v1/auth/revoke-sessions` revokes all sessions for the current account; the Web user controls include **Sign out all devices**. No token plaintext is stored in the revocation table. The signing key is published atomically with owner-only permissions.

Schema 7 introduces session versions. Tokens issued by earlier builds are rejected after upgrading; sign in again and replace script tokens. Existing password hashes and user IDs are preserved. The upgrade also restricts global name/profile edits to administrators and image associations to library editors. Explicitly grant the needed roles; do not grant global administration just to restore a local editing workflow.

## Browser origins and proxies

Cookie-authenticated mutations must carry an exact matching `Origin`, or a matching `Referer` when Origin is absent. Cross-site browser requests are rejected, including login requests. Non-browser login clients may omit those headers; subsequent scripts should send Bearer credentials. Bearer credentials take precedence over cookies and never fall back to a cookie if invalid. The origin comparison includes scheme, hostname and port, following the [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

Local HTTP uses a non-Secure cookie so local login works. Direct HTTPS uses Secure. For TLS termination, set `PICSHELF_PUBLIC_ORIGIN` to the exact public HTTP(S) origin without a path, query or credentials. An HTTPS public origin enables Secure cookies even if the private upstream uses HTTP.

The `picshelf serve` command ignores forwarded headers by default. If the deployment needs them, set `PICSHELF_TRUSTED_PROXIES` to a comma-separated list of specific proxy IP addresses or networks. A wildcard is rejected. The proxy must overwrite forwarded headers and prevent direct access to the upstream. If invoking Uvicorn directly, configure its proxy trust explicitly too. An arbitrary `X-Forwarded-Proto` or `X-Forwarded-Host` header does not establish trust. A wrong public-origin setting causes actionable origin errors instead of weakening checks.

Five failed logins per account or thirty per client address within sixty seconds trigger a temporary limit with `Retry-After`. Counters are shared through SQLite; up to four concurrent password checks run per application process, so already-admitted attempts may finish after a threshold is reached. Trusted proxies affect which peer address is used. Usernames and peer addresses are stored only as keyed bucket identifiers. Password inputs are limited to 1024 characters and API mutation bodies to 1 MiB. Validation responses omit submitted values.

## Import sources

Library manage access does not grant filesystem-wide read access. For delegated managers, an administrator must approve source roots:

```sh
picshelf import-sources allow <source-directory>
picshelf import-sources list
picshelf import-sources revoke <root-id>
```

By default an approved root permits copy and hardlink imports. Add `--allow-move` only when delegated managers may remove successfully imported source files. The equivalent admin endpoints are `GET/POST /api/v1/import-sources` and `DELETE /api/v1/import-sources/{id}`; the POST body has `root_path` and optional `allow_move`.

An approved root is shared by library managers, not assigned to an individual user. Approval records its canonical directory and filesystem identity. Preview and execution, including each file boundary, check that identity and the requested mode. Symlink escapes are rejected. Replacing/remounting a root requires a new explicit approval. Revoking approval stops future work; it does not undo files already imported. Server administrators and the local data directory owner retain explicit filesystem authority. This is not a sandbox against a hostile local process that already controls the filesystem.

## Content and caches

Authenticated API and media responses use `Cache-Control: private, no-store`; switching accounts or losing access must not reuse a private cached response. Versioned media URLs and ETags still identify content but do not allow persistent browser caching. Previously cached responses from an older server may remain until the browser discards them; clear old site data when upgrading on a shared browser. Session and cache considerations follow the [OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

Media uses `X-Content-Type-Options: nosniff`. Only supported raster MIME types are displayed inline; a legacy SVG/HTML or unknown original is returned as an attachment with `application/octet-stream`. Source descriptions are literal text and are never fetched. Artist links render only HTTP(S) URLs without embedded credentials, with no opener or referrer. User metadata is not interpreted as Rich/ANSI markup; terminal preview renderer output is handled separately.

Database backups contain password hashes, grants, metadata and authorization records. Treat them as private files. The signing key and original image directories require separate backups. Restoring an older authorization database can restore older grants and session versions; stop writers, review access, and revoke sessions after a restore before reopening the service.
