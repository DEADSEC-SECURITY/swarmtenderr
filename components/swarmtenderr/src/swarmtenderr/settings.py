"""Deployment defaults; runtime configuration and secrets have separate ownership."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def origin(value, *, empty=False):
    if empty and not value:
        return ""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Use an HTTP(S) origin without credentials, query or path")
    return value.rstrip("/")


def read_secret(path):
    if not path:
        return ""
    value = Path(path).read_text().strip()
    if not value:
        raise ValueError("A configured secret file is empty")
    return value


@dataclass(frozen=True)
class Settings:
    database: str = "sqlite:////data/swarmtenderr.sqlite3"
    data_dir: str = "/data"
    qbit_url: str = ""
    qbit_user: str = "admin"
    qbit_password_file: str = ""
    app_user: str = "admin"
    app_password: str = field(default="", repr=False)
    public_url: str = "http://localhost:8787"
    controls: bool = True
    optimizer_state: str = ""
    optimizer_control_socket: str = ""
    demo: bool = False
    poll_seconds: int = 3
    timezone: str = "UTC"
    locked_settings: tuple = ()
    defaults: dict = field(default_factory=dict)
    secret_key_file: str = ""
    oidc_secret_file: str = ""
    library_probe_path: str = ""
    allow_http_oidc: bool = False

    @classmethod
    def from_environment(cls):
        demo = os.environ.get("SWARMTENDERR_DEMO", "false").lower() == "true"
        config_path = os.environ.get("SWARMTENDERR_CONFIG_FILE", "")
        defaults = json.loads(Path(config_path).read_text()) if config_path else json.loads(os.environ.get("SWARMTENDERR_DEFAULTS_JSON", "{}"))
        if not isinstance(defaults, dict):
            raise ValueError("Configuration defaults must be an object")
        return cls(
            database=read_secret(os.environ.get("SWARMTENDERR_DATABASE_URL_FILE", "")) or os.environ.get("SWARMTENDERR_DATABASE_URL", cls.database),
            data_dir=os.environ.get("SWARMTENDERR_DATA_DIR", "/data"),
            qbit_url=origin(os.environ.get("QBITTORRENT_URL", ""), empty=True),
            qbit_user=os.environ.get("QBITTORRENT_USERNAME", "admin"),
            qbit_password_file=os.environ.get("QBITTORRENT_PASSWORD_FILE", ""),
            app_user=os.environ.get("SWARMTENDERR_ADMIN_USERNAME", "admin"),
            app_password=read_secret(os.environ.get("SWARMTENDERR_PASSWORD_FILE", "")),
            public_url=origin(os.environ.get("SWARMTENDERR_PUBLIC_URL", cls.public_url)),
            controls=os.environ.get("SWARMTENDERR_CONTROLS", "true").lower() == "true",
            optimizer_state=os.environ.get("SWARMTENDERR_OPTIMIZER_STATE", ""),
            optimizer_control_socket=os.environ.get("SWARMTENDERR_OPTIMIZER_CONTROL_SOCKET", ""), demo=demo,
            poll_seconds=int(os.environ.get("SWARMTENDERR_POLL_SECONDS", "3")),
            timezone=os.environ.get("TZ", "UTC"), defaults=defaults,
            locked_settings=tuple(filter(None, os.environ.get("SWARMTENDERR_LOCKED_SETTINGS", "").split(","))),
            secret_key_file=os.environ.get("SWARMTENDERR_SECRET_KEY_FILE", ""),
            oidc_secret_file=os.environ.get("SWARMTENDERR_OIDC_SECRET_FILE", ""),
            library_probe_path=os.environ.get("SWARMTENDERR_LIBRARY_PROBE_PATH", ""),
            allow_http_oidc=os.environ.get("SWARMTENDERR_ALLOW_HTTP_OIDC", "false").lower() == "true")
