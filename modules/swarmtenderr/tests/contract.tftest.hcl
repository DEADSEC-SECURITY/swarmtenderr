mock_provider "docker" {}

variables {
  image                     = "elgon2003/swarmtenderr@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  qbittorrent_url           = "http://qbittorrent:8080"
  password_file             = "/run/operator-secrets/dashboard-password"
  qbittorrent_password_file = "/run/operator-secrets/qbittorrent-password"
}

run "sqlite_default" {
  command = plan
  assert {
    condition     = output.swarmtenderr_contract_v1.database == "sqlite"
    error_message = "SQLite must be the default."
  }
  assert {
    condition     = contains(docker_container.app.env, "SWARMTENDERR_CONTROLS=false")
    error_message = "Controls must be opt-in."
  }
}

run "external_postgresql" {
  command = plan
  variables {
    postgres_url_file = "/run/operator-secrets/postgresql-url"
    postgres_ca_file  = "/etc/ssl/certs/lab-public-root.pem"
  }
  assert {
    condition     = contains(docker_container.app.env, "SWARMTENDERR_DATABASE_URL_FILE=/run/secrets/database-url")
    error_message = "PostgreSQL must be configured through a file reference."
  }
  assert {
    condition     = contains(docker_container.app.env, "PGSSLMODE=verify-full")
    error_message = "A private PostgreSQL CA must enable full TLS verification."
  }
}

run "reject_mutable_images" {
  command = plan
  variables {
    image = "elgon2003/swarmtenderr:latest"
  }
  expect_failures = [var.image]
}
