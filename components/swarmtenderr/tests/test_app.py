import base64
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from swarmtenderr.app import create_app
from swarmtenderr.observer import optimizer, projection
from swarmtenderr.qbit import Qbit, UpstreamError
from swarmtenderr.settings import Settings
from swarmtenderr.store import Store


HASH = "a" * 40


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(database="sqlite:///" + self.temp.name + "/state.db", demo=True)
        self.app = create_app(self.settings, start_observer=False)
        self.app.observer.poll()

    def request(self, path, method="GET", body=None, **headers):
        response = []
        raw = json.dumps(body).encode() if body is not None else b""
        environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "wsgi.input": io.BytesIO(raw),
                   "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json", **headers}
        data = b"".join(self.app(environ, lambda status, fields: response.extend((status, dict(fields)))))
        return int(response[0].split()[0]), response[1], data

    def test_demo_is_explicit_and_read_only(self):
        code, headers, raw = self.request("/api/v1/status")
        data = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertTrue(data["snapshot"]["demo"])
        self.assertFalse(data["snapshot"]["controls"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request(f"/api/v1/torrents/{HASH}/actions", "POST", {"action": "pause"})[0], 403)

    def test_auth_required_for_all_data_and_assets(self):
        self.app.settings = replace(self.settings, demo=False, app_password="test-only-dashboard-pass")
        for path in ("/", "/app.js", "/api/v1/status", "/api/v1/activity"):
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request("/healthz")[0], 200)
        auth = "Basic " + base64.b64encode(b"admin:test-only-dashboard-pass").decode()
        self.assertEqual(self.request("/api/v1/status", HTTP_AUTHORIZATION=auth)[0], 200)

    def test_static_assets_and_path_containment(self):
        for path in ("/", "/app.js", "/style.css", "/logo.svg"):
            code, headers, raw = self.request(path)
            self.assertEqual(code, 200)
            self.assertTrue(raw)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/../settings.py")[0], 404)

    def test_manual_control_audit_and_csrf(self):
        self.app.settings = replace(self.settings, demo=False, controls=True, app_password="fixture")
        auth = "Basic " + base64.b64encode(b"admin:fixture").decode()
        path = f"/api/v1/torrents/{HASH}/actions"
        self.assertEqual(self.request(path, "POST", {"action": "pause"}, HTTP_AUTHORIZATION=auth)[0], 403)
        for action in ("delete", "forceStart", "setPreferences"):
            self.assertEqual(self.request(path, "POST", {"action": action}, HTTP_AUTHORIZATION=auth, HTTP_ORIGIN=self.settings.public_url)[0], 400)
        with patch.object(self.app.client, "action") as action:
            result = self.request(path, "POST", {"action": "pause"}, HTTP_AUTHORIZATION=auth, HTTP_ORIGIN=self.settings.public_url)
            self.assertEqual(result[0], 202)
            action.assert_called_once_with(HASH, "pause")
        self.assertEqual(self.app.store.history()["actions"][0]["outcome"], "accepted")

    def test_uncertain_mutation_is_not_retried(self):
        self.app.settings = replace(self.settings, controls=True, demo=False, app_password="fixture")
        auth = "Basic " + base64.b64encode(b"admin:fixture").decode()
        with patch.object(self.app.client, "action", side_effect=UpstreamError("timeout")) as action:
            self.assertEqual(self.request(f"/api/v1/torrents/{HASH}/actions", "POST", {"action": "resume"}, HTTP_AUTHORIZATION=auth, HTTP_ORIGIN=self.settings.public_url)[0], 502)
            self.assertEqual(action.call_count, 1)
        self.assertEqual(self.app.store.history()["actions"][0]["outcome"], "unknown")

    def test_missing_database_is_not_reported_as_healthy(self):
        with patch.object(self.app.store, "read", side_effect=OSError("secret-in-error")):
            code, _, raw = self.request("/api/v1/status")
        self.assertEqual(code, 503)
        self.assertNotIn(b"secret-in-error", raw)

    def test_staleness_and_unknown_metadata_eta(self):
        data = self.app.store.read()
        data["observed_at"] = time.time() - 3600
        self.app.store.snapshot(data)
        self.assertTrue(json.loads(self.request("/api/v1/status")[2])["stale"])
        self.assertIsNone(next(row for row in data["torrents"] if row["state"] == "metaDL")["eta_seconds"])

    def test_credentials_paths_trackers_and_raw_state_are_not_exported(self):
        row = {"hash": HASH, "name": "title", "tracker": "private", "save_path": "/secret/path", "progress": .5, "magnet_uri": "secret"}
        raw = json.dumps(projection([row], {}))
        for word in ("private", "/secret/path", "magnet_uri"):
            self.assertNotIn(word, raw)
        path = Path(self.temp.name) / "optimizer.json"
        path.write_text(json.dumps({"root_token": "never-export", "optimizer": {"checked_at": time.time(), "rows": []}, "space_plan": {"requested_slots": 3, "private": "never-export"}}))
        public = optimizer(str(path))
        self.assertTrue(public["connected"])
        self.assertNotIn("never-export", json.dumps(public))

    def test_corrupt_or_stale_optimizer_does_not_fabricate_health(self):
        path = Path(self.temp.name) / "optimizer.json"
        path.write_text('{"optimizer": {"checked_at": 1}}')
        self.assertTrue(optimizer(str(path))["stale"])
        path.write_text("not-json")
        self.assertFalse(optimizer(str(path))["connected"])

    def test_store_restart_and_audit_persistence(self):
        identifier = self.app.store.begin_action(HASH, "pause")
        self.app.store.finish_action(identifier, "accepted")
        reopened = Store(self.settings.database)
        reopened.initialize()
        self.assertEqual(reopened.read()["torrents"], self.app.store.read()["torrents"])
        self.assertEqual(reopened.history()["actions"][0]["outcome"], "accepted")

    def test_sample_retention_and_future_schema_refusal(self):
        with self.app.store.connection() as connection:
            self.app.store.execute(connection, "INSERT INTO swarmtenderr_samples VALUES (?, ?, ?, ?)", ("old", 1, 0, 0))
        self.app.observer.poll()
        self.assertTrue(all(row["at"] > 1 for row in self.app.store.history()["samples"]))
        with self.app.store.connection() as connection:
            self.app.store.execute(connection, "UPDATE swarmtenderr_schema SET version=999")
        with self.assertRaises(ValueError):
            self.app.store.initialize()

    def test_environment_defaults_fail_closed(self):
        with patch.dict(os.environ, {"SWARMTENDERR_DEMO": "true", "SWARMTENDERR_CONTROLS": "true"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_environment()
        with patch.dict(os.environ, {"SWARMTENDERR_DEMO": "true", "QBITTORRENT_URL": "https://user:secret@example.org"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_environment()


class QbitHttpTests(unittest.TestCase):
    """The actual adapter makes real HTTP calls to an isolated qBittorrent stand-in."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests, self.progress, self.tags, self.force = [], .5, "", False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                owner.requests.append(("POST", self.path, form))
                self.send_response(200)
                if self.path.endswith("auth/login"):
                    self.send_header("Set-Cookie", "SID=fixture-session; HttpOnly; Path=/")
                self.end_headers()

            def do_GET(self):
                owner.requests.append(("GET", self.path, {}))
                if "SID=fixture-session" not in self.headers.get("Cookie", ""):
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps([{"hash": HASH, "progress": owner.progress, "tags": owner.tags, "force_start": owner.force}]).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        password = Path(self.temp.name) / "qbit-pass"
        password.write_text("test-only-fixture")
        self.client = Qbit(f"http://127.0.0.1:{self.server.server_port}", "admin", str(password))

    def test_cookie_login_and_single_hash_control(self):
        self.client.torrents()
        self.client.action(HASH, "prioritize")
        self.assertEqual(self.requests[-1], ("POST", "/api/v2/torrents/topPrio", {"hashes": [HASH]}))
        self.assertEqual(sum(path.endswith("auth/login") for _, path, _ in self.requests), 1)

    def test_protected_transfers_make_no_control_post(self):
        for progress, tags, force in ((1, "", False), (.5, "apollo-keep", False), (.5, "swarmtenderr-keep", False), (.5, "", True)):
            self.progress, self.tags, self.force = progress, tags, force
            with self.assertRaises(ValueError):
                self.client.action(HASH, "pause")
        self.assertFalse(any("/torrents/stop" in path for _, path, _ in self.requests))

    def test_arbitrary_or_bulk_operations_are_rejected(self):
        for identity, action in (("all", "pause"), (HASH + "|" + HASH, "pause"), (HASH, "delete")):
            with self.assertRaises(ValueError):
                self.client.action(identity, action)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
