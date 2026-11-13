output "swarmtenderr_contract_v1" {
  description = "Non-secret installation coordinates and immutable image identity."
  value = {
    version      = 1
    container    = docker_container.app.name
    image        = var.image
    url          = var.public_url
    database     = var.postgres_url_file == "" ? "sqlite" : "postgresql"
    state_volume = docker_volume.state.name
  }
}
