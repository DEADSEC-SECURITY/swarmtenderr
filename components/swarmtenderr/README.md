# Swarmtenderr

An open source, self-hosted qBittorrent dashboard and queue manager. See what is
moving, understand what is waiting, and tend your swarm from one interface.

Version 0.1 includes live WebSocket torrent status, dark/light themes, search/filter/sort, honest transfer ETAs,
aggregate rate history, persistent action auditing, and opt-in pause, resume,
reannounce and queue promotion. It connects to Apollo's existing optimizer to
explain its scheduling decisions and storage budgets. Standalone installations
use qBittorrent's native queue; the portable automatic scheduling engine is not
part of this first release. No speed improvement is promised.

The runtime is Python 3.13 with Starlette, Uvicorn and WebSockets. There is no frontend build, CDN,
telemetry service or account requirement. SQLite is the default. The same image
also supports PostgreSQL, including an existing externally managed database.

## Try locally

From the repository root:

```sh
docker build -t elgon2003/swarmtenderr:0.1.0-local components/swarmtenderr
SWARMTENDERR_IMAGE=elgon2003/swarmtenderr:0.1.0-local \
  docker compose -f components/swarmtenderr/config/compose.demo.yaml up -d
```

Open <http://localhost:8787>. Demo mode is explicitly labeled, has synthetic
open-content torrents, and cannot control a client. The release pipeline targets
`elgon2003/swarmtenderr:0.1.0`; verify a successful release before pulling it.

## Connect qBittorrent

Requires qBittorrent 5. Keep WebUI authentication and CSRF protection enabled.
Create two runtime files outside the checkout: a dashboard password and the
existing qBittorrent password. Do not place their contents in a Compose file,
Terraform variable, CLI argument or image layer. Make them readable by container
UID 10001, while restricting other host users. Compose bind-backed secrets keep
host permissions; they do not automatically fix ownership.

Set the following non-secret deployment values, then start `config/compose.yaml`:

| Variable | Purpose |
| --- | --- |
| `SWARMTENDERR_IMAGE` | Published version; prefer its verified `@sha256:` reference |
| `SWARMTENDERR_PASSWORD_PATH` | Host dashboard credential file path |
| `QBITTORRENT_PASSWORD_PATH` | Host qBittorrent credential file path |
| `QBITTORRENT_URL` | Client origin reachable from the container |
| `QBITTORRENT_USERNAME` | Client username, default `admin` |
| `SWARMTENDERR_PUBLIC_URL` | Exact browser origin, default `http://localhost:8787` |

Log in to the dashboard as `admin` using its own password. Ports bind to loopback
by default. For LAN use, terminate TLS at your reverse proxy and set the exact
HTTPS browser origin. Basic authentication requires that HTTPS boundary outside
localhost. The browser never receives the qBittorrent password or PostgreSQL URL.
Same-origin WebSocket handshakes reuse the browser’s dashboard authentication.
The server pushes changed observations; the browser reconnects with exponential
backoff up to 30 seconds and does not poll on a timer. Client sampling defaults
to three seconds (`SWARMTENDERR_POLL_SECONDS`, minimum one second). Aggregate rate
samples are downsampled to 30 seconds for bounded database growth. Your reverse
proxy must preserve WebSocket upgrades and the original Origin header.

Manual controls default to disabled. Set `SWARMTENDERR_CONTROLS=true` in the
service environment to enable them. Completed, force-started, `apollo-keep` and
`swarmtenderr-keep` torrents are protected. Each request refreshes the target,
records a durable pending audit, and then submits exactly one allowlisted
mutation. Acceptance is distinct from observed completion. Timeouts are recorded
as uncertain and are never retried automatically. Deletion, force-start,
client preference changes and file-priority operations are not exposed.

## SQLite or PostgreSQL

SQLite uses `/data/swarmtenderr.sqlite3`, WAL and a five-second busy timeout. Keep
the persistent `/data` volume on a local filesystem. Run one app instance for a
given SQLite database. Back up using SQLite's online backup API or stop the app
and copy the whole database/WAL set; copying only a live main database is unsafe.

For PostgreSQL, create a dedicated database or schema and an application role
that owns it. Use PostgreSQL 14+; the acceptance profile runs PostgreSQL 18. Write
the full connection URL into a host secret file and set
`SWARMTENDERR_DATABASE_URL_PATH` to its path. Start Compose with both files:

