"""qBittorrent 5 adapter: bounded reads, cookie auth, and an explicit action allowlist."""

from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import threading
import urllib.error
import urllib.parse
import urllib.request


class UpstreamError(Exception):
    """A sanitized upstream failure suitable for the dashboard."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpstreamError("Unexpected redirect from qBittorrent")


ACTIONS = {"pause": "stop", "resume": "start", "prioritize": "topPrio", "reannounce": "reannounce"}
HASH = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class Qbit:
    def __init__(self, url, username, password_file="", *, password=None):
        self.url, self.username, self.password_file = url, username, password_file
        self._password = password
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))
        self.lock = threading.RLock()
        self.authenticated = False

    def _request(self, path, form=None):
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(self.url + "/api/v2/" + path, data=body,
            headers={"Referer": self.url + "/", "Origin": self.url, "Accept": "application/json"})
        with self.opener.open(request, timeout=8) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise UpstreamError("qBittorrent response exceeds the size limit")
            return raw.decode()

    def request(self, path, form=None):
        with self.lock:
            try:
                if not self.authenticated:
                    self.cookies.clear()
                    password = self._password if self._password is not None else Path(self.password_file).read_text().strip()
                    self._request("auth/login", {"username": self.username, "password": password})
                    if not any(cookie.name == "SID" for cookie in self.cookies):
                        raise UpstreamError("qBittorrent rejected authentication")
                    self.authenticated = True
                raw = self._request(path, form)
                return json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    self.authenticated = False
                raise UpstreamError(f"qBittorrent returned HTTP {error.code}") from None
            except (ValueError, OSError, TimeoutError, urllib.error.URLError):
                raise UpstreamError("qBittorrent is unavailable or returned an invalid response") from None

    def torrents(self):
        rows = self.request("torrents/info")
        if not isinstance(rows, list) or len(rows) > 10000 or any(not isinstance(row, dict) for row in rows):
            raise UpstreamError("Invalid qBittorrent torrent list")
        return rows

    def action(self, identity, action, *, eligible=None):
        if not HASH.fullmatch(identity) or action not in ACTIONS:
            raise ValueError("Invalid torrent or unsupported action")
        # Refresh directly before every mutation; never trust a browser's cached row.
        rows = self.request("torrents/info?" + urllib.parse.urlencode({"hashes": identity}))
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("hash", "").lower() != identity.lower():
            raise ValueError("Torrent no longer exists")
        row = rows[0]
        if (row.get("progress", 1) >= 1 or row.get("force_start", False)
                or row.get("state") == "forcedDL"
                or {"apollo-keep", "swarmtenderr-keep"} & set(str(row.get("tags", "")).replace(" ", "").split(","))):
            raise ValueError("Completed, force-started and protected torrents cannot be controlled")
        if eligible is not None and not eligible(row):
            raise ValueError("Torrent no longer belongs to the managed category scope")
        self.request("torrents/" + ACTIONS[action], {"hashes": identity})

    def system_state(self):
        result = self.request("sync/maindata?rid=0")
        state = result.get("server_state", {}) if isinstance(result, dict) else {}
        free = state.get("free_space_on_disk")
        if type(free) not in (float, int) or free < 0:
            raise UpstreamError("qBittorrent did not report download filesystem capacity")
        return state

    def preferences(self):
        result = self.request("app/preferences")
        if not isinstance(result, dict):
            raise UpstreamError("qBittorrent preferences are unavailable")
        return result

    def download_cap(self, slots, *, expected=None):
        if type(slots) is not int or not 0 <= slots <= 100:
            raise ValueError("Invalid download ceiling")
        if expected is not None and self.preferences().get("max_active_downloads") != expected:
            raise ValueError("The native download ceiling changed before the request")
        self.request("app/setPreferences", {"json": json.dumps({"max_active_downloads": slots})})
