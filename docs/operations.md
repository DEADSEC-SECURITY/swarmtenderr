# Operations and recovery

## Backup

Back up the database **and** the runtime key. UI integration credentials cannot be
recovered without `/data/secret.key`. Also protect deployment-owned bootstrap,
client/provider and external-database secret files; file references are not copied
into the application database.

For a portable, consistent SQLite or PostgreSQL backup:

```sh
docker compose -f components/swarmtenderr/config/compose.yaml run --rm --no-deps \
  --entrypoint apollo-swarmtenderr-maintenance swarmtenderr \
  backup --file /data/backup.json
```

The command refuses to overwrite an existing file and writes mode 0600. The backup
contains password hashes, encrypted settings **and their encryption key**. Move it
to encrypted, access-controlled off-host storage and remove the working copy through
your normal backup retention process. It is not a public support bundle. The portable
format is bounded to 256 MiB on restore; for larger databases use native backup and
preserve the key separately. PostgreSQL's normal `pg_dump`/restore is supported;
use a consistent database backup and keep secret/key files with its recovery set.
Do not copy a live SQLite main file without its WAL or the SQLite backup API.

## Restore and SQLite → PostgreSQL transfer

Stop every application instance. Restore into a **fresh data directory and empty
destination database** using the same candidate/release schema. Supply the target
PostgreSQL URL through its runtime secret file when changing backend. Mount the
private backup read-only, then run:

```sh
apollo-swarmtenderr-maintenance restore --file /backup/backup.json
```

Use the image's maintenance entrypoint with the destination `/data` volume and the
same database environment/file configuration as the eventual app container. The
portable format is database neutral, so this is also the explicit SQLite-to-
PostgreSQL migration. Restore preserves accounts, hashes, encrypted settings,
completion identities/totals and audit history. It revokes old sessions, drops
controller ownership/pending requests, records a restore audit and starts in Observe.
Never restore over an active installation or reuse an old controller lease.

Start the app, sign in with an existing local account, verify totals/timezone and
test client/identity connections before considering enforcement. The runtime key
is restored with private ownership; run maintenance as the same UID as the app.

## Local account recovery

The initial bootstrap file creates the first local administrator only when the
identity table is empty. Editing that file later does not reset an account.
For on-host recovery, run the normal image as its app UID with the existing volume,
database configuration and a newly prepared private password file:

```sh
apollo-swarmtenderr --reset-password-for admin --password-file /run/secrets/recovery-password
```

This replaces the local password hash and revokes its sessions; no password is
printed. The final active local administrator cannot be disabled/demoted through
the UI. SSO credentials are managed by the identity provider.

## Upgrade and rollback

Record the running image digest and back up first. Pull the accepted candidate
by digest, review release notes/schema compatibility and recreate the container
with the existing data/key volume. v1→v2 is transactional; unknown future schemas
are rejected. Database selection does not implicitly migrate data.

A rollback across a schema boundary requires the pre-upgrade database/key backup
and previous image together, restored into separate state after stopping the app.
Do not run a v1 image against a v2 database. Keep the failed candidate state for
inspection under the same private-data controls, and verify restored totals before
resuming operations. Never automatically downgrade or discard a database.

## Health and diagnosis

`/healthz` proves the process answers HTTP; `/readyz` additionally checks SQL access.
A first boot without client configuration remains available for login/settings.
Client outages retain the last observation with a visible stale/error state. The
Settings connection test distinguishes save success from upstream connectivity.
Database/file exceptions are sanitized. Keep proxy logs from recording OIDC callback
query strings and never publish private titles/hashes in support reports.
