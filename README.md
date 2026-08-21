# Media Uploader

Media Uploader is a small Flask application for uploading large images and videos in chunks and sharing them through expiring bearer links. Redis holds short-lived upload/share state; file bytes live on local or container-mounted storage.

## Highlights

- Chunked browser uploads with bounded client concurrency, retry/backoff, progress, cancellation, and stable transfer IDs for retry-safe chunk delivery.
- Server-side enforcement of chunk shape, per-file size, session byte/file quotas, and extension-to-MIME mapping while preserving valid Unicode filenames verbatim.
- Random server-generated storage IDs. User filenames are metadata only and never become storage paths.
- Expiring share tokens checked on **every** summary, preview, and download request.
- Filesystem expiry markers plus a cleanup worker so Redis TTL expiry does not leave permanent uploads behind.
- CSRF protection, trusted-host support, configurable proxy trust, secure-cookie support, rate limits, strict response headers, and a same-origin CSP.
- Retry-safe, idempotent finalization so a lost Redis/network response does not strand a completed share.
- Structured audit events with stable first-party browser correlation for site requests, upload sessions, completed files, share creation/opening, previews/downloads, denials, CSRF failures, and rate limits, persisted without flooding the interactive server console by default.
- Password-protected administration panel with share history, creator network/browser metadata, expiry/status, access counters, file details, and immediate revocation.
- SHA-256 is computed while each file is assembled, stored with share metadata, exposed on the summary/download response, and recorded in audit events.
- A global free-space safety margin rejects new work before uploads can exhaust the filesystem.
- Redis persistence and health checks in the Docker deployment.
- Pytest, Ruff, mypy, Playwright browser E2E coverage, JavaScript syntax checks, Docker build CI, and Dependabot configuration.

## Security model

A share URL is a bearer capability: anyone who possesses a live `/s/<token>` URL can access the files associated with it until the Redis token expires. The token is revalidated for each file response, so retaining a file URL does not bypass expiry.

This application is not an authentication or identity system. Audit events correlate a browser using a random signed first-party visitor identifier and also record network/request metadata such as IP address and User-Agent; none of those values prove a real-world identity. If you need user accounts, per-user authorization, malware scanning, moderation, or compliance-grade identity, add those explicitly for your deployment.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment expectations.

## Requirements

- Python 3.11+
- Redis
- `libmagic` (`libmagic1` on Debian/Ubuntu)
- Node.js only if you want to run the JavaScript syntax checks locally

## Local development

From a repository checkout:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -c constraints.lock -e . --group dev
cp .env.example .env  # Windows: copy .env.example .env
```

Generate a development secret and place it in `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Redis is required for upload state, share authorization, and rate limiting. For local development, the simplest option is the loopback-only Redis Compose service:

```bash
docker compose -f docker-compose.dev.yml up -d
```

This uses the dedicated Compose project name `media-uploader-dev` and publishes Redis only on `127.0.0.1:6379`; it does not collide with the production Compose stack or expose Redis on other network interfaces. If port 6379 is already in use, set `REDIS_DEV_PORT` and point `REDIS_URL`/`E2E_REDIS_URL` at that port. If you already run Redis elsewhere, set `REDIS_URL` in `.env` instead.

Verify Redis before starting the application:

```bash
docker compose -f docker-compose.dev.yml exec redis redis-cli ping
```

The command should print `PONG`. Then run:

```bash
media-uploader
```

The server performs a Redis preflight before Waitress starts. If Redis is unavailable, startup fails immediately with an actionable error instead of starting a server that can only return HTTP 503 responses.

The Waitress entry point binds to `0.0.0.0:5000`, so it can be reachable on your LAN when the host firewall allows it. Use the production reverse-proxy deployment for Internet exposure rather than publishing the Waitress port directly. To stop the local Redis service later:

```bash
docker compose -f docker-compose.dev.yml down
```

## Configuration

The application loads `.env` from the repository root. Important settings are:

