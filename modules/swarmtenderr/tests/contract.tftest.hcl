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

run "ui_connection_setup" {
  command = plan
  variables {
    qbittorrent_url           = ""
    qbittorrent_password_file = ""
  }
  assert {
    condition     = !contains(docker_container.app.env, "QBITTORRENT_PASSWORD_FILE=/run/secrets/qbittorrent-password")
    error_message = "An app configured through the UI must not require a client password bind."
  }
}

run "oidc_defaults_locks_and_narrow_feed" {
  command = plan
  variables {
    defaults = {
      timezone           = "America/New_York"
      sso_enabled        = true
      sso_issuer         = "https://identity.example/application/o/swarmtenderr/"
      sso_client_id      = "public-client-id"
      sso_allowed_groups = ["media"]
    }
    locked_settings    = ["sso_issuer", "sso_client_id"]
    oidc_secret_file   = "/run/operator-secrets/oidc-secret"
    observer_feed_file = "/srv/observer/feed.json"
    identity_ca_file   = "/etc/ssl/certs/lab-ca-bundle.pem"
  }
  assert {
    condition     = contains(docker_container.app.env, "SWARMTENDERR_OIDC_SECRET_FILE=/run/secrets/oidc-client-secret")
    error_message = "SSO secrets must be referenced through a runtime file."
  }
  assert {
    condition     = contains(docker_container.app.env, "SWARMTENDERR_OPTIMIZER_STATE=/observer/feed.json")
    error_message = "External mode must consume the narrow feed."
  }
  assert {
    condition     = contains(docker_container.app.env, "SWARMTENDERR_LOCKED_SETTINGS=sso_issuer,sso_client_id")
    error_message = "Deployment locks must reach the app."
  }
}

run "reject_automatic_sso_admin" {
  command = plan
  variables {
    defaults = { sso_default_role = "admin" }
  }
  expect_failures = [var.defaults]
}

run "reject_unscoped_enforcement" {
  command = plan
  variables {
    defaults = { policy_mode = "enforce" }
  }
  expect_failures = [var.defaults]
}

run "reject_unknown_setting_locks" {
  command = plan
  variables {
    locked_settings = ["fictional_setting"]
  }
  expect_failures = [var.locked_settings]
}
