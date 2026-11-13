variable "name" {
  type        = string
  default     = "swarmtenderr"
  description = "Container and persistent volume prefix."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,62}$", var.name))
    error_message = "Use a lowercase Docker resource name."
  }
}

variable "image" {
  type        = string
  description = "Published Swarmtenderr image pinned by sha256 digest."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Pin the tested image using repository@sha256:<64 lowercase hex digits>."
  }
}

variable "public_url" {
  type        = string
  default     = "http://localhost:8787"
  description = "Exact browser origin used for CSRF protection, with no trailing slash."
  validation {
    condition     = can(regex("^https?://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$", var.public_url))
    error_message = "Use a complete HTTP(S) origin without paths or credentials."
  }
}

variable "qbittorrent_url" {
  type        = string
  description = "qBittorrent 5 HTTP(S) origin reachable from the container."
  validation {
    condition     = can(regex("^https?://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$", var.qbittorrent_url))
    error_message = "Use an HTTP(S) origin without paths or credentials."
  }
}

variable "qbittorrent_username" {
  type        = string
  default     = "admin"
  description = "Non-secret qBittorrent login name."
}

variable "password_file" {
  type        = string
  description = "Absolute Docker-host path to the dashboard password; only the path enters state."
  validation {
    condition     = startswith(var.password_file, "/")
    error_message = "Supply an absolute host file path, never a password value."
  }
}

variable "qbittorrent_password_file" {
  type        = string
  description = "Absolute Docker-host credential file path; contents are never read by Terraform."
  validation {
    condition     = startswith(var.qbittorrent_password_file, "/")
    error_message = "Supply an absolute host file path."
  }
}

variable "postgres_url_file" {
  type        = string
  default     = ""
  description = "Optional absolute Docker-host file containing a PostgreSQL URL; empty selects SQLite."
  validation {
    condition     = var.postgres_url_file == "" || startswith(var.postgres_url_file, "/")
    error_message = "Use an absolute file path or an empty string for SQLite."
  }
}

variable "controls_enabled" {
  type        = bool
  default     = false
  description = "Enable manual queue controls. External optimizer mode always disables them."
}

variable "postgres_ca_file" {
  type        = string
  default     = ""
  description = "Optional public CA certificate path on the Docker host for verified private PostgreSQL TLS."
  validation {
    condition     = var.postgres_ca_file == "" || startswith(var.postgres_ca_file, "/")
    error_message = "Use an absolute public certificate file path."
  }
}

variable "bind_address" {
  type        = string
  default     = "127.0.0.1"
  description = "Published host IPv4 address; loopback by default for a local TLS proxy."
  validation {
    condition     = can(cidrhost("${var.bind_address}/32", 0))
    error_message = "Use a valid IPv4 address."
  }
}

variable "port" {
  type        = number
  default     = 8787
  description = "Host port for the web interface."
  validation {
    condition     = var.port >= 1024 && var.port <= 65535 && floor(var.port) == var.port
    error_message = "Choose a whole port from 1024 through 65535."
  }
}

variable "network_name" {
  type        = string
  default     = ""
  description = "Optional existing Docker network that reaches qBittorrent or PostgreSQL."
}