| Variable | Default | Purpose |
|---|---:|---|
| `FLASK_SECRET_KEY` | required | Flask/CSRF signing secret; at least 32 characters |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `UPLOAD_FOLDER` | `./uploads` | Persistent upload root |
| `CHUNK_SIZE` | 5 MiB | Expected browser chunk size |
| `MAX_FILE_SIZE` | 1 GiB | Maximum assembled file size |
| `MAX_REQUEST_SIZE` | chunk + 2 MiB | Maximum HTTP request size; keep close to chunk size |
| `MAX_FILES_PER_SESSION` | 20 | Per-session file quota |
| `MAX_SESSION_BYTES` | 2 GiB | Per-session reserved byte quota |
| `SESSION_EXPIRY_SECONDS` | 1800 | Lifetime of unfinished upload sessions |
| `TOKEN_EXPIRY_SECONDS` | 21600 | Lifetime of finalized share links |
| `CLEANUP_EXPIRY_GRACE_SECONDS` | 60 | Disk-cleanup grace after Redis authorization expires |
| `MIN_FREE_DISK_BYTES` | 512 MiB | Global filesystem safety margin preserved before accepting/assembling uploads |
| `WTF_CSRF_TIME_LIMIT_SECONDS` | 43200 | CSRF-token lifetime for long upload sessions |
| `UPLOAD_CONCURRENCY` | 3 | Concurrent files uploaded by the browser |
| `CHUNK_UPLOAD_RETRIES` | 2 | Automatic retries per failed chunk |
| `TRANSFER_FINALIZE_TIMEOUT_SECONDS` | 900 | Reclaim a transfer left in finalizing state after a crashed request |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `HSTS_MAX_AGE` | 0 | HSTS max-age in seconds; Compose sets one year over HTTPS |
| `TRUSTED_HOSTS` | unset | Comma-separated accepted Host values |
| `PROXY_FIX_X_FOR` | 0 | Number of trusted proxies setting `X-Forwarded-For` |
| `PROXY_FIX_X_PROTO` | 0 | Number of trusted proxies setting `X-Forwarded-Proto` |
| `PROXY_FIX_X_HOST` | 0 | Number of trusted proxies setting `X-Forwarded-Host` |
| `AUDIT_ENABLED` | `true` | Emit structured application audit events |
| `AUDIT_LOG_STATIC` | `false` | Include static asset requests in application audit events |
| `AUDIT_LOG_HEALTH` | `false` | Include `/healthz` probes in application audit events |
| `AUDIT_LOG_CONSOLE` | `false` | Mirror JSON audit events to the interactive process stderr/terminal |
| `AUDIT_LOG_PATH` | `./logs/audit.jsonl` | Rotating JSONL audit log path; set empty to disable file persistence |
| `AUDIT_RETENTION_DAYS` | 30 | Number of daily rotated audit log files to retain |
| `VISITOR_RETENTION_DAYS` | 365 | Lifetime of the signed first-party browser correlation cookie |
| `ADMIN_ENABLED` | `false` | Enable the authenticated `/admin` panel |
| `SHARE_REGISTRY_ENABLED` | same as `ADMIN_ENABLED` | Collect Redis share-history/activity metadata; required when the admin panel is enabled |
| `ADMIN_USERNAME` | `admin` | Administration login username |
| `ADMIN_PASSWORD_HASH` | unset | Werkzeug password hash generated by `media-uploader-admin-hash` |
| `ADMIN_SESSION_TTL_SECONDS` | 3600 | Maximum administration login lifetime |
| `ADMIN_SHARE_HISTORY_DAYS` | 365 | Retain share registry records for this long after each share expires |
| `RATE_LIMIT_ADMIN_LOGIN` | 10 | Login attempts per rate-limit window and client IP |

Do not enable `PROXY_FIX_*` values unless the application is actually behind the corresponding number of trusted proxies. Do not expose Redis publicly.

## Audit logging

