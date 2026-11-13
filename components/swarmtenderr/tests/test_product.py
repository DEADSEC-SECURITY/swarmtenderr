"""Identity, settings, migration and completion accounting acceptance contracts."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from swarmtenderr.app import create_app
from swarmtenderr.auth import AuthError
from swarmtenderr.analytics import Analytics
from swarmtenderr.observer import projection
from swarmtenderr.qbit import UpstreamError
from swarmtenderr.settings import Settings
from swarmtenderr.store import Store
from database_fixture import database_url


HASH = "a" * 40


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        self.password = "test-only-administrator-password"
        self.settings = Settings(database=database_url(self, path / "state.db"), data_dir=str(path),
                                 app_password=self.password, qbit_url="http://localhost:8080")
        self.app = create_app(self.settings, False)
        self.token = self.app.auth.login("admin", self.password, "fixture")
        self.csrf = self.app.auth.csrf(self.token)

    def request(self, path, method="GET", payload=None, auth=True, **headers):
        response = []
        raw = json.dumps(payload).encode() if payload is not None else b""
        route, _, query = path.partition("?")
        env = {"PATH_INFO": route, "QUERY_STRING": query, "REQUEST_METHOD": method,
               "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json",
               "HTTP_ORIGIN": self.settings.public_url, "wsgi.input": io.BytesIO(raw),
               "REMOTE_ADDR": "fixture", **({"HTTP_COOKIE": "swarmtenderr_session=" + self.token,
                "HTTP_X_CSRF_TOKEN": self.csrf} if auth else {}), **headers}
        body = b"".join(self.app(env, lambda status, fields: response.extend((status, dict(fields)))))
        return int(response[0].split()[0]), json.loads(body) if response[1]["Content-Type"] == "application/json" else body, response[1]

    def add_user(self, role="viewer"):
        code, data, _ = self.request("/api/v1/users", "POST", {"username": role, "display_name": role.title(),
            "role": role, "password": "test-only-account-password"})
        self.assertEqual(code, 200, data)
        return next(u for u in data["users"] if u["username"] == role)

    def test_real_login_cookie_logout_and_revocation(self):
        self.assertEqual(self.request("/api/v1/status", auth=False)[0], 401)
        code, data, headers = self.request("/api/v1/auth/login", "POST",
            {"username": "admin", "password": self.password}, auth=False)
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertEqual(data["user"]["role"], "admin")
        self.assertEqual(self.request("/api/v1/auth/logout", "POST", {})[0], 200)
        self.assertEqual(self.request("/api/v1/status")[0], 401)

    def test_static_login_assets_are_public_but_data_is_private(self):
        for path in ("/", "/app.js", "/style.css", "/logo.svg", "/healthz", "/api/v1/auth/options"):
            self.assertEqual(self.request(path, auth=False)[0], 200)
        for path in ("/api/v1/status", "/api/v1/statistics", "/api/v1/completions", "/api/v1/completions.csv", "/api/v1/activity", "/api/v1/users"):
            self.assertEqual(self.request(path, auth=False)[0], 401)
        self.assertEqual(self.request("/../settings.py")[0], 404)

    def test_roles_are_enforced_on_the_server(self):
        self.add_user("viewer")
        self.token = self.app.auth.login("viewer", "test-only-account-password", "fixture")
        self.csrf = self.app.auth.csrf(self.token)
        self.assertEqual(self.request("/api/v1/statistics")[0], 200)
        self.assertEqual(self.request("/api/v1/users")[0], 403)
        self.assertEqual(self.request("/api/v1/settings")[0], 403)
        self.assertEqual(self.request(f"/api/v1/torrents/{HASH}/actions", "POST", {"action": "pause"})[0], 403)

    def test_last_admin_and_session_invalidation(self):
        admin = self.request("/api/v1/me")[1]["user"]
        result = self.request("/api/v1/users/" + admin["id"], "PATCH", {"active": False})
        self.assertEqual(result[0], 400)
        self.assertIn("last active administrator", result[1]["error"])
        viewer = self.add_user()
        other = self.app.auth.login("viewer", "test-only-account-password", "other")
        self.assertIsNotNone(self.app.auth.current(other))
        self.assertEqual(self.request("/api/v1/users/" + viewer["id"], "PATCH", {"active": False})[0], 200)
        self.assertIsNone(self.app.auth.current(other))

    def test_password_change_checks_current_and_revokes_existing_sessions(self):
        self.assertEqual(self.request("/api/v1/account/password", "POST",
            {"current_password": None, "new_password": "test-only-replacement-password"})[0], 400)
        self.assertEqual(self.request("/api/v1/account/password", "POST",
            {"current_password": "incorrect", "new_password": "test-only-replacement-password"})[0], 400)
        self.assertEqual(self.request("/api/v1/account/password", "POST",
            {"current_password": self.password, "new_password": "test-only-replacement-password"})[0], 200)
        self.assertIsNone(self.app.auth.current(self.token))
        self.assertTrue(self.app.auth.login("admin", "test-only-replacement-password", "fixture"))

    def test_login_failures_are_throttled_and_password_hash_is_not_plaintext(self):
        for _ in range(10):
            with self.assertRaises(AuthError):
                self.app.auth.login("admin", "incorrect", "brute-force")
        with self.assertRaises(AuthError) as caught:
            self.app.auth.login("admin", self.password, "brute-force")
        self.assertEqual(caught.exception.status, 429)
        with self.app.store.connection() as c:
            hashed = c.execute("SELECT password_hash FROM swarmtenderr_users").fetchone()[0]
        self.assertNotIn(self.password, hashed)
        self.assertTrue(hashed.startswith("pbkdf2_sha256$"))

    def test_csrf_and_origin_protect_all_settings_writes(self):
        body = {"revision": 0, "changes": {"timezone": "America/New_York"}}
        self.assertEqual(self.request("/api/v1/settings", "PATCH", body, HTTP_X_CSRF_TOKEN="bad")[0], 403)
        self.assertEqual(self.request("/api/v1/settings", "PATCH", body, HTTP_ORIGIN="https://untrusted.example")[0], 403)
        self.assertEqual(self.app.runtime.values()["timezone"], "UTC")

    def test_settings_persist_are_encrypted_and_detect_conflicts(self):
        changes = {"timezone": "America/New_York", "poll_seconds": 7, "qbit_password": "test-only-integration-password"}
        code, data, _ = self.request("/api/v1/settings", "PATCH", {"revision": 0, "changes": changes})
        self.assertEqual(code, 200, data)
        self.assertTrue(data["secrets"]["qbit_password"])
        self.assertNotIn("test-only-integration-password", json.dumps(data))
        self.assertEqual(self.request("/api/v1/settings", "PATCH", {"revision": 0, "changes": {"poll_seconds": 9}})[0], 409)
        reopened = create_app(self.settings, False)
        self.assertEqual(reopened.runtime.values()["poll_seconds"], 7)
        self.assertEqual(reopened.runtime.values()["qbit_password"], "test-only-integration-password")
        with self.app.store.connection() as c:
            raw = c.execute("SELECT value FROM swarmtenderr_configuration WHERE key='qbit_password'").fetchone()[0]
        self.assertNotIn("test-only-integration-password", raw)
        code, data, _ = self.request("/api/v1/settings/reset", "POST", {"revision": 1, "keys": ["poll_seconds"]})
        self.assertEqual(code, 200)
        self.assertEqual(data["values"]["poll_seconds"], 3)
        self.assertEqual(data["sources"]["poll_seconds"], "deployment")

    def test_deployment_locks_and_validation(self):
        from dataclasses import replace
        locked = create_app(replace(self.settings, locked_settings=("poll_seconds",)), False)
        with self.assertRaises(PermissionError):
            locked.runtime.save({"poll_seconds": 5}, 0, "admin")
        for change in ({"timezone": "No/SuchZone"}, {"poll_seconds": 0}, {"qbit_url": "https://user:secret@example.org"},
                       {"policy_mode": "enforce"}, {"sso_default_role": "admin"}):
            with self.assertRaises(ValueError):
                self.app.runtime.save(change, 0, "admin")

    def test_audit_precedes_mutation_and_uncertain_result_is_not_retried(self):
        self.app.runtime.save({"controls_enabled": True, "qbit_password": "test-only-integration-password"}, 0, "admin")
        with patch.object(self.app.observer, "get_client") as get:
            get.return_value.action.side_effect = UpstreamError("timeout")
            code, result, _ = self.request(f"/api/v1/torrents/{HASH}/actions", "POST", {"action": "resume"})
            self.assertEqual(code, 502)
            get.return_value.action.assert_called_once_with(HASH, "resume")
            self.assertEqual(self.app.store.history()["actions"][0]["outcome"], "uncertain")
        with patch.object(self.app.store, "begin_action", side_effect=OSError("private-database-error")), patch.object(self.app.observer, "get_client") as get:
            self.assertEqual(self.request(f"/api/v1/torrents/{HASH}/actions", "POST", {"action": "resume"})[0], 503)
            get.assert_not_called()

    def test_sso_binding_does_not_link_email_to_local_admin(self):
        config = self.app.runtime.values() | {"sso_issuer": "https://issuer.example/application/o/swarmtenderr/", "sso_default_role": "viewer"}
        token = self.app.auth.sso_session({"iss": config["sso_issuer"], "sub": "new-subject",
            "email": "admin@example.org", "preferred_username": "admin"}, config)
        self.assertEqual(self.app.auth.current(token)["role"], "viewer")
        config["sso_allowed_groups"] = ["allowed"]
        with self.assertRaises(AuthError):
            self.app.auth.sso_session({"iss": config["sso_issuer"], "sub": "other", "groups": ["denied"]}, config)

    def test_csv_formula_protection_and_json_projection(self):
        now = time.time()
        rows = [{"hash": HASH, "name": "=1+1", "category": "test", "progress": 1,
                 "completion_on": now - 1, "added_on": now - 100, "size": 123, "downloaded": 123,
                 "tracker": "private-tracker", "save_path": "/private/path"}]
        self.app.store.snapshot({"observed_at": now, "download_speed": 0, "upload_speed": 0}, rows)
        code, raw, _ = self.request("/api/v1/completions.csv")
        self.assertEqual(code, 200)
        self.assertIn(b"'=1+1", raw)
        serialized = json.dumps(projection(rows, {}))
        self.assertNotIn("private-tracker", serialized)
        self.assertNotIn("/private/path", serialized)

    def test_locks_override_saved_values_and_empty_secret_input_preserves_sso(self):
        from dataclasses import replace
        from swarmtenderr.runtime import Runtime
        self.app.runtime.save({'timezone': 'America/New_York', 'sso_issuer': 'https://identity.example/application/o/swarm/',
            'sso_client_id': 'swarm', 'sso_client_secret': 'test-client-secret', 'sso_enabled': True}, 0, 'fixture')
        self.app.runtime.save({'sso_client_secret': '', 'sso_label': 'Sign in with Authentik'}, 1, 'fixture')
        self.assertEqual(self.app.runtime.values()['sso_client_secret'], 'test-client-secret')
        locked = Runtime(replace(self.settings, locked_settings=('timezone',)), self.app.store, self.app.vault)
        self.assertEqual(locked.values()['timezone'], 'UTC')
        self.assertEqual(locked.public()['sources']['timezone'], 'deployment')
        with self.assertRaises(PermissionError):
            locked.save({'timezone': 'Europe/London'}, 2, 'fixture')

    def test_sso_access_policy_changes_revoke_sso_sessions_and_preserve_local_recovery(self):
        config = self.app.runtime.values() | {'sso_issuer': 'https://identity.example/application/o/swarm/'}
        token = self.app.auth.sso_session({'iss': config['sso_issuer'], 'sub': 'a-subject'}, config)
        sso_user = self.app.auth.current(token)
        self.app.auth.update_user(sso_user['id'], {'role': 'admin'}, 'fixture')
        local = self.app.auth.current(self.token)
        with self.assertRaises(ValueError):
            self.app.auth.update_user(local['id'], {'active': False}, 'fixture')
        token = self.app.auth.sso_session({'iss': config['sso_issuer'], 'sub': 'a-subject'}, config)
        self.app.runtime.save({'sso_allowed_groups': ['media']}, 0, 'fixture')
        self.assertIsNone(self.app.auth.current(token))
        self.assertIsNotNone(self.app.auth.current(self.token))

    def test_demo_cannot_mix_with_production_database(self):
        from dataclasses import replace
        with self.assertRaises(ValueError):
            create_app(replace(self.settings, demo=True), False)
        path = Path(self.temp.name)/'isolated-demo'
        demo = replace(self.settings, demo=True, database='sqlite:///'+str(path/'state.db'), data_dir=str(path))
        create_app(demo, False)
        with self.assertRaises(ValueError):
            create_app(replace(demo, demo=False), False)

    def test_history_category_filters_and_period_comparison_are_real_data(self):
        now = time.time()
        rows = [{'hash': f'{i:040x}', 'name': 'Linux', 'progress': 1, 'size': 100,
                 'added_on': now-1000, 'completion_on': now-10, 'category': category} for i, category in enumerate(('linux',''))]
        self.app.store.snapshot({'observed_at': now, 'download_speed': 0, 'upload_speed': 0}, rows)
        code, data, _ = self.request('/api/v1/completions?uncategorized=1')
        self.assertEqual(code, 200)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['rows'][0]['category'], '')
        self.assertEqual(self.request('/api/v1/completions?category=linux')[1]['total'], 1)
        statistics = self.request('/api/v1/statistics')[1]
        self.assertEqual(statistics['month']['count'], 2)
        self.assertEqual(sum(category['count'] for category in statistics['categories']), 2)
        self.assertEqual(statistics['comparison']['previous_day'], statistics['daily'][-2])

    def test_missing_runtime_key_never_silently_replaces_encrypted_settings(self):
        self.app.runtime.save({'qbit_password': 'private-test-value'}, 0, 'fixture')
        key = Path(self.settings.data_dir)/'secret.key'
        key.unlink()
        with self.assertRaises(ValueError):
            create_app(self.settings, False)
        self.assertFalse(key.exists())


class AccountingTests(unittest.TestCase):
    def test_calendar_dedup_removed_torrents_restart_and_dst(self):
        with tempfile.TemporaryDirectory() as directory:
            url = database_url(self, Path(directory) / "state.db")
            store = Store(url)
            store.initialize()
            now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()
            times = [datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc).timestamp() for hour in (5, 6)]
            rows = [{"hash": f"{i:040x}", "name": "Download", "category": "linux", "progress": 1,
                     "completion_on": stamp, "added_on": stamp - 500, "size": 1024, "downloaded": 1024}
                    for i, stamp in enumerate(times)]
            data = {"observed_at": now, "download_speed": 0, "upload_speed": 0}
            for _ in range(3):
                store.snapshot(data, rows)
            store.snapshot(data, [])
            store = Store(url)
            store.initialize()
            stats = Analytics(store).summary("America/New_York", now)
            self.assertEqual(stats["total_completed"], 2)
            self.assertEqual(stats["month"]["count"], 2)
            self.assertEqual(next(day["count"] for day in stats["daily"] if day["period"] == "2026-11-01"), 2)
            self.assertIsNone(stats["daily"][0]["count"])
            self.assertEqual(store.completions()["total"], 2)
            rows[0]["completion_on"] += 30
            store.snapshot(data, rows)
            self.assertEqual(store.completions()["total"], 2)

    def test_v1_migration_preserves_existing_data_and_refuses_future_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            url = database_url(self, Path(directory) / 'state.db')
            store = Store(url)
            with store.connection() as c:
                c.execute("CREATE TABLE swarmtenderr_schema (version INTEGER PRIMARY KEY)")
                c.execute("INSERT INTO swarmtenderr_schema VALUES (1)")
                c.execute("CREATE TABLE swarmtenderr_actions (id TEXT PRIMARY KEY, requested REAL, torrent TEXT, action TEXT, outcome TEXT)")
                c.execute("INSERT INTO swarmtenderr_actions VALUES ('old',1,'hash','pause','accepted')")
            store.initialize()
            self.assertEqual(store.history()["actions"][0]["id"], "old")
            with store.connection() as c:
                c.execute("UPDATE swarmtenderr_schema SET version=999")
            with self.assertRaises(ValueError):
                store.initialize()

class CompletionSessionTests(unittest.TestCase):
    def test_readded_existing_files_do_not_count_without_new_download_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(database_url(self, Path(directory)/'state.db'))
            store.initialize()
            now = time.time() - 120
            def observe(at, rows, origin='http://client-a:8080'):
                store.snapshot({'observed_at': at, 'download_speed': 0, 'upload_speed': 0}, rows, client_origin=origin)
            observe(now, [])
            completed = {'hash': HASH, 'name': 'Linux', 'progress': 1, 'size': 1024, 'downloaded': 1024,
                         'added_on': now+1, 'completion_on': now+10}
            observe(now+12, [completed])
            self.assertEqual(store.completions()['total'], 1)
            readded = completed | {'added_on': now+20, 'completion_on': now+25}
            observe(now+30, [readded])
            self.assertEqual(store.completions()['total'], 1)
            new = completed | {'added_on': now+40, 'progress': .1, 'downloaded': 0, 'completion_on': 0}
            observe(now+41, [new])
            observe(now+80, [new | {'progress': 1, 'downloaded': 1024, 'completion_on': now+70}])
            self.assertEqual(store.completions()['total'], 2)
            # Switching actual connection origins keeps separate stable clients.
            observe(now+90, [completed], 'http://client-b:8080')
            self.assertEqual(store.completions()['total'], 3)
            observe(now+100, [])
            self.assertEqual(store.completions()['total'], 3)
    def test_final_download_counter_can_arrive_after_complete_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(database_url(self, Path(directory)/'state.db'))
            store.initialize()
            now = time.time() - 120
            row = {'hash': HASH, 'name': 'Linux', 'progress': 0, 'size': 1024,
                   'downloaded': 0, 'added_on': now-1, 'completion_on': -1}
            def observe(offset, **fields):
                store.snapshot({'observed_at': now+offset, 'download_speed': 0, 'upload_speed': 0}, [row | fields])
            observe(0)
            observe(1, progress=1, completion_on=now+1)
            self.assertEqual(store.completions()['total'], 0)
            observe(2, progress=1, completion_on=now+1, downloaded=1024)
            self.assertEqual(store.completions()['total'], 1)
            observe(3, progress=1, completion_on=now+1, downloaded=1024)
            self.assertEqual(store.completions()['total'], 1)

    def test_old_completion_rechecked_during_first_connection_can_backfill_later(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(database_url(self, Path(directory)/'state.db'))
            store.initialize()
            now = time.time()
            row = {'hash': HASH, 'name': 'Linux', 'progress': .5, 'size': 1024,
                   'downloaded': 1024, 'added_on': now-1000, 'completion_on': now-500}
            data = {'observed_at': now, 'download_speed': 0, 'upload_speed': 0}
            store.snapshot(data, [row])
            self.assertEqual(store.completions()['total'], 0)
            store.snapshot(data, [row | {'progress': 1}])
            self.assertEqual(store.completions()['total'], 1)
            self.assertEqual(store.completions()['rows'][0]['source'], 'backfill')


class LargeHistoryTests(unittest.TestCase):
    def test_ten_thousand_torrent_observation_is_batched_and_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(database_url(self, Path(directory)/'state.db'))
            store.initialize()
            now = time.time()
            rows = [{'hash': f'{i:040x}', 'name': f'Linux {i}', 'category': 'linux', 'progress': 1,
                     'downloaded': 1024, 'size': 1024, 'added_on': now-600-i, 'completion_on': now-10} for i in range(10000)]
            data = {'observed_at': now, 'download_speed': 0, 'upload_speed': 0}
            store.snapshot(data, rows)
            store.snapshot(data, rows)
            result = store.completions(limit=100)
            self.assertEqual(result['total'], 10000)
            self.assertEqual(len(result['rows']), 100)
            self.assertEqual(Analytics(store).summary('UTC')['total_bytes'], 10240000)
            # Same SQL backend and identity set remain idempotent after reopening.
            reopened = Store('sqlite:///' + directory + '/state.db') if not store.postgres else Store(store.url)
            self.assertEqual(reopened.completions()['total'], 10000)
            with store.connection() as connection:
                store.execute(connection, "INSERT INTO swarmtenderr_completions VALUES ('extra','fixture','extra','Linux','linux',1,2,100,3,'observed')")
            settings = Settings(database=store.url if store.postgres else 'sqlite:///'+store.url,
                data_dir=directory, app_password='test-only-large-export-password')
            app = create_app(settings, False)
            token = app.auth.login('admin','test-only-large-export-password','fixture')
            response=[]
            body=b''.join(app({'PATH_INFO':'/api/v1/completions.csv','REQUEST_METHOD':'GET','QUERY_STRING':'',
                'HTTP_COOKIE':'swarmtenderr_session='+token},lambda status,headers:response.append(status)))
            self.assertEqual(response[0],'200 OK')
            import csv
            self.assertEqual(len(list(csv.reader(io.StringIO(body.decode())))),10001)
