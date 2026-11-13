"""Versioned SQL storage, transactional writes, audit and completion records."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, url):
        self.postgres = url.startswith(("postgresql://", "postgres://"))
        if self.postgres:
            self.url = url
        elif url.startswith("sqlite:///"):
            self.url = url[len("sqlite:///"):]
            if not self.url or self.url == ":memory:":
                raise ValueError("SQLite requires a persistent file")
            Path(self.url).parent.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError("Use sqlite:/// or postgresql://")

    @contextmanager
    def connection(self, serialize=False, consistent=False):
        if self.postgres:
            import psycopg
            connection = psycopg.connect(self.url, connect_timeout=5)
        else:
            connection = sqlite3.connect(self.url, timeout=10)
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
        try:
            if serialize:
                if self.postgres:
                    connection.execute("SELECT pg_advisory_xact_lock(710342907)")
                else:
                    connection.execute("BEGIN IMMEDIATE")
            elif consistent:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" if self.postgres else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, connection, sql, parameters=()):
        return connection.execute(sql.replace("?", "%s") if self.postgres else sql, parameters)

    @staticmethod
    def objects(cursor):
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def initialize(self):
        with self.connection(serialize=True) as c:
            self.execute(c, "CREATE TABLE IF NOT EXISTS swarmtenderr_schema (version INTEGER PRIMARY KEY)")
            versions = self.execute(c, "SELECT version FROM swarmtenderr_schema").fetchall()
            if versions not in ([], [(1,)], [(2,)]):
                raise ValueError("Unsupported database schema")
            for sql in (
                "CREATE TABLE IF NOT EXISTS swarmtenderr_snapshot (id INTEGER PRIMARY KEY, observed DOUBLE PRECISION NOT NULL, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_samples (id TEXT PRIMARY KEY, observed DOUBLE PRECISION NOT NULL, download DOUBLE PRECISION NOT NULL, upload DOUBLE PRECISION NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_actions (id TEXT PRIMARY KEY, requested DOUBLE PRECISION NOT NULL, torrent TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_configuration (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL, active INTEGER NOT NULL, created DOUBLE PRECISION NOT NULL, last_login DOUBLE PRECISION NOT NULL, UNIQUE (issuer, subject))",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_sessions (digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, expires DOUBLE PRECISION NOT NULL, seen DOUBLE PRECISION NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_login_attempts (identity TEXT PRIMARY KEY, started DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_audit (id TEXT PRIMARY KEY, at DOUBLE PRECISION NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_completions (id TEXT PRIMARY KEY, client TEXT NOT NULL, hash TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL, added DOUBLE PRECISION NOT NULL, completed DOUBLE PRECISION NOT NULL, bytes DOUBLE PRECISION NOT NULL, observed DOUBLE PRECISION NOT NULL, source TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_controller (id INTEGER PRIMARY KEY, owner TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_download_sessions (id TEXT PRIMARY KEY, downloaded DOUBLE PRECISION NOT NULL, progress DOUBLE PRECISION NOT NULL, moved DOUBLE PRECISION NOT NULL, seen DOUBLE PRECISION NOT NULL)",
                "CREATE TABLE IF NOT EXISTS swarmtenderr_policy (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS swarmtenderr_sample_time ON swarmtenderr_samples (observed)",
                "CREATE INDEX IF NOT EXISTS swarmtenderr_action_time ON swarmtenderr_actions (requested)",
                "CREATE INDEX IF NOT EXISTS swarmtenderr_completion_identity ON swarmtenderr_completions (client, hash)",
                "CREATE INDEX IF NOT EXISTS swarmtenderr_completion_time ON swarmtenderr_completions (completed)",
                "CREATE INDEX IF NOT EXISTS swarmtenderr_audit_time ON swarmtenderr_audit (at)",
            ):
                self.execute(c, sql)
            self.execute(c, "INSERT INTO swarmtenderr_meta VALUES ('config_revision', '0') ON CONFLICT DO NOTHING")
            self.execute(c, "INSERT INTO swarmtenderr_meta VALUES ('client_id', ?) ON CONFLICT DO NOTHING", (json.dumps(uuid.uuid4().hex),))
            self.execute(c, "DELETE FROM swarmtenderr_schema")
            self.execute(c, "INSERT INTO swarmtenderr_schema VALUES (2)")

    def claim_mode(self, demo):
        expected = "demo" if demo else "production"
        with self.connection(serialize=True) as c:
            mode = self.meta("installation_mode", None, c)
            if mode is None:
                snapshot = self.execute(c, "SELECT payload FROM swarmtenderr_snapshot WHERE id=1").fetchone()
                users = self.execute(c, "SELECT COUNT(*) FROM swarmtenderr_users").fetchone()[0]
                if snapshot:
                    mode = "demo" if json.loads(snapshot[0]).get("demo") else "production"
                elif users:
                    mode = "production"
            if mode is not None and mode != expected:
                raise ValueError("Demo and production require separate databases and data volumes")
            self.set_meta(c, "installation_mode", expected)

    def meta(self, key, default=None, connection=None):
        if connection is None:
            with self.connection() as c:
                return self.meta(key, default, c)
        row = self.execute(connection, "SELECT value FROM swarmtenderr_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, c, key, value):
        self.execute(c, "INSERT INTO swarmtenderr_meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def config(self, include_policy=False):
        with self.connection(consistent=True) as c:
            rows = self.execute(c, "SELECT key, value FROM swarmtenderr_configuration").fetchall()
            result = (self.meta("config_revision", 0, c), {key: json.loads(value) for key, value in rows})
            if include_policy:
                result += (self.meta("policy_revision", 0, c), self.meta("policy_ack_revision", 0, c))
            return result

    def save_config(self, changes, reset, revision, actor):
        with self.connection(serialize=True) as c:
            if self.meta("config_revision", 0, c) != revision:
                raise Conflict("Settings changed in another session. Reload before saving.")
            for key, value in changes.items():
                self.execute(c, "INSERT INTO swarmtenderr_configuration VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            for key in reset:
                self.execute(c, "DELETE FROM swarmtenderr_configuration WHERE key=?", (key,))
            if (set(changes) | set(reset)) & {"sso_enabled", "sso_issuer", "sso_client_id", "sso_client_secret", "sso_allowed_groups"}:
                self.execute(c, "DELETE FROM swarmtenderr_sessions WHERE user_id IN (SELECT id FROM swarmtenderr_users WHERE issuer <> '')")
            policy_keys = {"policy_mode", "managed_categories", "excluded_tags", "download_slots", "minimum_free_gib", "work_budget_gib", "library_reserve_gib"}
            if (set(changes) | set(reset)) & policy_keys:
                self.set_meta(c, "policy_revision", self.meta("policy_revision", 0, c) + 1)
            if "policy_mode" in set(changes) | set(reset):
                self.set_meta(c, "policy_ack_revision", self.meta("policy_ack_revision", 0, c) + 1)
            self.set_meta(c, "config_revision", revision + 1)
            self.audit("settings.updated", actor, {"keys": sorted(set(changes) | set(reset))}, c)
        return revision + 1

    def audit(self, event, actor, detail, connection=None):
        if connection is None:
            with self.connection(serialize=True) as c:
                return self.audit(event, actor, detail, c)
        self.set_meta(connection, "activity_revision", self.meta("activity_revision", 0, connection) + 1)
        self.execute(connection, "INSERT INTO swarmtenderr_audit VALUES (?, ?, ?, ?, ?)", (uuid.uuid4().hex, time.time(), actor, event, json.dumps(detail, allow_nan=False)))

    def snapshot(self, data, raw=None, retention_days=30, client_origin=""):
        now = data["observed_at"]
        with self.connection(serialize=True) as c:
            initial = self.meta("collection_started", None, c) is None
            if initial:
                self.set_meta(c, "collection_started", now)
            self.execute(c, "INSERT INTO swarmtenderr_snapshot VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET observed=excluded.observed, payload=excluded.payload", (now, json.dumps(data, allow_nan=False)))
            latest = self.execute(c, "SELECT MAX(observed) FROM swarmtenderr_samples").fetchone()[0]
            if latest is None or now - latest >= 30:
                self.execute(c, "INSERT INTO swarmtenderr_samples VALUES (?, ?, ?, ?)", (uuid.uuid4().hex, now, data["download_speed"], data["upload_speed"]))
            self.execute(c, "DELETE FROM swarmtenderr_samples WHERE observed < ?", (now - retention_days * 86400,))
            self.set_meta(c, "observed_client_origin", client_origin)
            client = self.meta("client_id", "default", c)
            if client_origin:
                clients = self.meta("clients", {}, c)
                if client_origin not in clients:
                    clients[client_origin] = client if not clients else uuid.uuid4().hex
                    self.set_meta(c, "clients", clients)
                client = clients[client_origin]
            client_started = self.meta("client_started:" + client, None, c)
            initial_client = client_started is None
            if initial_client:
                client_started = now
                self.set_meta(c, "client_started:" + client, now)
            keyed = {}
            for row in raw or []:
                added = row.get('added_on', 0)
                if isinstance(added, (int, float)) and 0 <= added <= now + 60:
                    identity = hashlib.sha256(f"{client}:{row.get('hash')}:{added}".encode()).hexdigest()
                    keyed[identity] = row
            previous_sessions, known_completions, repeated_hashes = {}, set(), set()
            keys = list(keyed)
            for offset in range(0, len(keys), 500):
                group = keys[offset:offset+500]
                placeholders = ','.join('?' for _ in group)
                previous_sessions.update({record[0]: record[1:] for record in self.execute(c, 'SELECT id, downloaded, progress, moved FROM swarmtenderr_download_sessions WHERE id IN ('+placeholders+')', group).fetchall()})
                known_completions.update(record[0] for record in self.execute(c, 'SELECT id FROM swarmtenderr_completions WHERE id IN ('+placeholders+')', group).fetchall())
                hashes = [str(keyed[key].get('hash', ''))[:64] for key in group]
                repeated_hashes.update(record[0] for record in self.execute(c, 'SELECT DISTINCT hash FROM swarmtenderr_completions WHERE client=? AND hash IN ('+placeholders+')', (client, *hashes)).fetchall())
            saved_sessions, new_completions = [], []
            for row in raw or []:
                completed, added = row.get("completion_on", 0), row.get("added_on", 0)
                if not isinstance(added, (int, float)) or not 0 <= added <= now + 60:
                    continue
                identity = hashlib.sha256(f"{client}:{row.get('hash')}:{added}".encode()).hexdigest()
                if row.get('progress', 0) >= 1 and identity in known_completions:
                    continue
                previous = previous_sessions.get(identity)
                downloaded, progress = max(0, row.get("downloaded", 0)), row.get("progress", 0)
                moved = (previous[2] + max(0, downloaded - previous[0])) if previous and previous[1] < 1 else previous[2] if previous else 0
                # qBittorrent can report progress=1 before its downloaded-byte
                # counter catches up. Retain incomplete-session evidence across
                # those separately refreshed fields; rechecks without new bytes
                # still contribute zero and cannot create repeat completions.
                minimum_progress = min(previous[1], progress) if previous else progress
                saved_sessions.append((identity, downloaded, minimum_progress, moved, now))
                if not isinstance(completed, (int, float)) or not 0 < completed <= now + 60 or progress < 1:
                    continue
                repeated = str(row.get("hash", ""))[:64] in repeated_hashes
                # Existing-file re-additions/rechecks must not manufacture another
                # completion. A repeat needs bytes observed during that addition.
                native_new = not repeated and downloaded > 0 and client_started <= added <= completed
                historical = not repeated and added < client_started and completed < client_started
                if not initial_client and not (moved > 0 or native_new or historical):
                    continue
                new_completions.append((identity, client, str(row.get("hash", ""))[:64], str(row.get("name", ""))[:300],
                     str(row.get("category", ""))[:80], added, completed, max(0, row.get("size", 0)), now,
                     "backfill" if completed < client_started else "observed"))
            def batch(sql, records):
                if not records:
                    return 0
                cursor = c.cursor()
                try:
                    cursor.executemany(sql.replace('?', '%s') if self.postgres else sql, records)
                    return cursor.rowcount
                finally:
                    cursor.close()
            batch("INSERT INTO swarmtenderr_download_sessions VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET downloaded=excluded.downloaded, progress=excluded.progress, moved=excluded.moved, seen=excluded.seen", saved_sessions)
            changed = batch("INSERT INTO swarmtenderr_completions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", new_completions)
            self.execute(c, "DELETE FROM swarmtenderr_download_sessions WHERE seen<?", (now - 30*86400,))
            if changed:
                self.set_meta(c, "completion_revision", self.meta("completion_revision", 0, c) + 1)

    def read(self):
        with self.connection() as c:
            row = self.execute(c, "SELECT payload FROM swarmtenderr_snapshot WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def begin_action(self, torrent, action, actor="system"):
        identity = uuid.uuid4().hex
        with self.connection(serialize=True) as c:
            self.execute(c, "INSERT INTO swarmtenderr_actions VALUES (?, ?, ?, ?, ?)", (identity, time.time(), torrent, action, "pending"))
            self.audit("torrent.requested", actor, {"id": identity, "torrent": torrent, "action": action}, c)
        return identity

    def finish_action(self, identity, outcome):
        with self.connection(serialize=True) as c:
            self.set_meta(c, "activity_revision", self.meta("activity_revision", 0, c) + 1)
            self.execute(c, "UPDATE swarmtenderr_actions SET outcome=? WHERE id=?", (outcome, identity))

    def confirm_actions(self, rows):
        indexed = {row.get("hash"): row for row in rows}
        with self.connection() as c:
            actions = self.execute(c, "SELECT id, requested, torrent, action FROM swarmtenderr_actions WHERE outcome='accepted'").fetchall()
            for identity, requested, torrent, action in actions:
                row = indexed.get(torrent)
                if not row or action == "reannounce":
                    continue
                stopped = row.get("state") in {"stoppedDL", "pausedDL"}
                confirmed = ((action == "pause" and stopped) or
                             (action == "resume" and not stopped) or
                             (action == "prioritize" and row.get("priority") == 1))
                outcome = "confirmed" if confirmed else "uncertain" if time.time() - requested > 120 else None
                if outcome:
                    self.execute(c, "UPDATE swarmtenderr_actions SET outcome=? WHERE id=?", (outcome, identity))

    def history(self, admin=False):
        with self.connection() as c:
            rows = self.objects(self.execute(c, "SELECT id, requested AS at, torrent, action, outcome FROM swarmtenderr_actions ORDER BY requested DESC LIMIT 100"))
            samples = self.objects(self.execute(c, "SELECT observed AS at, download, upload FROM swarmtenderr_samples ORDER BY observed DESC LIMIT 120"))
            audit = self.objects(self.execute(c, "SELECT id, at, actor, event, detail FROM swarmtenderr_audit ORDER BY at DESC LIMIT 100")) if admin else []
        for event in audit:
            event["detail"] = json.loads(event["detail"])
        return {"actions": rows, "samples": list(reversed(samples)), "audit": audit}

    def completions(self, start=0, end=None, search="", category=None, page=1, limit=50):
        clauses, parameters = ["completed>=?", "completed<=?"], [start, end or time.time() + 60]
        if search:
            clauses.append("LOWER(name) LIKE ?")
            parameters.append("%" + search.lower()[:200] + "%")
        if category is not None:
            clauses.append("category=?")
            parameters.append(category[:80])
        where = " AND ".join(clauses)
        with self.connection() as c:
            total = self.execute(c, "SELECT COUNT(*) FROM swarmtenderr_completions WHERE " + where, parameters).fetchone()[0]
            rows = self.objects(self.execute(c, "SELECT * FROM swarmtenderr_completions WHERE " + where + " ORDER BY completed DESC, id LIMIT ? OFFSET ?", (*parameters, limit, (page - 1) * limit)))
        return {"total": total, "page": page, "limit": limit, "rows": rows}

    def policy_state(self):
        with self.connection() as c:
            row = self.execute(c, "SELECT payload FROM swarmtenderr_policy WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {}

    def save_policy(self, state):
        with self.connection() as c:
            self.execute(c, "INSERT INTO swarmtenderr_policy VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(state, allow_nan=False),))

    def controller_lease(self, owner, release=False):
        """One writer per database; use the database clock across hosts."""
        with self.connection(serialize=True) as c:
            now = self.execute(c, "SELECT EXTRACT(EPOCH FROM clock_timestamp())" if self.postgres else "SELECT (julianday('now')-2440587.5)*86400").fetchone()[0]
            row = self.execute(c, "SELECT owner, expires FROM swarmtenderr_controller WHERE id=1").fetchone()
            if release:
                self.execute(c, "DELETE FROM swarmtenderr_controller WHERE id=1 AND owner=?", (owner,))
                return True
            if row and row[0] != owner and row[1] > now:
                return False
            self.execute(c, "INSERT INTO swarmtenderr_controller VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, expires=excluded.expires", (owner, now + 90))
            return True