Application audit logging is enabled by default. Each event is emitted as one JSON object on the `media_uploader.audit` logger and persisted to `AUDIT_LOG_PATH` as JSON Lines with daily rotation. Events include a generated request ID and a stable, non-secret visitor fingerprint plus request context such as the effective client IP and source port when available, method, endpoint/route template, host, User-Agent, accepted language, status, and request duration where applicable. Domain events additionally record upload-session creation, completed files, share creation, share opens, preview/download access, denied share/file access, CSRF rejection, oversized requests, and rate limits.

`AUDIT_LOG_CONSOLE` is off by default. This keeps the Waitress/application terminal human-readable while retaining the complete structured trail in `AUDIT_LOG_PATH`. Set it to `true` only when your process supervisor or log collector intentionally consumes audit JSON from stderr. Low-value static and health requests do not create visitor records when their audit switches are disabled.

The browser visitor identifier is stored inside Flask's signed, HttpOnly session cookie and only its SHA-256 fingerprint is written to audit records. Share tokens and upload-session IDs are bearer/security-sensitive values, so the audit log never records them raw. It writes a stable SHA-256 fingerprint instead, which lets repeated accesses to the same share/session be correlated without turning the log itself into a source of usable share URLs. Filenames are logged because they are part of the upload/share activity metadata.

When `ProxyFix` is configured correctly, the recorded `client_ip` is the client address supplied by the trusted proxy chain. With all `PROXY_FIX_*` counts at zero, it is the direct peer address. Do not trust forwarded-address headers from arbitrary clients.

The production Traefik configuration also emits JSON access logs, but deliberately drops `RequestPath`, `RequestLine`, query parameters, and request headers so bearer share tokens are not copied into edge logs. Traefik writes those records to stdout, where the Compose `json-file` logging policy rotates them. The web container stores its rotating application audit log on the dedicated `audit-logs` volume. Application audit events provide the route-level/share-level correlation that the redacted edge log intentionally omits.

IP addresses and User-Agent strings can be personal data. If you operate this service for other people, document the logging in your privacy notice and choose retention/access controls appropriate to your jurisdiction and deployment. `AUDIT_LOG_STATIC` and `AUDIT_LOG_HEALTH` are off by default to avoid high-volume low-value events.

## Administration panel

The application can maintain a Redis-backed share registry independently of the bearer-token authorization keys. Registry collection is controlled by `SHARE_REGISTRY_ENABLED`; when the setting is omitted it defaults to `ADMIN_ENABLED`, so disabling the administration feature also disables this extra metadata collection by default. New registry records contain creation/expiry timestamps, creator visitor fingerprint, effective client IP, bounded User-Agent, file count/bytes, file metadata, open/preview/download counters, and last-access metadata. Active shares that existed before the registry was introduced are discovered when an administrator opens the panel; their creator metadata is shown as unknown because it cannot be reconstructed safely.

Enable the panel in `.env`:

```dotenv
ADMIN_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH='paste-generated-hash-here'
```

Generate the password hash interactively:

```bash
media-uploader-admin-hash
```

Keep the hash quoted in `.env` because Werkzeug hashes contain `$` characters. Restart the application, then browse to:

```text
/admin
```

The admin session is separate from the long-lived visitor identifier and expires after `ADMIN_SESSION_TTL_SECONDS`. Login attempts are Redis-rate-limited by client IP. Admin pages are marked `private, no-store`, require the normal CSRF protection for mutations, and are hidden with HTTP 404 when `ADMIN_ENABLED=false`.

The share list retains records for `ADMIN_SHARE_HISTORY_DAYS` after each share expires. The periodic cleanup worker also prunes the Redis sorted-set indexes, so registry indexes do not grow indefinitely when nobody opens `/admin`. Revoking an active share deletes its authorization key immediately and removes the stored share directory; the registry record remains so the action is visible in history. The panel shows pseudonymous visitor fingerprints and network/browser metadata, not verified human identities.

For Internet-facing deployments, protect `/admin` with HTTPS and consider an additional edge control such as a VPN, IP allowlist, or upstream identity provider.

