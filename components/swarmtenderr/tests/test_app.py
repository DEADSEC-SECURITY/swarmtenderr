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
