"""Small, explicit SQL contract shared by SQLite and PostgreSQL."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid


class Store:
    def __init__(self, url: str):
        self.postgres = url.startswith(("postgresql://", "postgres://"))
        if self.postgres:
            self.url = url
        elif url.startswith("sqlite:///"):
            self.url = url[len("sqlite:///"):]
            if not self.url or self.url == ":memory:":
                raise ValueError("SQLite requires a persistent file")
            Path(self.url).parent.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError("Database must use sqlite:/// or postgresql://")

    @contextmanager
    def connection(self):
        if self.postgres:
            import psycopg
            connection = psycopg.connect(self.url, connect_timeout=5)
        else:
            connection = sqlite3.connect(self.url, timeout=5)
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, connection, sql, parameters=()):
        return connection.execute(sql.replace("?", "%s") if self.postgres else sql, parameters)

    def initialize(self):
        with self.connection() as connection:
            self.execute(connection, "CREATE TABLE IF NOT EXISTS swarmtenderr_schema (version INTEGER PRIMARY KEY)")
            versions = self.execute(connection, "SELECT version FROM swarmtenderr_schema").fetchall()
            if versions and versions != [(1,)]:
                raise ValueError("Unsupported database schema; restore the matching application version")
            self.execute(connection, "INSERT INTO swarmtenderr_schema VALUES (1) ON CONFLICT DO NOTHING")
            self.execute(connection, "CREATE TABLE IF NOT EXISTS swarmtenderr_snapshot (id INTEGER PRIMARY KEY, observed DOUBLE PRECISION NOT NULL, payload TEXT NOT NULL)")
            self.execute(connection, "CREATE TABLE IF NOT EXISTS swarmtenderr_samples (id TEXT PRIMARY KEY, observed DOUBLE PRECISION NOT NULL, download DOUBLE PRECISION NOT NULL, upload DOUBLE PRECISION NOT NULL)")
            self.execute(connection, "CREATE TABLE IF NOT EXISTS swarmtenderr_actions (id TEXT PRIMARY KEY, requested DOUBLE PRECISION NOT NULL, torrent TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL)")
            self.execute(connection, "CREATE INDEX IF NOT EXISTS swarmtenderr_sample_time ON swarmtenderr_samples (observed)")
            self.execute(connection, "CREATE INDEX IF NOT EXISTS swarmtenderr_action_time ON swarmtenderr_actions (requested)")

    def snapshot(self, data):
        now = data["observed_at"]
        with self.connection() as connection:
            self.execute(connection, "INSERT INTO swarmtenderr_snapshot VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET observed=excluded.observed, payload=excluded.payload", (now, json.dumps(data, allow_nan=False)))
            latest = self.execute(connection, "SELECT MAX(observed) FROM swarmtenderr_samples").fetchone()[0]
            if latest is None or now - latest >= 30:
                self.execute(connection, "INSERT INTO swarmtenderr_samples VALUES (?, ?, ?, ?)", (uuid.uuid4().hex, now, data["download_speed"], data["upload_speed"]))
            self.execute(connection, "DELETE FROM swarmtenderr_samples WHERE observed < ?", (now - 30 * 86400,))

    def read(self):
        with self.connection() as connection:
            row = self.execute(connection, "SELECT payload FROM swarmtenderr_snapshot WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def begin_action(self, torrent, action):
        identity = uuid.uuid4().hex
        with self.connection() as connection:
            self.execute(connection, "INSERT INTO swarmtenderr_actions VALUES (?, ?, ?, ?, ?)", (identity, time.time(), torrent, action, "pending"))
        return identity

    def finish_action(self, identity, outcome):
        with self.connection() as connection:
            self.execute(connection, "UPDATE swarmtenderr_actions SET outcome=? WHERE id=?", (outcome, identity))

    def history(self):
        with self.connection() as connection:
            rows = self.execute(connection, "SELECT id, requested, torrent, action, outcome FROM swarmtenderr_actions ORDER BY requested DESC LIMIT 100").fetchall()
            samples = self.execute(connection, "SELECT observed, download, upload FROM swarmtenderr_samples ORDER BY observed DESC LIMIT 60").fetchall()
        return {"actions": [dict(zip(("id", "at", "torrent", "action", "outcome"), row)) for row in rows],
                "samples": [dict(zip(("at", "download", "upload"), row)) for row in reversed(samples)]}
