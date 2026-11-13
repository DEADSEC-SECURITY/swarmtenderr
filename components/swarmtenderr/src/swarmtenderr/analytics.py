"""Completion accounting using native timestamps and calendar boundaries."""
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo


class Analytics:
    def __init__(self, store):
        self.store = store
        self.cached = None
        self.cache_key = None
        self.cached_at = 0

    def summary(self, timezone, now=None):
        current = time.time() if now is None else now
        key = (timezone, int(current // 10), self.store.meta("completion_revision", 0))
        if self.cached is not None and self.cache_key == key:
            return self.cached
        zone = ZoneInfo(timezone)
        today = datetime.fromtimestamp(current, zone).replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        with self.store.connection(consistent=True) as c:
            since = self.store.meta("collection_started", None, c)

            def period(start, end):
                count, payload = self.store.execute(c, "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM swarmtenderr_completions WHERE completed>=? AND completed<?", (start.timestamp(), end.timestamp())).fetchone()
                covered = since is not None and start.timestamp() >= since
                return {"period": start.date().isoformat(), "count": int(count) if count or covered or (since and end.timestamp() > since) else None,
                        "bytes": payload if count or covered or (since and end.timestamp() > since) else None, "partial": not covered}

            daily = [period(today - timedelta(days=i), today - timedelta(days=i - 1)) for i in reversed(range(30))]
            monthly = []
            for i in reversed(range(12)):
                index = month.year * 12 + month.month - 1 - i
                start = month.replace(year=index // 12, month=index % 12 + 1)
                following = index + 1
                end = month.replace(year=following // 12, month=following % 12 + 1)
                monthly.append(period(start, end))
            total, payload = self.store.execute(c, "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM swarmtenderr_completions").fetchone()
            categories = self.store.objects(self.store.execute(c, "SELECT category, COUNT(*) AS count, SUM(bytes) AS bytes FROM swarmtenderr_completions WHERE completed>=? GROUP BY category ORDER BY COUNT(*) DESC, category LIMIT 20", (month.timestamp(),)))
        result = {"timezone": timezone, "today": daily[-1], "month": monthly[-1],
                  "total_completed": total, "total_bytes": payload, "daily": daily, "monthly": monthly,
                  "categories": categories, "comparison": {"previous_day": daily[-2], "previous_month": monthly[-2]}, "coverage": {"since": since, "historical_complete": False,
                  "note": "Counts include observed completions and surviving client history. Removed torrents before collection cannot be recovered."}}
        self.cache_key, self.cached, self.cached_at = key, result, current
        return result