## Storage lifecycle

Unfinished uploads are stored below:

```text
uploads/sessions/<session-id>/
```

Finalized shares are atomically moved to:

```text
uploads/shares/<token>/
```

Each directory contains an internal `.expires_at` marker. Before writing a chunk or creating the temporary assembled copy, the server also verifies that `MIN_FREE_DISK_BYTES` will remain available; `/healthz` verifies Redis connectivity, upload-storage writability, and the configured free-space margin. The free-space check is a safety margin rather than a cluster-wide byte reservation, so deployments with multiple application replicas should add storage-level capacity controls. Redis controls authorization; the marker controls eventual disk cleanup after a short grace period so disk reclamation cannot race ahead of Redis expiry. Run cleanup periodically with either:

```bash
flask --app media_uploader cleanup-uploads
```

or:

```bash
media-uploader-cleanup
```

The Docker Compose deployment runs the cleanup worker automatically.

## Docker + Traefik

The production Compose file is intended for a single-host HTTPS deployment with Traefik. It deliberately uses Traefik's **file provider** rather than the Docker provider, so the reverse proxy never receives access to `/var/run/docker.sock`.

Copy `.env.example` to `.env` and set at least:

```dotenv
FLASK_SECRET_KEY=<random-secret>
MEDIA_UPLOADER_DATA_DIR=/srv/media-uploader
UPLOAD_HOST=uploads.example.com
ACME_EMAIL=admin@example.com
```

Prepare the persistent directories as root:

```bash
sudo ./deploy/oci/prepare-host.sh /srv/media-uploader
```

Create a bcrypt htpasswd file for people who are allowed to create uploads. The edge authentication protects `/`, `/api/uploads/*`, and `/admin/*`; public `/s/*` share links, static assets, and `/healthz` do not require these uploader credentials. For example, on Debian/Ubuntu with `apache2-utils` installed:

```bash
sudo htpasswd -cB /srv/media-uploader/secrets/upload-users.htpasswd uploader
sudo chown root:65532 /srv/media-uploader/secrets/upload-users.htpasswd
sudo chmod 0640 /srv/media-uploader/secrets/upload-users.htpasswd
```

Render the Traefik routing file with the same hostname and request-size ceiling used by the application:

```bash
sudo ./deploy/traefik/render-dynamic-config.sh \
  uploads.example.com \
  7340032 \
  /srv/media-uploader/traefik/dynamic.yml
sudo chown root:65532 /srv/media-uploader/traefik/dynamic.yml
sudo chmod 0640 /srv/media-uploader/traefik/dynamic.yml
```

Point the hostname at the server, allow inbound TCP 80/443, and then run:

```bash
sudo docker compose config
sudo docker compose up -d --build
```

The production Compose configuration:

- redirects HTTP to HTTPS and obtains certificates with Let's Encrypt;
- protects upload creation and the admin path with edge bcrypt Basic Auth while leaving bearer share links public, and strips any cached `Authorization` header before proxying public routes;
- uses Traefik's file provider and does not mount the Docker socket;
- runs Traefik as a non-root uid on high container ports, with a read-only root filesystem, no Linux capabilities, and `no-new-privileges`;
- marks the Flask session cookie secure, validates the expected host, and trusts exactly one Traefik hop;
- enforces the upload request-size ceiling at Traefik as well as Flask;
- keeps Redis reachable only on an internal Docker network and persists it with AOF;
- bind-mounts uploads, Redis state, audit logs, and ACME state below `MEDIA_UPLOADER_DATA_DIR`, making it suitable for a dedicated block-volume mount;
- rotates container stdout/stderr logs with Docker's `json-file` policy;
- runs the application containers with a read-only root filesystem, dropped Linux capabilities, and `no-new-privileges`;
- health-checks Redis and the web process; and
- runs a dedicated expired-file cleanup worker.

