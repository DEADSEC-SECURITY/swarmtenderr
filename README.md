# Swarmtenderr

A self-hosted qBittorrent monitor and queue optimizer with live updates, completion
statistics, local accounts, Authentik-compatible OIDC, and editable settings.
SQLite is the default; an existing PostgreSQL database is supported by the same
image. One installation manages one client and one set of users.

The 0.2 completion work is a release candidate. A candidate passing unit tests is
not a production release. See the acceptance record before selecting a published
image. Source lives here; Apollo owns its deployment and consumes pinned releases.

## Install

Use Docker Compose 2 and qBittorrent 5. Create a private file outside the checkout
containing the initial administrator password (12–256 characters). Make it readable
by container UID 10001 with mode 0400 or 0600. Do not put passwords into Compose,
Terraform, shell arguments, source or image layers.

```sh
export SWARMTENDERR_PASSWORD_PATH=/absolute/private/admin-password
export SWARMTENDERR_IMAGE=elgon2003/swarmtenderr@sha256:YOUR_ACCEPTED_DIGEST
docker compose -f components/swarmtenderr/config/compose.yaml up -d
```

Open <http://localhost:8787>, sign in as `admin`, and configure the client in
Settings → qBittorrent. Saving and testing a connection are separate operations;
failed credentials never produce a successful connection result. Use a TLS reverse
proxy outside localhost and set `SWARMTENDERR_PUBLIC_URL` to the exact browser
origin. Preserve WebSocket upgrades and the Origin header.

Optional Compose overlays configure [a client secret file](components/swarmtenderr/config/compose.client-secret.yaml),
[PostgreSQL](components/swarmtenderr/config/compose.postgres.yaml),
[OIDC secret file](components/swarmtenderr/config/compose.oidc.yaml), and
[private TLS trust](components/swarmtenderr/config/compose.trust.yaml).
Each secret file stays on the Docker host. Preserve its UID 10001 readability;
Compose bind-backed secrets retain host permissions.

## Use

- **Overview:** current rates, active/queued/stalled/seeding counts, completions
  today/this month, completed payload, and daily/monthly history.
- **Torrents:** search, filters, sorting, pagination and transfer details. Permitted
  pause/resume/prioritize/reannounce actions are audited before submission.
- **Completion history:** durable events, date/name/category filters and CSV export.
  CSV exports are bounded to the first 10,000 matching records and escape formulas.
- **Activity:** requested, accepted, confirmed, rejected and uncertain outcomes;
  administrators also see account/configuration audit records.
- **Settings:** live editable general, client, optimization and SSO configuration.
  Deployment defaults, UI overrides, locks and resets have explicit ownership.
- **Users:** administrators create accounts, assign roles, deactivate users and reset
  local passwords. Users change their own password and sign out through the account menu.

Viewers inspect; operators also use enabled torrent controls; administrators manage
configuration and accounts. One active local administrator is required for recovery.
Role changes, deactivation and password changes revoke affected sessions and live
streams. Production never inherits the demo credentials.

## Optimizer

Begin in Observe mode, choose managed categories, and inspect recommendations.
Enforce mode owns native priority and the active-download ceiling only. Native
queueing must be enabled and slow torrents counted in qBittorrent. Storage
reservations pay for a complete batch, external downloads and safety buffers.
Measured throughput, completion progress, age, bounded discovery samples and retry
cooldowns influence ranking. Seeder estimates are labeled as estimates.

Manual stops, force-started transfers, excluded tags/categories, completed data and
seeding are protected. The optimizer does not delete files, force-start transfers,
change file priorities or take over repair/removal. Upload settings stay with
qBittorrent. A database lease prevents two instances of this installation from
controlling the same queue. Do not point independent installations/databases at the
same client in Enforce mode; ownership cannot be coordinated across unrelated databases.

If an upstream request has an uncertain result, inspect the native queue and return
to Observe before explicitly enabling enforcement again. Observe restores only a
native ceiling whose current value proves this installation still owns it.
An external observer feed forces observation and disables all client mutations.

## Configuration and data

Public defaults come from `settings.defaults.json` or `SWARMTENDERR_DEFAULTS_JSON`.
Saved UI values override unlocked fields. `SWARMTENDERR_LOCKED_SETTINGS` is a comma
separated list of fields owned by deployment; locked deployment values also take
precedence over older saved overrides. Integration secrets are write-only and
stored encrypted using `/data/secret.key`, separate from the database.

SQLite lives in `/data/swarmtenderr.sqlite3` on a persistent local volume. PostgreSQL
uses `SWARMTENDERR_DATABASE_URL_FILE`; provision a dedicated database/schema and
owning role. For a remote server, use `sslmode=verify-full` and a public CA bundle.
Tables use the `swarmtenderr_` prefix. Transactional v1→v2 migration preserves old
observations/actions and rejects unknown future schema versions.

Completion records survive torrent removal and rate-sample retention. Counts use
native completion times in the selected IANA timezone. Initial connection backfills
surviving client records; older removed torrents cannot be reconstructed. Rechecks
and repeated polls do not count again. Re-added existing files require observed new
download bytes to count as another completion. A first-seen new torrent can use its
native added/completed times and downloaded-byte evidence. Very short repeated
downloads missed entirely between observations cannot be proven and are not counted.
Changing the client origin starts a separate stable client identity; avoid changing
an alias for the same client if you want one historical identity.

Rate samples are downsampled to 30 seconds with configurable retention. Completion
and audit records are durable. Trackers, magnets, save paths, integration passwords
and database URLs never appear in the dashboard data projection.

## Recovery, SSO, Terraform and releases

See [operations](docs/operations.md) for backups, restore, database transfer,
upgrade and rollback; [OIDC/Authentik](docs/oidc.md) for identity setup;
[the Terraform module](modules/swarmtenderr/README.md) for deployment ownership;
and [release engineering](docs/releases.md) for the build-once candidate and
exact-digest promotion pipeline. The [OpenAPI contract](components/swarmtenderr/config/openapi.json)
and [permissions contract](components/swarmtenderr/config/contracts.md) describe the API.

## Develop and test

```sh
docker build -t swarmtenderr:local components/swarmtenderr
python -B components/swarmtenderr/tests/test_container_acceptance.py swarmtenderr:local
SWARMTENDERR_IMAGE=swarmtenderr:local docker compose -f components/swarmtenderr/config/compose.demo.yaml up -d
```

The required acceptance profile runs the installed artifact's identity, SSO failure,
settings, history, migration, policy and recovery tests on both databases, then
checks real HTTP/WebSocket behavior and persistent restarts. The explicitly labeled
demo uses `demo / demo`, synthetic open-content torrents, working general/account
flows and disabled real-client/SSO mutations. It uses its own volume.

Licensed under MIT. No telemetry service or external account is required.
