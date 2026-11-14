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
  default     = ""
  description = "Optional initial qBittorrent origin; empty allows setup through Settings."
  validation {
    condition     = var.qbittorrent_url == "" || can(regex("^https?://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$", var.qbittorrent_url))
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
  default     = ""
  description = "Absolute Docker-host credential file path; contents are never read by Terraform."
  validation {
    condition     = var.qbittorrent_password_file == "" || startswith(var.qbittorrent_password_file, "/")
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

variable "defaults" {
  description = "Public deployment defaults. Saved unlocked UI values take precedence. No secret values are accepted."
  type = object({
    timezone                    = optional(string, "UTC")
    poll_seconds                = optional(number, 3)
    retention_days              = optional(number, 30)
    policy_mode                 = optional(string, "observe")
    managed_categories          = optional(list(string), [])
    excluded_tags               = optional(list(string), ["swarmtenderr-keep"])
    download_slots              = optional(number, 3)
    minimum_free_gib            = optional(number, 50)
    work_budget_gib             = optional(number, 500)
    library_reserve_gib         = optional(number, 50)
    ranking_interval_seconds    = optional(number, 300)
    probe_seconds               = optional(number, 2100)
    retry_seconds               = optional(number, 7200)
    rate_fresh_seconds          = optional(number, 900)
    seed_trial_seconds          = optional(number, 600)
    seed_trial_retry_seconds    = optional(number, 7200)
    min_trial_seeds             = optional(number, 5)
    min_proven_speed_kib        = optional(number, 32)
    swarm_trial_seconds         = optional(number, 180)
    swarm_warmup_seconds        = optional(number, 120)
    swarm_min_sample_seconds    = optional(number, 60)
    swarm_retry_seconds         = optional(number, 7200)
    swarm_slow_speed_kib        = optional(number, 256)
    metadata_trial_seconds      = optional(number, 180)
    metadata_retry_seconds      = optional(number, 7200)
    minimum_rate_sample_seconds = optional(number, 120)
    trial_improvement_percent   = optional(number, 20)
    unknown_size_gib            = optional(number, 2)
    other_buffer_gib            = optional(number, 5)
    sso_enabled                 = optional(bool, false)
    sso_issuer                  = optional(string, "")
    sso_client_id               = optional(string, "")
    sso_label                   = optional(string, "Sign in with SSO")
    sso_allowed_groups          = optional(list(string), [])
    sso_default_role            = optional(string, "viewer")
  })
  default = {}
  validation {
    condition = alltrue([
      contains(["observe", "enforce"], var.defaults.policy_mode),
      var.defaults.policy_mode != "enforce" || length(var.defaults.managed_categories) > 0,
      contains(["viewer", "operator"], var.defaults.sso_default_role),
      var.defaults.poll_seconds >= 1 && var.defaults.poll_seconds <= 3600 && floor(var.defaults.poll_seconds) == var.defaults.poll_seconds,
      var.defaults.retention_days >= 1 && var.defaults.retention_days <= 365 && floor(var.defaults.retention_days) == var.defaults.retention_days,
      var.defaults.download_slots >= 1 && var.defaults.download_slots <= 100 && floor(var.defaults.download_slots) == var.defaults.download_slots,
      alltrue([for value in [var.defaults.minimum_free_gib, var.defaults.work_budget_gib, var.defaults.library_reserve_gib] : value >= 1 && value <= 100000 && floor(value) == value]),
      var.defaults.sso_issuer == "" || can(regex("^https://[^/?#@]+/[^?#]*$", "${var.defaults.sso_issuer}/")),
      !var.defaults.sso_enabled || (var.defaults.sso_issuer != "" && var.defaults.sso_client_id != ""),
      alltrue([for value in concat(var.defaults.managed_categories, var.defaults.excluded_tags, var.defaults.sso_allowed_groups) : length(value) > 0 && length(value) <= 80]),
      alltrue([for values in [var.defaults.managed_categories, var.defaults.excluded_tags, var.defaults.sso_allowed_groups] : length(values) <= 100]),
      length(var.defaults.timezone) > 0 && length(var.defaults.timezone) <= 100,
      var.defaults.ranking_interval_seconds >= 30 && var.defaults.ranking_interval_seconds <= 3600 && floor(var.defaults.ranking_interval_seconds) == var.defaults.ranking_interval_seconds,
      var.defaults.probe_seconds >= 300 && var.defaults.probe_seconds <= 7200 && floor(var.defaults.probe_seconds) == var.defaults.probe_seconds,
      var.defaults.retry_seconds >= 300 && var.defaults.retry_seconds <= 86400 && floor(var.defaults.retry_seconds) == var.defaults.retry_seconds,
      var.defaults.rate_fresh_seconds >= 60 && var.defaults.rate_fresh_seconds <= 86400 && floor(var.defaults.rate_fresh_seconds) == var.defaults.rate_fresh_seconds,
      var.defaults.seed_trial_seconds >= 60 && var.defaults.seed_trial_seconds <= 1800 && floor(var.defaults.seed_trial_seconds) == var.defaults.seed_trial_seconds,
      var.defaults.seed_trial_retry_seconds >= 300 && var.defaults.seed_trial_retry_seconds <= 86400 && floor(var.defaults.seed_trial_retry_seconds) == var.defaults.seed_trial_retry_seconds,
      var.defaults.min_trial_seeds >= 1 && var.defaults.min_trial_seeds <= 10000 && floor(var.defaults.min_trial_seeds) == var.defaults.min_trial_seeds,
      var.defaults.min_proven_speed_kib >= 1 && var.defaults.min_proven_speed_kib <= 10240 && floor(var.defaults.min_proven_speed_kib) == var.defaults.min_proven_speed_kib,
      var.defaults.swarm_trial_seconds >= 60 && var.defaults.swarm_trial_seconds <= 1800 && floor(var.defaults.swarm_trial_seconds) == var.defaults.swarm_trial_seconds,
      var.defaults.swarm_warmup_seconds >= 30 && var.defaults.swarm_warmup_seconds <= 1800 && floor(var.defaults.swarm_warmup_seconds) == var.defaults.swarm_warmup_seconds,
      var.defaults.swarm_min_sample_seconds >= 15 && var.defaults.swarm_min_sample_seconds <= 600 && floor(var.defaults.swarm_min_sample_seconds) == var.defaults.swarm_min_sample_seconds,
      var.defaults.swarm_retry_seconds >= 300 && var.defaults.swarm_retry_seconds <= 86400 && floor(var.defaults.swarm_retry_seconds) == var.defaults.swarm_retry_seconds,
      var.defaults.swarm_slow_speed_kib >= 1 && var.defaults.swarm_slow_speed_kib <= 10240 && floor(var.defaults.swarm_slow_speed_kib) == var.defaults.swarm_slow_speed_kib,
      var.defaults.metadata_trial_seconds >= 60 && var.defaults.metadata_trial_seconds <= 1800 && floor(var.defaults.metadata_trial_seconds) == var.defaults.metadata_trial_seconds,
      var.defaults.metadata_retry_seconds >= 300 && var.defaults.metadata_retry_seconds <= 86400 && floor(var.defaults.metadata_retry_seconds) == var.defaults.metadata_retry_seconds,
      var.defaults.minimum_rate_sample_seconds >= 30 && var.defaults.minimum_rate_sample_seconds <= 600 && floor(var.defaults.minimum_rate_sample_seconds) == var.defaults.minimum_rate_sample_seconds,
      var.defaults.trial_improvement_percent >= 5 && var.defaults.trial_improvement_percent <= 50 && floor(var.defaults.trial_improvement_percent) == var.defaults.trial_improvement_percent,
      var.defaults.unknown_size_gib >= 1 && var.defaults.unknown_size_gib <= 100000 && floor(var.defaults.unknown_size_gib) == var.defaults.unknown_size_gib,
      var.defaults.other_buffer_gib >= 1 && var.defaults.other_buffer_gib <= 100000 && floor(var.defaults.other_buffer_gib) == var.defaults.other_buffer_gib,
      var.defaults.swarm_min_sample_seconds <= min(var.defaults.swarm_trial_seconds, var.defaults.metadata_trial_seconds),
      var.defaults.swarm_warmup_seconds <= var.defaults.swarm_trial_seconds,
      var.defaults.minimum_rate_sample_seconds <= var.defaults.swarm_trial_seconds
    ])
    error_message = "Use supported policy modes, non-admin SSO defaults, finite integer limits, a valid HTTPS issuer and explicit enforcement categories."
  }
}

variable "locked_settings" {
  type        = list(string)
  default     = []
  description = "Keys owned by deployment; UI updates and resets are denied for these keys."
  validation {
    condition = alltrue([for key in var.locked_settings : contains([
      "timezone", "poll_seconds", "retention_days", "qbit_url", "qbit_username", "qbit_password", "controls_enabled",
      "policy_mode", "managed_categories", "excluded_tags", "download_slots", "minimum_free_gib", "work_budget_gib", "library_reserve_gib",
      "ranking_interval_seconds", "probe_seconds", "retry_seconds", "rate_fresh_seconds", "seed_trial_seconds", "seed_trial_retry_seconds", "min_trial_seeds", "min_proven_speed_kib", "swarm_trial_seconds", "swarm_warmup_seconds", "swarm_min_sample_seconds", "swarm_retry_seconds", "swarm_slow_speed_kib", "metadata_trial_seconds", "metadata_retry_seconds", "minimum_rate_sample_seconds", "trial_improvement_percent", "unknown_size_gib", "other_buffer_gib",
      "sso_enabled", "sso_issuer", "sso_client_id", "sso_client_secret", "sso_label", "sso_allowed_groups", "sso_default_role"
    ], key)])
    error_message = "The lock list contains an unsupported setting."
  }
}

variable "oidc_secret_file" {
  type        = string
  default     = ""
  description = "Optional Docker-host OIDC client secret file. Only the path enters Terraform state."
  validation {
    condition     = var.oidc_secret_file == "" || startswith(var.oidc_secret_file, "/")
    error_message = "Use an absolute host file path."
  }
}

variable "identity_ca_file" {
  type        = string
  default     = ""
  description = "Optional public CA bundle used to verify the identity provider TLS connection."
  validation {
    condition     = var.identity_ca_file == "" || startswith(var.identity_ca_file, "/")
    error_message = "Use an absolute public CA bundle path."
  }
}

variable "observer_feed_file" {
  type        = string
  default     = ""
  description = "Optional narrow, sanitized external optimizer feed. Forces observation and disables client controls."
  validation {
    condition     = var.observer_feed_file == "" || startswith(var.observer_feed_file, "/")
    error_message = "Use an absolute observer file path."
  }
}

variable "library_probe_path" {
  type        = string
  default     = ""
  description = "Optional Docker-host import destination filesystem mounted read-only solely for capacity probing."
  validation {
    condition     = var.library_probe_path == "" || startswith(var.library_probe_path, "/")
    error_message = "Use an absolute filesystem directory path."
  }
}

variable "optimizer_control_directory" {
  type        = string
  default     = ""
  description = "Optional narrow guard control directory with optimizer.sock. Mounted read-only; requires the external observer feed. Never mount the Docker socket."
  validation {
    condition     = var.optimizer_control_directory == "" || (startswith(var.optimizer_control_directory, "/") && var.observer_feed_file != "")
    error_message = "Use an absolute guard control directory and configure its external observer feed."
  }
}
