"""Runtime-only configuration. Secret file contents never enter deployment definitions."""

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


def secret_file(name: str) -> str:
    path = os.environ.get(name, "")
    if not path:
        raise ValueError(f"{name} must point to a readable secret file")
    value = Path(path).read_text().strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(frozen=True)
class Settings:
    database: str = "sqlite:////data/swarmtenderr.sqlite3"
    qbit_url: str = "http://qbittorrent:8080"
    qbit_user: str = "admin"
    qbit_password_file: str = ""
    app_user: str = "admin"
    app_password: str = ""
    public_url: str = "http://localhost:8787"
    controls: bool = False
    optimizer_state: str = ""
    demo: bool = False
    poll_seconds: int = 3

    @classmethod
    def from_environment(cls):
        demo = os.environ.get("SWARMTENDERR_DEMO", "false").lower() == "true"
        database_file = os.environ.get("SWARMTENDERR_DATABASE_URL_FILE", "")
        database = (secret_file("SWARMTENDERR_DATABASE_URL_FILE") if database_file
                    else os.environ.get("SWARMTENDERR_DATABASE_URL", cls.database))
        settings = cls(
            database=database,
            qbit_url=os.environ.get("QBITTORRENT_URL", cls.qbit_url).rstrip("/"),
            qbit_user=os.environ.get("QBITTORRENT_USERNAME", "admin"),
            qbit_password_file=os.environ.get("QBITTORRENT_PASSWORD_FILE", ""),
            app_password="" if demo else secret_file("SWARMTENDERR_PASSWORD_FILE"),
            public_url=os.environ.get("SWARMTENDERR_PUBLIC_URL", cls.public_url).rstrip("/"),
            controls=os.environ.get("SWARMTENDERR_CONTROLS", "false").lower() == "true",
            optimizer_state=os.environ.get("SWARMTENDERR_OPTIMIZER_STATE", ""),
            demo=demo,
            poll_seconds=int(os.environ.get("SWARMTENDERR_POLL_SECONDS", "3")),
        )
        for value in (settings.public_url, settings.qbit_url):
            url = urlsplit(value)
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                    or url.password or url.query or url.fragment or url.path not in ("", "/")):
                raise ValueError("Service URLs require an HTTP(S) origin without credentials or paths")
        if not 1 <= settings.poll_seconds <= 3600:
            raise ValueError("Poll interval must be between 1 and 3600 seconds")
        if settings.controls and (demo or settings.optimizer_state):
            raise ValueError("Demo and external-controller modes are observation only")
        if not demo and not settings.qbit_password_file:
            raise ValueError("QBITTORRENT_PASSWORD_FILE is required")
        return settings
