resource "docker_image" "app" {
  name         = var.image
  keep_locally = true
}

resource "docker_volume" "state" {
  name = "${var.name}-state"
  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_container" "app" {
  name          = var.name
  image         = docker_image.app.image_id
  restart       = "unless-stopped"
  read_only     = true
  must_run      = true
  memory        = 256
  cpu_shares    = 256
  security_opts = ["no-new-privileges:true"]
  env = concat([
    "SWARMTENDERR_PUBLIC_URL=${var.public_url}",
    "QBITTORRENT_URL=${var.qbittorrent_url}",
    "QBITTORRENT_USERNAME=${var.qbittorrent_username}",
    "SWARMTENDERR_DEFAULTS_JSON=${jsonencode(var.defaults)}",
    "SWARMTENDERR_LOCKED_SETTINGS=",
    "SWARMTENDERR_PASSWORD_FILE=/run/secrets/dashboard-password",
    "SWARMTENDERR_CONTROLS=true",
    ], var.postgres_url_file != "" ? ["SWARMTENDERR_DATABASE_URL_FILE=/run/secrets/database-url"] : [],
    var.postgres_ca_file != "" ? ["PGSSLROOTCERT=/run/certs/postgres-ca.pem", "PGSSLMODE=verify-full"] : [],
    var.qbittorrent_password_file != "" ? ["QBITTORRENT_PASSWORD_FILE=/run/secrets/qbittorrent-password"] : [],
    var.oidc_secret_file != "" ? ["SWARMTENDERR_OIDC_SECRET_FILE=/run/secrets/oidc-client-secret"] : [],
    var.identity_ca_file != "" ? ["SSL_CERT_FILE=/run/certs/identity-ca.pem"] : [],
    var.optimizer_control_directory != "" ? ["SWARMTENDERR_OPTIMIZER_CONTROL_SOCKET=/run/optimizer/optimizer.sock"] : [],
    var.observer_feed_file != "" ? ["SWARMTENDERR_OPTIMIZER_STATE=/observer/feed.json"] : [],
  var.library_probe_path != "" ? ["SWARMTENDERR_LIBRARY_PROBE_PATH=/probes/library"] : [])

  capabilities {
    drop = ["ALL"]
  }
  ports {
    internal = 8787
    external = var.port
    ip       = var.bind_address
  }
  volumes {
    volume_name    = docker_volume.state.name
    container_path = "/data"
  }
  volumes {
    host_path      = var.password_file
    container_path = "/run/secrets/dashboard-password"
    read_only      = true
  }
  dynamic "volumes" {
    for_each = { for name, source in {
      "qbittorrent-password" = var.qbittorrent_password_file,
      "oidc-client-secret"   = var.oidc_secret_file
    } : name => source if source != "" }
    content {
      host_path      = volumes.value
      container_path = "/run/secrets/${volumes.key}"
      read_only      = true
    }
  }
  dynamic "volumes" {
    for_each = { for destination, source in {
      "/run/certs/identity-ca.pem" = var.identity_ca_file,
      "/observer/feed.json"        = var.observer_feed_file,
      "/run/optimizer"             = var.optimizer_control_directory,
      "/probes/library"            = var.library_probe_path
    } : destination => source if source != "" }
    content {
      host_path      = volumes.value
      container_path = volumes.key
      read_only      = true
    }
  }
  dynamic "volumes" {
    for_each = var.postgres_url_file != "" ? [var.postgres_url_file] : []
    content {
      host_path      = volumes.value
      container_path = "/run/secrets/database-url"
      read_only      = true
    }
  }
  dynamic "networks_advanced" {
    for_each = var.network_name != "" ? [var.network_name] : []
    content {
      name = networks_advanced.value
    }
  }
  dynamic "volumes" {
    for_each = var.postgres_ca_file != "" ? [var.postgres_ca_file] : []
    content {
      host_path      = volumes.value
      container_path = "/run/certs/postgres-ca.pem"
      read_only      = true
    }
  }
  tmpfs = { "/tmp" = "rw,nosuid,nodev,size=64m,mode=1777" }
}
