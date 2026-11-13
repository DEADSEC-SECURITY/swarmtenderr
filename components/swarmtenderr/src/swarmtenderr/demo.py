"""Explicit synthetic demo history; never invoked for a real installation."""
from datetime import datetime, timedelta, timezone
import hashlib
import time


def seed_history(store):
    with store.connection(serialize=True) as c:
        if store.meta("demo_seeded", False, c):
            return
        today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        names = ["Debian installation image", "Ubuntu Desktop", "Blender open movie",
                 "OpenStreetMap export", "Fedora Workstation", "Public-domain archive"]
        for day in range(365):
            count = (day * 7 + 3) % 6
            for n in range(count):
                completed = (today - timedelta(days=day, hours=n)).timestamp()
                if completed > time.time():
                    completed = time.time() - n * 60
                identity = hashlib.sha256(f"demo:{day}:{n}".encode()).hexdigest()
                store.execute(c, "INSERT INTO swarmtenderr_completions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (identity, "demo", identity[:40], names[(day + n) % len(names)],
                     "linux" if n % 2 else "open-media", completed - 5400, completed,
                     (1 + (day + n) % 12) * 1024**3, time.time(), "demo"))
        store.set_meta(c, "collection_started", (today - timedelta(days=365)).timestamp())
        store.set_meta(c, "demo_seeded", True)
