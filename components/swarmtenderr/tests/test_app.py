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
from swarmtenderr.observer import Observer, optimizer, projection
from types import SimpleNamespace
from swarmtenderr.qbit import Qbit, UpstreamError
from swarmtenderr.settings import Settings
from swarmtenderr.store import Store


HASH = "a" * 40


class QbitHttpTests(unittest.TestCase):
    """The actual adapter makes real HTTP calls to an isolated qBittorrent stand-in."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests, self.progress, self.tags, self.force = [], .5, "", False
        self.cookie_name = "SID"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                owner.requests.append(("POST", self.path, form))
                self.send_response(200)
                if self.path.endswith("auth/login"):
                    self.send_header("Set-Cookie", owner.cookie_name + "=fixture-session; HttpOnly; Path=/")
                self.end_headers()

            def do_GET(self):
                owner.requests.append(("GET", self.path, {}))
                if owner.cookie_name + "=fixture-session" not in self.headers.get("Cookie", ""):
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

    def test_port_scoped_client_cookie_is_supported(self):
        self.cookie_name = "QBT_SID_8080"
        self.client.torrents()
        self.client.action(HASH, "pause")
        self.assertEqual(self.requests[-1][1], "/api/v2/torrents/stop")

    def test_protected_transfers_make_no_control_post(self):
        for progress, tags, force in ((1, "", False), (.5, "swarmtenderr-keep", False), (.5, "", True)):
            self.progress, self.tags, self.force = progress, tags, force
            with self.assertRaises(ValueError):
                self.client.action(HASH, "pause")
        self.assertFalse(any("/torrents/stop" in path for _, path, _ in self.requests))

    def test_configured_operator_tag_is_rechecked_before_mutation(self):
        for configured, actual in [('operator-keep', 'operator-keep'), ('operator keep', 'spare, operator keep')]:
            client = Qbit(self.client.url, "admin", self.client.password_file, protected_tags=[configured])
            self.tags = actual
            with self.assertRaises(ValueError):
                client.action(HASH, "pause")
        self.assertFalse(any("/torrents/stop" in path for _, path, _ in self.requests))

    def test_arbitrary_or_bulk_operations_are_rejected(self):
        for identity, action in (("all", "pause"), (HASH + "|" + HASH, "pause"), (HASH, "delete")):
            with self.assertRaises(ValueError):
                self.client.action(identity, action)
        self.assertEqual(self.requests, [])


class ConfiguredProtectionTests(unittest.TestCase):
    def test_scheduler_ownership_uses_operator_tags(self):
        from swarmtenderr.controller import Controller
        config = {'managed_categories': ['media'], 'excluded_tags': ['operator keep']}
        row = {'category': 'media', 'state': 'queuedDL', 'progress': .5, 'tags': 'spare, operator keep'}
        self.assertFalse(Controller.managed(row, config))
        row['tags'] = 'spare'
        self.assertTrue(Controller.managed(row, config))

    def test_projection_uses_operator_configuration(self):
        rows = [{"hash": HASH, "progress": .5, "state": "queuedDL", "tags": "spare, operator keep"}]
        self.assertFalse(projection(rows, {})[0]['protected'])
        self.assertTrue(projection(rows, {}, protected_tags=['operator keep'])[0]['protected'])

    def test_changed_tag_configuration_rebuilds_the_mutation_adapter(self):
        config = {'qbit_url': 'http://client:8080', 'qbit_username': 'admin',
                  'qbit_password': 'test-only-fixture', 'excluded_tags': ['operator-keep']}
        observer = Observer(None, None, None, SimpleNamespace(values=lambda: config))
        first = observer.get_client()
        self.assertIn('operator-keep', first.protected_tags)
        config['excluded_tags'] = ['another-keep']
        second = observer.get_client()
        self.assertIsNot(first, second)
        self.assertNotIn('operator-keep', second.protected_tags)
        self.assertIn('another-keep', second.protected_tags)


if __name__ == "__main__":
    unittest.main()
