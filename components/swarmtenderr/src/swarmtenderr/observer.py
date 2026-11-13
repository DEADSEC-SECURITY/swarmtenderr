"""Read-only polling and a narrow projection of the external optimizer contract."""

import json
import hashlib
import logging
import math
from pathlib import Path
import threading
import time
from swarmtenderr_policy.explanations import sanitize


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
                "rows": rows, "capacity": capacity, "events": events,
                "rankings": sanitize(snapshot.get("rankings", [])),
                "ranking_total": number(snapshot.get("ranking_total")),
                "settings_revision": number(state.get("optimizer_settings_revision"))}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"connected": False, "reason": "Optimizer snapshot unavailable", "rows": [], "events": []}


def projection(rows, engine):
    decisions = {row["torrent"]: row for row in engine.get("rows", [])}
    rankings = {row["torrent"]: row for row in sanitize(engine.get("rankings", []))}
    result = []
    for row in rows:
        identity = text(row.get("hash"), 64)
        explanation = rankings.get(identity)
        decision = explanation or decisions.get(identity, {})
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
            "ranking": explanation,
            "trial_until": number(decision.get("trial_until")), "cooldown_until": number(decision.get("cooldown_until")),
            "estimate_source": text(decision.get("estimate_source"), 60),
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
    def __init__(self, settings, store, client, runtime=None):
        self.settings, self.store, self.client = settings, store, client
        self.runtime = runtime
        self.client_identity = None
        self.wake = threading.Event()
        from .controller import Controller
        self.controller = Controller(settings, store)
        self.last_error = ""
        self.demo_policy = {}
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def get_client(self):
        if self.runtime is None:
            return self.client
        from .qbit import Qbit, UpstreamError
        config = self.runtime.values()
        if not config['qbit_url'] or not config['qbit_password']:
            raise UpstreamError('Configure the qBittorrent connection in Settings')
        identity = (config['qbit_url'], config['qbit_username'], hashlib.sha256(config['qbit_password'].encode()).hexdigest())
        if identity != self.client_identity:
            self.client = Qbit(config['qbit_url'], config['qbit_username'], password=config['qbit_password'])
            self.client_identity = identity
        return self.client

    def poll(self):
        with self.lock:
            config = self.runtime.values() if self.runtime else {"controls_enabled": self.settings.controls, "retention_days": 30}
            rows = demo_rows() if self.settings.demo else self.get_client().torrents()
            if self.settings.optimizer_state:
                engine = optimizer(self.settings.optimizer_state)
            elif self.settings.demo:
                from swarmtenderr_policy import admission, explanations
                from swarmtenderr_policy.tuning import DEFAULT_POLICY
                now = time.time()
                sample = {row['hash']: row for row in rows}
                owned = {key: {} for key, row in sample.items() if row.get('progress', 1) < 1}
                admission.observe(sample, self.demo_policy.setdefault('history', {}), now)
                plan = admission.queue_plan(sample, owned, self.demo_policy['history'], now)
                rankings, total = explanations.build(self.demo_policy, sample, owned, plan, now, admission, policy=DEFAULT_POLICY)
                engine = {"connected": True, "owner": "demo", "mode": "observe", "stale": False,
                    "reason": "Demo recommendations; automatic optimization is disabled", "rows": [], "events": [],
                    "observed_at": now, "rankings": rankings, "ranking_total": total}
            else:
                try:
                    engine = self.controller.poll(self.get_client(), rows, config)
                except Exception:
                    engine = {"connected": False, "reason": "Policy evaluation failed; check native queue settings, capacity and pending requests in Activity", "rows": [], "events": []}
            torrents = projection(rows, engine)
            public_engine = {key: value for key, value in engine.items() if key != 'rankings'}
            public_engine['ranking_count'] = len(engine.get('rankings', []))
            data = {"observed_at": time.time(), "demo": self.settings.demo,
                    "controls": config["controls_enabled"], "optimizer": public_engine, "torrents": torrents,
                    "download_speed": sum(row["download_speed"] for row in torrents),
                    "upload_speed": sum(row["upload_speed"] for row in torrents)}
            self.store.snapshot(data, rows, config["retention_days"], config.get("qbit_url", ""))
            self.store.confirm_actions(rows)
            self.last_error = ""
            return data

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    self.poll()
                except Exception:
                    previous = self.last_error
                    try:
                        configured = self.settings.demo or (self.runtime and self.runtime.values()["qbit_url"])
                    except Exception:
                        configured = True
                    self.last_error = "Polling failed; showing the last successful observation" if configured else "Configure the qBittorrent connection in Settings to begin monitoring."
                    if self.last_error != previous and configured:
                        logging.getLogger("swarmtenderr").warning("Observer poll failed")
                try:
                    interval = self.runtime.values()["poll_seconds"] if self.runtime else self.settings.poll_seconds
                except Exception:
                    interval = 5
                self.wake.wait(interval)
                self.wake.clear()
        finally:
            try:
                self.store.controller_lease(self.controller.owner, release=True)
            except Exception:
                pass  # A crashed/unreachable database lease expires after 90s.

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True, name="swarmtenderr-observer")
        thread.start()
        return thread
