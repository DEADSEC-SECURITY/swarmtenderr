"""Authenticated WSGI API and static dashboard; imports perform no I/O."""

import argparse
import base64
import hmac
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from . import __version__
from .observer import Observer
from .qbit import Qbit, UpstreamError
from .settings import Settings
from .store import Store


class Application:
    def __init__(self, settings, store, client, observer):
        self.settings, self.store, self.client, self.observer = settings, store, client, observer

    def __call__(self, environ, start_response):
        status, data, content_type, extra = 200, b"", "application/json", []
        path, method = environ.get("PATH_INFO", "/"), environ.get("REQUEST_METHOD", "GET")
        try:
            if path == "/healthz" and method == "GET":
                data = {"status": "ok", "version": __version__}
            elif not self.authorized(environ):
                status, data = 401, {"error": "Dashboard authentication required"}
                extra = [("WWW-Authenticate", 'Basic realm="Swarmtenderr", charset="UTF-8"')]
            elif method == "GET" and path in {"/", "/app.js", "/style.css", "/logo.svg"}:
                filename = "index.html" if path == "/" else path[1:]
                content_type = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                                "css": "text/css; charset=utf-8", "svg": "image/svg+xml"}[filename.rsplit(".", 1)[1]]
                data = (Path(__file__).parent / "static" / filename).read_bytes()
            elif method == "GET" and path == "/api/v1/status":
                data = self.status()
            elif method == "GET" and path == "/api/v1/activity":
                data = self.store.history()
            elif method == "POST" and re.fullmatch(r"/api/v1/torrents/[a-fA-F0-9]{40}(?:[a-fA-F0-9]{24})?/actions", path):
                status, data = self.action(environ, path.split("/")[4])
            else:
                status, data = 404, {"error": "Unknown endpoint"}
        except (ValueError, UnicodeError):
            status, data = 400, {"error": "Invalid request"}
        except Exception:
            status, data = 503, {"error": "Service unavailable; check database and runtime configuration"}
        if not isinstance(data, bytes):
            data = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
        headers = [("Content-Type", content_type), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
                   ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        reasons = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict", 502: "Bad Gateway", 503: "Service Unavailable"}
        start_response(f"{status} {reasons[status]}", headers + extra)
        return [data]

    def authorized(self, environ):
        if self.settings.demo:
            return True
        try:
            scheme, encoded = environ.get("HTTP_AUTHORIZATION", "").split(" ", 1)
            if scheme.lower() != "basic":
                return False
            identity = base64.b64decode(encoded, validate=True)
            expected = f"{self.settings.app_user}:{self.settings.app_password}".encode()
            return hmac.compare_digest(identity, expected)
        except (ValueError, UnicodeError):
            return False

    def status(self):
        snapshot = self.store.read()
        return {"version": __version__, "snapshot": snapshot,
                "stale": not snapshot or time.time() - snapshot["observed_at"] > max(15, self.settings.poll_seconds * 3),
                "error": self.observer.last_error, "database": "postgresql" if self.store.postgres else "sqlite"}

    def action(self, environ, identity):
        if not self.settings.controls or self.settings.demo or self.settings.optimizer_state:
            return 403, {"error": "Controls are disabled in observation mode"}
        if environ.get("HTTP_ORIGIN") != self.settings.public_url:
            return 403, {"error": "A matching dashboard Origin is required"}
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0] != "application/json":
            return 400, {"error": "JSON content is required"}
        length = int(environ.get("CONTENT_LENGTH", "0"))
        if not 0 < length <= 1024:
            return 400, {"error": "Invalid request size"}
        payload = json.loads(environ["wsgi.input"].read(length))
        if (not isinstance(payload, dict) or set(payload) != {"action"}
                or not isinstance(payload["action"], str)
                or payload["action"] not in {"pause", "resume", "prioritize", "reannounce"}):
            return 400, {"error": "Unsupported action"}
        audit = self.store.begin_action(identity, payload["action"])
        try:
            self.client.action(identity, payload["action"])
        except ValueError as error:
            self.store.finish_action(audit, "rejected")
            return 409, {"error": str(error)}
        except UpstreamError:
            # A timeout may follow an applied mutation. Do not retry automatically.
            self.store.finish_action(audit, "unknown")
            return 502, {"error": "Upstream result uncertain; inspect the torrent before retrying", "id": audit}
        self.store.finish_action(audit, "accepted")
        return 202, {"id": audit, "outcome": "accepted", "message": "qBittorrent accepted the request; the next observation will show its state"}


def create_app(settings=None, start_observer=True):
    try:
        settings = settings or Settings.from_environment()
        store = Store(settings.database)
        store.initialize()
    except Exception:
        raise RuntimeError("Swarmtenderr initialization failed; check runtime files and database connectivity") from None
    client = Qbit(settings.qbit_url, settings.qbit_user, settings.qbit_password_file)
    observer = Observer(settings, store, client)
    if start_observer:
        observer.start()
    return Application(settings, store, client, observer)


def main():
    parser = argparse.ArgumentParser(description="Swarmtenderr dashboard")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    import uvicorn
    uvicorn.run("swarmtenderr.live:create_app", factory=True, host="0.0.0.0", port=8787,
                workers=1, ws_max_size=1024, ws_max_queue=4, ws_ping_interval=20, ws_ping_timeout=20)


if __name__ == "__main__":
    main()