```sh
docker compose -f components/swarmtenderr/config/compose.yaml \
  -f components/swarmtenderr/config/compose.postgres.yaml up -d
```

The app reads `SWARMTENDERR_DATABASE_URL_FILE` at startup and gives it precedence
over the SQLite default. Use `sslmode=verify-full` and the appropriate trust
configuration for remote databases. PostgreSQL is optional; it is bundled as a
driver, not a database service. Tables use the `swarmtenderr_` prefix. The v1
schema creates only its own tables and refuses unknown schema versions. Database
selection does not migrate existing observations: export/import or start fresh
explicitly. Use your normal `pg_dump`/restore process for PostgreSQL.

Observations retain the latest sanitized torrent snapshot and 30 days of
aggregate rate samples. The latest 60 samples are served for the pulse chart.
Action audit records persist until an operator archives the database. Titles and
hashes are private operational data; trackers, magnets, save paths and credentials
are not included in the dashboard projection. An upstream failure retains the
last snapshot and marks it stale, rather than inventing zero activity.

## External optimizer integration

Set `SWARMTENDERR_OPTIMIZER_STATE` to a read-only, atomically updated optimizer
snapshot with the Apollo `optimizer`, `space_plan`, and `optimizer_events`
fields. Mount its parent directory, so atomic file replacement remains visible.
The adapter allowlists public fields and marks observations older than two
minutes as stale. External-controller mode rejects controls at configuration and
request time, preventing a competing scheduler. It never writes that state or
imports another stack's private code. See the Apollo operating runbook for the
specific host mounts and ownership exception.

## Terraform

Use [the reusable Docker module](../../modules/swarmtenderr/README.md). It creates
the pinned container and a protected state volume, accepts file paths rather
than credentials, and returns `swarmtenderr_contract_v1`. It supports an existing
Docker network, TLS proxy origin, and optional external PostgreSQL URL file.
It does not create or take ownership of an existing global database.

## CI and releases

[The workflow](../../.github/workflows/swarmtenderr.yml) checks JavaScript/API
syntax, builds the actual image, runs its complete unit/HTTP suite outside the
source tree, exercises SQLite and real PostgreSQL including HTTP startup, and
runs Terraform validation and mocked plans. Pull requests receive no publishing
credentials. A matching `swarmtenderr-v0.1.0` tag publishes amd64/arm64 images to
Docker Hub only after verification. Both architectures are built; release CI
must also exercise both before publication. Actions are pinned to verified
commit revisions. Runtime dependencies have exact versions and release hashes.
BuildKit generates provenance and an SBOM; the release summary records the digest.

The publisher requires a repository Actions secret named `DOCKERHUB_TOKEN` for
`elgon2003`, scoped to read/write the Swarmtenderr image. Never commit the token.
GitHub owns release publishing. Terraform consumes the resulting digest; image
publication does not automatically apply infrastructure. Keep the previous
digest for rollback and never delete its data volume.

## Verification

```sh
PYTHONPATH=components/swarmtenderr/src python -B -m unittest discover -s components/swarmtenderr/tests -v
docker build -t swarmtenderr-ci components/swarmtenderr
python -B components/swarmtenderr/tests/test_container_acceptance.py swarmtenderr-ci
terraform -chdir=modules/swarmtenderr init -backend=false
terraform -chdir=modules/swarmtenderr validate
terraform -chdir=modules/swarmtenderr test
```

The container profile creates and removes uniquely named disposable resources;
it never mounts real client state. PostgreSQL acceptance is explicit and fails
without its test connection URL; it is not a silently skipped unit test. Local
mocked-client success does not establish live-server acceptance. `/healthz` is
process liveness; `/api/v1/status` reports upstream freshness separately. See
[OpenAPI](config/openapi.json) for the API contract and
[qBittorrent's official API](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0))
for the upstream interface.

## Name check and license

On November 12, 2026, exact-name GitHub repository, Docker Hub, npm and PyPI
checks found no Swarmtenderr project/package; general web searches found no
competing app. Optimizerr and Swarmarr had existing projects and were rejected.
This records a bounded discoverability check, not a trademark clearance.

MIT licensed; see [LICENSE](LICENSE).