Do not publish ports 5000 or 6379 on the host. Do not add your normal account to the `docker` group merely for convenience on an Internet-facing server; membership effectively grants root-equivalent control of the host. Use `sudo docker ...` unless you have a deliberate alternative.

For an OCI walkthrough, see `deploy/oci/README.md`. If you deploy behind a different proxy, adjust the `PROXY_FIX_*` counts to match the actual topology rather than copying the Compose values blindly.

## Supported media

The server preserves valid Unicode filenames exactly as supplied by the browser, while using random server-generated IDs for storage paths. SHA-256 is computed during assembly and shown on the share page; file responses also include `X-Content-SHA256`. Path separators, control characters, and bidirectional-control characters are rejected. The extension is normalized only for validation and correlated with `libmagic` MIME detection. The default mappings include PNG, JPEG, GIF, HEIC/HEIF, MP4, MOV, AVI, and WebM. Browser preview support is intentionally narrower than upload support because HEIC and AVI rendering is inconsistent across browsers.

The HTML `accept` filter is only a convenience. Server-side checks are authoritative.

## Development checks

```bash
ruff format --check .
ruff check .
mypy media_uploader tests tools
pytest --cov=media_uploader --cov-report=term-missing
pip-audit
node --check media_uploader/static/scripts/index.js
node --check media_uploader/static/scripts/summary.js
python -m pip install -c constraints.lock -e . --group e2e
python -m playwright install chromium
pytest e2e --browser chromium
```

The E2E suite starts the Flask application itself on a free localhost port. Redis must be reachable first (the default E2E database is `redis://127.0.0.1:6379/15`); set `E2E_REDIS_URL` to use another isolated Redis database. You do not need to run `media-uploader` separately before `pytest e2e`.

GitHub Actions runs the same checks and builds the Docker image on pushes and pull requests.

## Project layout

```text
media_uploader/
  blueprints/        Public, upload, share, and authenticated admin HTTP routes
  services/          Redis upload state and filesystem storage
  static/            CSS, JavaScript, favicons, vendored Bootstrap CSS
  templates/         Jinja templates
  __init__.py        application factory, security headers, CLI
  config.py          environment configuration
  cleanup_worker.py  periodic filesystem cleanup
  server.py          Waitress production entry point
  admin_cli.py       Interactive admin-password hash generator
tests/               unit/integration-style Flask tests
e2e/                 Playwright browser upload/share/download flow
constraints.lock     generated exact runtime dependency resolution
Dockerfile
docker-compose.yml
```

## Dependency locking

`pyproject.toml` remains the only hand-maintained dependency declaration. `constraints.lock` is a generated exact runtime resolution used by Docker and CI so release builds do not silently pick newer transitive packages. CI runs `python tools/check_runtime_lock.py` against the installed runtime graph and fails if a transitive dependency is missing from the lock, stale, or resolved to a different version. When intentionally updating dependencies, regenerate the lock from `pyproject.toml` with a resolver such as `uv pip compile pyproject.toml -o constraints.lock`, review the diff, run the full test/E2E suite, and commit both files together. Do not hand-add application dependencies only to the lock file.

## Operational notes

- Monitor both the upload filesystem and Redis memory/disk usage. `MIN_FREE_DISK_BYTES` is a last-resort guard, not a substitute for alerts/capacity planning.
- Back up or export the `audit-logs` and `traefik-logs` volumes if audit history must survive host loss; restrict access because they contain client metadata.
- Keep the cleanup worker running. Token expiry blocks access immediately, but disk reclamation is periodic.
- Rate limits are intentionally simple fixed-window limits backed by Redis. Internet-facing deployments may additionally enforce connection/body/rate limits at the reverse proxy or edge.
- A Redis loss makes existing share tokens unavailable. The Compose configuration persists Redis, while filesystem expiry markers still reclaim otherwise orphaned bytes.
- Error responses intentionally do not expose internal exception strings or filesystem paths; inspect server logs for the corresponding exception.

## License

MIT. See [LICENSE](LICENSE).
