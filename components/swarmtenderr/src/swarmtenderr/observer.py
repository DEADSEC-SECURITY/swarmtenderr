"""Read-only polling and a narrow projection of the external optimizer contract."""

import json
import logging
import math
from pathlib import Path
import threading
import time


def number(value, default=0):
    return value if isinstance(value, (float, int)) and math.isfinite(value) else default


def text(value, limit=180):
    return "".join(c if c.isprintable() else " " for c in str(value or ""))[:limit]


def optimizer(path):
    if not path:
        return {"connected": False, "reason": "No external optimizer configured", "rows": [], "events": []}
    try:
        with Path(path).open("rb") as source:
            raw = source.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("oversize")
        state = json.loads(raw)
        snapshot = state.get("optimizer", {})
        checked = number(snapshot.get("checked_at"))
        stale = not 0 <= time.time() - checked <= 120
        rows = [{key: text(row.get(key), 300) for key in
                 ("torrent", "decision", "reason", "estimate_source")} for row in snapshot.get("rows", [])[:25]]
        capacity = {key: number(state.get("space_plan", {}).get(key)) for key in
                    ("requested_slots", "applied_slots", "ssd_budget_bytes", "ssd_reserved_bytes", "library_budget_bytes", "library_reserved_bytes")}
        events = [{"at": number(event.get("at")), "name": text(event.get("torrent")),
                   "action": text(event.get("action")), "reason": text(event.get("reason"), 300)}
                  for event in state.get("optimizer_events", [])[-50:]]
        return {"connected": True, "stale": stale, "observed_at": checked,
                "rows": rows, "capacity": capacity, "events": events}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"connected": False, "reason": "Optimizer snapshot unavailable", "rows": [], "events": []}


def projection(rows, engine):
    decisions = {row["torrent"]: row for row in engine.get("rows", [])}
    result = []
    for row in rows:
        identity = text(row.get("hash"), 64)
        decision = decisions.get(identity, {})
        progress = max(0, min(1, number(row.get("progress"))))
        state = text(row.get("state"), 32)
        tags = set(str(row.get("tags", "")).replace(" ", "").split(","))
        protected = (progress >= 1 or row.get("force_start", False) or state == "forcedDL"
                     or bool(tags & {"apollo-keep", "swarmtenderr-keep"}))
        speed = max(0, number(row.get("dlspeed")))
        remaining = max(0, number(row.get("amount_left")))
        # Unknown metadata or a queued torrent has no reliable finish time.
        eta = remaining / speed if speed > 0 and remaining > 0 and state == "downloading" else None
        result.append({"hash": identity, "name": text(row.get("name")), "category": text(row.get("category"), 40),
            "state": state, "progress": progress, "download_speed": speed,
            "upload_speed": max(0, number(row.get("upspeed"))), "remaining_bytes": remaining,
            "size": max(0, number(row.get("size"))), "seeds": max(0, number(row.get("num_seeds"))),
            "peers": max(0, number(row.get("num_leechs"))), "priority": number(row.get("priority"), -1),
            "eta_seconds": eta, "protected": protected,
            "decision": decision.get("decision", "Protected" if protected else "Native queue"),
            "reason": decision.get("reason", "Seeding or manual protection" if protected else "Managed by qBittorrent; no automatic policy attached")})
    return sorted(result, key=lambda row: (row["protected"], row["priority"] < 0, row["priority"], row["name"]))


def demo_rows():
    names = ["Debian 13.0 amd64 DVD", "Ubuntu 26.04 Desktop", "Blender Open Movies collection",
             "Internet Archive public domain films", "Fedora Workstation Live", "OpenStreetMap weekly planet"]
    states = ["downloading", "downloading", "metaDL", "queuedDL", "stalledDL", "uploading"]
    return [{"hash": f"{index + 1:040x}", "name": name, "category": "linux" if index < 2 else "open-media",
             "state": states[index], "progress": [.86, .42, 0, .12, .31, 1][index],
             "amount_left": [600, 2400, 0, 7200, 4200, 0][index] * 1024**2,
             "size": 5 * 1024**3 if index != 2 else 0,
             "dlspeed": [8.4, 3.2, 0, 0, 0, 0][index] * 1024**2,
             "upspeed": [0, 0, 0, 0, 0, 1.8][index] * 1024**2,
             "num_seeds": [42, 28, 0, 12, 0, 65][index], "num_leechs": index + 2,
             "priority": index + 1} for index, name in enumerate(names)]


class Observer:
    def __init__(self, settings, store, client):
        self.settings, self.store, self.client = settings, store, client
        self.last_error = ""
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def poll(self):
        with self.lock:
            rows = demo_rows() if self.settings.demo else self.client.torrents()
            engine = optimizer(self.settings.optimizer_state)
            torrents = projection(rows, engine)
            data = {"observed_at": time.time(), "demo": self.settings.demo,
                    "controls": self.settings.controls, "optimizer": engine, "torrents": torrents,
                    "download_speed": sum(row["download_speed"] for row in torrents),
                    "upload_speed": sum(row["upload_speed"] for row in torrents)}
            self.store.snapshot(data)
            self.last_error = ""
            return data

    def run(self):
        while not self.stop.is_set():
            try:
                self.poll()
            except Exception:
                # Exceptions may carry SQL URLs or filesystem paths. Never log their values.
                self.last_error = "Polling failed; showing the last successful observation"
                logging.getLogger("swarmtenderr").warning("Observer poll failed")
            self.stop.wait(self.settings.poll_seconds)

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True, name="swarmtenderr-observer")
        thread.start()
        return thread
