# Swarmtenderr Docker deployment

Reusable Terraform module for a pre-existing Docker host. Configure
`kreuzwerker/docker ~> 4.7` in the caller, locally or through its supported SSH
transport. The module creates one container and a `prevent_destroy` state volume.
It never reads, generates, outputs or provisions credential values.

```hcl
module "swarmtenderr" {
  source = "./modules/swarmtenderr"

  image                     = "elgon2003/swarmtenderr@sha256:<verified-release-digest>"
  public_url                = "https://swarmtenderr.example.internal"
  qbittorrent_url           = "http://qbittorrent:8080"
  network_name              = "media"
  password_file             = "/etc/swarmtenderr/dashboard-password"
  qbittorrent_password_file = "/etc/swarmtenderr/qbittorrent-password"
  # Optional: omit for SQLite.
  # postgres_url_file = "/etc/swarmtenderr/postgresql-url"
}
```

Replace the digest placeholder with the exact successful release output. Mutable
tags are deliberately rejected. Pre-create the credential files on the Docker
host with access for container UID 10001, and configure PostgreSQL independently
when selected. A file path is not a secret; file contents never enter Terraform
state. Do not replace these inputs with `file()` calls or inline credentials.

The published port defaults to `127.0.0.1:8787`. Put your normal TLS/auth boundary
in front for LAN access. `public_url` must be the exact browser origin.
`controls_enabled` defaults to false. The optional existing network should reach
qBittorrent and any external database. This module does not deploy a reverse
proxy, DNS, qBittorrent or PostgreSQL, or connect Apollo's private optimizer state.
Apollo uses its own existing Compose, DNS and ingress owners for that integration.

`swarmtenderr_contract_v1` contains the version, URL, container, exact image,
database kind and volume name. No credential values or PostgreSQL URLs are output.

Run `terraform fmt -recursive`, `terraform init -backend=false`,
`terraform validate`, and `terraform test`. All native tests explicitly plan
against a mocked Docker provider. Review a real target plan before apply.
Rollback to a prior tested digest. Back up the database first and preserve the
volume; changing database backends does not migrate existing data automatically.
