# Swarmtenderr Docker module

Terraform owns the container, protected persistent volume, networking, public
configuration defaults. Credentials stay in runtime host files;
the module never reads their values into plans, state, outputs or image layers.

```hcl
module "swarmtenderr" {
  source = "git::https://github.com/DEADSEC-SECURITY/swarmtenderr.git//modules/swarmtenderr?ref=YOUR_REVIEWED_COMMIT"

  image         = "elgon2003/swarmtenderr@sha256:YOUR_ACCEPTED_DIGEST"
  public_url    = "https://swarmtenderr.example.internal"
  password_file = "/run/operator-secrets/swarmtenderr-admin-password"
  network_name  = "media"

  defaults = {
    timezone           = "America/New_York"
    policy_mode        = "observe"
    managed_categories = ["sonarr", "radarr"]
    sso_enabled        = true
    sso_issuer         = "https://identity.example.internal/application/o/swarmtenderr/"
    sso_client_id      = "PUBLIC_CLIENT_ID"
    sso_allowed_groups = ["media"]
  }
  oidc_secret_file = "/run/operator-secrets/swarmtenderr-oidc-secret"
  identity_ca_file = "/etc/ssl/certs/lab-ca-bundle.pem"
}
```

Replace examples and pin source commit/image digest. The bootstrap password file
must contain 12–256 characters. Bind-backed secret files must be readable by UID
10001 and protected from other host users. Terraform cannot create their contents.

Client settings are optional initial defaults; use the UI if preferred. PostgreSQL
uses `postgres_url_file` and optional `postgres_ca_file` references. Provision its
database/schema separately and use verified TLS remotely. SQLite remains the
default. Both backends require the persistent `/data` encryption key.

Saved UI settings override deployment defaults. Legacy `locked_settings` and
`controls_enabled` inputs no longer gate administrator edits or manual controls. Never put a secret into `defaults`.
`observer_feed_file` mounts a sanitized feed and forces observation.
`library_probe_path` optionally mounts an import filesystem read-only for actual
capacity probing; it grants no media write access.

The port binds to loopback by default. Use a local TLS proxy or explicitly select
a trusted binding. The volume has `prevent_destroy`; replacing data needs a backup
and migration decision. `swarmtenderr_contract_v1` retains public endpoint/image/
database metadata. Run `terraform fmt -check -recursive`, `init -backend=false`,
`validate` and `test` (mocked Docker; no infrastructure apply).
