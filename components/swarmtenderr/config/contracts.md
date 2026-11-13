# Product contracts for 0.2

Public static assets and login options expose no client data. All data APIs and
WebSocket streams require a revocable application session. Local passwords are
salted PBKDF2 hashes; session tokens are random and stored only by digest. The
session cookie is HttpOnly, SameSite=Lax and Secure for HTTPS deployments.
Unsafe authenticated requests require the exact configured Origin and a
session-bound CSRF header. WebSocket connections recheck session validity.

Viewer: status, statistics, history/export, own account and password.
Operator: viewer plus permitted single-torrent controls.
Admin: operator plus users, connection/settings/SSO and recovery administration.
The final active administrator cannot be disabled or demoted. Keep one active local administrator for recovery.

Routes: GET /api/v1/auth/options; POST /api/v1/auth/login; GET /api/v1/me;
POST /api/v1/auth/logout; POST /api/v1/account/password; GET/POST /api/v1/users;
PATCH /api/v1/users/{id}; POST /api/v1/users/{id}/password;
GET/PATCH /api/v1/settings; POST /api/v1/settings/reset;
POST /api/v1/settings/test-connection; GET /api/v1/statistics;
GET /api/v1/completions; GET /api/v1/completions.csv; GET /api/v1/activity;
GET /api/v1/status; POST /api/v1/torrents/{hash}/actions; WS /api/v1/live.
OIDC login/callback are /auth/oidc/login and /auth/oidc/callback.

Deployment supplies defaults; saved UI configuration overrides unlocked keys.
An explicit deployment lock list prevents overrides. Updates include a revision;
stale writes fail with 409. Secret fields are write-only and encrypted using an
application-owned key outside the database. Empty secret inputs preserve saved
values; reset explicitly returns keys to deployment defaults.

Completion identity includes the stable client, hash and native addition time.
Repeated observations and rechecks of that identity do not count twice. A new
addition with observed transferred bytes can establish a distinct completion.
A first-seen new hash can also use native added/completed timestamps and downloaded
bytes; an existing-file re-addition needs an observed increase during that addition.
Client origins have separate stable identities; surviving history is backfilled per client.
Native completion timestamps determine dates in the configured IANA timezone.
Surviving historical records are marked as backfill, collection coverage is
explicit, and removed records cannot be reconstructed. Completion events persist
independently of aggregate-rate retention.

Torrent mutations are recorded before submission and expose accepted, confirmed,
rejected or uncertain outcomes. Unknown upstream results are never retried
automatically. Demo and external-controller modes cannot mutate real torrents.

OIDC uses discovery, authorization code, S256 PKCE, state and nonce validation.
Identities bind by issuer and subject, never by an email-only match. New SSO
accounts receive the configured viewer/operator role, never automatic admin.
Local administrator recovery remains available.
