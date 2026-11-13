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
    "QBITTORRENT_PASSWORD_FILE=/run/secrets/qbittorrent-password",
    "SWARMTENDERR_PASSWORD_FILE=/run/secrets/dashboard-password",
    "SWARMTENDERR_CONTROLS=${var.controls_enabled}",
  ], var.postgres_url_file != "" ? ["SWARMTENDERR_DATABASE_URL_FILE=/run/secrets/database-url"] : [])

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
  volumes {
    host_path      = var.qbittorrent_password_file
    container_path = "/run/secrets/qbittorrent-password"
    read_only      = true
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
  tmpfs = { "/tmp" = "rw,nosuid,nodev,size=64m,mode=1777" }
}
