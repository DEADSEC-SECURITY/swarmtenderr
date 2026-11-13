"""Authenticated API, account administration, settings and dashboard assets."""
import argparse
import csv
import hmac
from http.cookies import SimpleCookie
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import __version__
from .analytics import Analytics
from .auth import Auth, AuthError, COOKIE, ROLES
from .observer import Observer
from .qbit import Qbit, UpstreamError
from .runtime import Runtime
from .secrets import SecretVault
from .settings import Settings
from .store import Conflict, Store


class Application:
    def __init__(self, settings, store, vault):
        self.settings, self.store, self.vault = settings, store, vault
        self.runtime = Runtime(settings, store, vault)
        self.auth = Auth(store, vault)
        self.auth.bootstrap(settings)
        self.analytics = Analytics(store)
        self.observer = Observer(settings, store, None, runtime=self.runtime)

    @property
    def client(self):
        return self.observer.get_client()

    def session_token(self, environ):
        try:
            cookie = SimpleCookie(environ.get("HTTP_COOKIE", ""))
            return cookie[COOKIE].value if COOKIE in cookie else ""
        except Exception:
            return ""

    def authorized(self, environ):
        return self.auth.current(self.session_token(environ))

    def session_cookie(self, token):
        secure = "; Secure" if self.settings.public_url.startswith("https:") else ""
        age = 43200 if token else 0
        return ("Set-Cookie", f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}")

    def status(self, user=None):
        snapshot = self.store.read()
        config = self.runtime.values()
        if snapshot:
            snapshot["controls"] = config["controls_enabled"] and not self.settings.demo and not self.settings.optimizer_state
        changed_client = not self.settings.demo and self.store.meta("observed_client_origin", config["qbit_url"]) != config["qbit_url"]
        return {"version": __version__, "snapshot": snapshot,
                "stale": changed_client or not snapshot or time.time() - snapshot["observed_at"] > max(15, config["poll_seconds"] * 3),
                "error": "Client connection changed; waiting for its first observation" if changed_client else self.observer.last_error, "database": "postgresql" if self.store.postgres else "sqlite",
                "completion_revision": self.store.meta("completion_revision", 0),
                "activity_revision": self.store.meta("activity_revision", 0),
                "configuration_revision": config["revision"], "demo": self.settings.demo,
                "user": user, "timezone": config["timezone"], "poll_seconds": config["poll_seconds"]}

    def require(self, user, role):
        if not user:
            raise AuthError("Sign in to continue")
        if ROLES[user["role"]] < ROLES[role]:
            raise AuthError("Your account does not have permission for this action", 403)

    def body(self, environ):
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0] != "application/json":
            raise ValueError("JSON content is required")
        size = int(environ.get("CONTENT_LENGTH", "0"))
        if not 0 < size <= 16384:
            raise ValueError("Invalid request size")
        value = json.loads(environ["wsgi.input"].read(size))
        if not isinstance(value, dict):
            raise ValueError("Provide a JSON object")
        return value

    def __call__(self, environ, start_response):
        status, data, content_type, extra = 200, {}, "application/json", []
        path, method = environ.get("PATH_INFO", "/"), environ.get("REQUEST_METHOD", "GET")
        try:
            if method == "GET" and path in {"/", "/app.js", "/style.css", "/logo.svg"}:
                filename = "index.html" if path == "/" else path[1:]
                content_type = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "svg": "image/svg+xml"}[filename.rsplit(".", 1)[1]]
                data = (Path(__file__).parent / "static" / filename).read_bytes()
            elif method == "GET" and path == "/healthz":
                data = {"status": "ok", "version": __version__}
            elif method == "GET" and path == "/readyz":
                self.store.config()
                data = {"status": "ready", "version": __version__}
            elif method == "GET" and path == "/api/v1/auth/options":
                config = self.runtime.values()
                data = {"demo": self.settings.demo, "sso_enabled": config["sso_enabled"], "sso_label": config["sso_label"], "version": __version__}
            else:
                token = self.session_token(environ)
                user = self.auth.current(token)
                if method not in {"GET", "HEAD"}:
                    if environ.get("HTTP_ORIGIN") != self.settings.public_url:
                        raise AuthError("Request origin does not match this installation", 403)
                    if path != "/api/v1/auth/login":
                        self.require(user, "viewer")
                        if not hmac.compare_digest(environ.get("HTTP_X_CSRF_TOKEN", ""), self.auth.csrf(token)):
                            raise AuthError("Security token expired; reload and try again", 403)
                    payload = self.body(environ)
                else:
                    payload = {}
                if path == "/api/v1/auth/login" and method == "POST":
                    token = self.auth.login(payload.get("username"), payload.get("password"), environ.get("REMOTE_ADDR", "local"))
                    user = self.auth.current(token)
                    extra.append(self.session_cookie(token))
                    data = {"user": user, "csrf": self.auth.csrf(token)}
                else:
                    self.require(user, "viewer")
                    query = parse_qs(environ.get("QUERY_STRING", ""))
                    if path == "/api/v1/me" and method == "GET":
                        data = {"user": user, "csrf": self.auth.csrf(token)}
                    elif path == "/api/v1/auth/logout" and method == "POST":
                        self.auth.revoke(token)
                        extra.append(self.session_cookie(""))
                        data = {"ok": True}
                    elif path == "/api/v1/account/password" and method == "POST":
                        if not isinstance(payload.get("current_password"), str):
                            raise ValueError("Current password is required")
                        self.auth.set_password(user["id"], payload.get("new_password"), user["username"], current=payload.get("current_password", ""))
                        extra.append(self.session_cookie(""))
                        data = {"ok": True, "message": "Password changed. Sign in again."}
                    elif path == "/api/v1/status" and method == "GET":
                        data = self.status(user)
                    elif path == "/api/v1/activity" and method == "GET":
                        data = self.store.history(admin=user["role"] == "admin")
                    elif path == "/api/v1/statistics" and method == "GET":
                        data = self.analytics.summary(self.runtime.values()["timezone"])
                    elif path in {"/api/v1/completions", "/api/v1/completions.csv"} and method == "GET":
                        limit = 10000 if path.endswith(".csv") else min(100, max(1, int(query.get("limit", [50])[0])))
                        timezone = ZoneInfo(self.runtime.values()["timezone"])
                        start = datetime.fromisoformat(query["from"][0]).replace(tzinfo=timezone).timestamp() if query.get("from") else float(query.get("start", [0])[0])
                        end = (datetime.fromisoformat(query["to"][0]).replace(tzinfo=timezone) + timedelta(days=1)).timestamp() - 0.001 if query.get("to") else float(query.get("end", [time.time() + 60])[0])
                        if end < start:
                            raise ValueError("The end date must not precede the start date")
                        result = self.store.completions(start=start, end=end, search=query.get("search", [""])[0], category="" if query.get("uncategorized", ["0"])[0] == "1" else query.get("category", [None])[0], page=max(1, int(query.get("page", [1])[0])), limit=limit)
                        if path.endswith(".csv"):
                            stream = io.StringIO()
                            writer = csv.writer(stream)
                            writer.writerow(["Name", "Category", "Completed (Unix seconds)", "Payload bytes", "Source"])
                            for row in result["rows"]:
                                cells = [row["name"], row["category"], row["completed"], row["bytes"], row["source"]]
                                writer.writerow(["'" + v if isinstance(v, str) and v.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")) else v for v in cells])
                            data, content_type = stream.getvalue().encode(), "text/csv; charset=utf-8"
                            extra.append(("Content-Disposition", 'attachment; filename="swarmtenderr-completions.csv"'))
                        else:
                            data = result
                    elif path == "/api/v1/settings" and method in {"GET", "PATCH"}:
                        self.require(user, "admin")
                        if method == "PATCH":
                            self.runtime.save(payload.get("changes", {}), payload.get("revision"), user["username"])
                            self.observer.wake.set()
                        data = self.runtime.public()
                    elif path == "/api/v1/settings/reset" and method == "POST":
                        self.require(user, "admin")
                        self.runtime.save({}, payload.get("revision"), user["username"], reset=payload.get("keys", []))
                        self.observer.wake.set()
                        data = self.runtime.public()
                    elif path == "/api/v1/settings/test-connection" and method == "POST":
                        self.require(user, "admin")
                        if self.settings.demo:
                            raise AuthError("Real client connections are disabled in demo mode", 403)
                        allowed = {"qbit_url", "qbit_username", "qbit_password"}
                        if set(payload) - allowed:
                            raise ValueError("Unknown connection field")
                        self.runtime.validate(payload)
                        config = self.runtime.values()
                        config.update({k: v for k, v in payload.items() if v})
                        client = Qbit(config["qbit_url"], config["qbit_username"], password=config["qbit_password"])
                        rows = client.torrents()
                        data = {"ok": True, "torrents": len(rows), "message": "Authentication and torrent listing succeeded"}
                    elif path == "/api/v1/users" and method in {"GET", "POST"}:
                        self.require(user, "admin")
                        if method == "POST":
                            self.auth.create_user(payload, user["username"])
                        data = {"users": self.auth.users()}
                    elif re.fullmatch(r"/api/v1/users/[a-f0-9]{32}", path) and method == "PATCH":
                        self.require(user, "admin")
                        self.auth.update_user(path.split("/")[4], payload, user["username"])
                        data = {"users": self.auth.users()}
                    elif re.fullmatch(r"/api/v1/users/[a-f0-9]{32}/password", path) and method == "POST":
                        self.require(user, "admin")
                        self.auth.set_password(path.split("/")[4], payload.get("password"), user["username"])
                        data = {"ok": True}
                    elif method == "POST" and re.fullmatch(r"/api/v1/torrents/[a-fA-F0-9]{40}(?:[a-fA-F0-9]{24})?/actions", path):
                        self.require(user, "operator")
                        status, data = self.action(path.split("/")[4], payload, user)
                    else:
                        status, data = 404, {"error": "Unknown endpoint"}
        except AuthError as error:
            status, data = error.status, {"error": str(error)}
        except Conflict as error:
            status, data = 409, {"error": str(error)}
        except PermissionError as error:
            status, data = 403, {"error": str(error)}
        except UpstreamError as error:
            status, data = 502, {"error": str(error)}
        except (ValueError, UnicodeError, KeyError) as error:
            status, data = 400, {"error": str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "Invalid request"}
        except Exception:
            status, data = 503, {"error": "Service unavailable; check database and runtime files"}
        if not isinstance(data, bytes):
            data = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
        ws_origin = self.settings.public_url.replace("https://", "wss://").replace("http://", "ws://")
        headers = [("Content-Type", content_type), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
                   ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self' {ws_origin}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        from http import HTTPStatus
        start_response(f"{status} {HTTPStatus(status).phrase}", headers + extra)
        return [data]

    def action(self, identity, payload, user):
        config = self.runtime.values()
        if not config["controls_enabled"] or self.settings.demo or self.settings.optimizer_state:
            raise AuthError("Torrent controls are disabled for this installation", 403)
        action = payload.get("action")
        if not isinstance(action, str) or action not in {"pause", "resume", "prioritize", "reannounce"} or set(payload) != {"action"}:
            raise ValueError("Unsupported action")
        audit = self.store.begin_action(identity, action, user["username"])
        try:
            self.client.action(identity, action)
        except ValueError as error:
            self.store.finish_action(audit, "rejected")
            return 409, {"error": str(error)}
        except UpstreamError:
            self.store.finish_action(audit, "uncertain")
            return 502, {"error": "Result uncertain. Inspect the torrent before retrying.", "id": audit}
        self.store.finish_action(audit, "accepted")
        self.observer.wake.set()
        return 202, {"id": audit, "outcome": "accepted", "message": "Request accepted; waiting for the next client observation"}


def create_app(settings=None, start_observer=True):
    settings = settings or Settings.from_environment()
    store = Store(settings.database)
    store.initialize()
    store.claim_mode(settings.demo)
    key_path = Path(settings.secret_key_file or str(Path(settings.data_dir) / "secret.key"))
    if not key_path.exists() and {"qbit_password", "sso_client_secret"}.intersection(store.config()[1]):
        raise ValueError("Existing encrypted settings require the original runtime key; restore it from backup")
    vault = SecretVault(key_path)
    app = Application(settings, store, vault)
    if settings.demo:
        from .demo import seed_history
        seed_history(store)
    if start_observer:
        app.observer.start()
    return app


def main():
    parser = argparse.ArgumentParser(description="Swarmtenderr")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--reset-password-for", metavar="USERNAME")
    parser.add_argument("--password-file")
    args = parser.parse_args()
    if args.reset_password_for:
        if not args.password_file:
            parser.error("Recovery requires --password-file")
        app = create_app(start_observer=False)
        account = next((u for u in app.auth.users() if u["username"] == args.reset_password_for), None)
        if account is None:
            parser.error("Account not found")
        app.auth.set_password(account["id"], Path(args.password_file).read_text().strip(), "on-host-recovery")
        print("Password reset; existing sessions revoked")
        return
    import uvicorn
    uvicorn.run("swarmtenderr.live:create_app", factory=True, host="0.0.0.0", port=8787,
                workers=1, access_log=False, ws_max_size=1024, ws_max_queue=4, ws_ping_interval=20, ws_ping_timeout=20)


if __name__ == "__main__":
    main()
