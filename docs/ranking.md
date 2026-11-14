# Torrent ranking explanations

Torrents → Optimizer rank sorts the current eligible plan. Click a rank or inspect
the torrent to see why it was chosen or is waiting. Plan rank is a recommendation;
qBittorrent's native queue position is displayed separately. Observe mode never
claims to have applied that recommendation. An external controller reports
the active native guard's policy, not a second scheduler.

The completion policy compares complete-source availability, then useful measured
throughput, then weighted completion cost. The inspector shows remaining bytes
divided by the current/fresh measured speed or bounded seeder estimate. Completion,
waiting-age and remaining-size boosts divide this base cost. Each contribution is
shown as the corresponding change in weighted seconds; lower cost ranks first.
This number is not a wall-clock ETA or an arbitrary points system. More remaining
work and slower speed increase base cost. Seeders inform the fallback speed;
popularity and peers choose discovery experiments without adding completion points.

Reserved metadata, seeded and swarm trials can override completion order. Safety
holds, manual ownership, download/import reservations, unavailable sources and
probe/retry/ranking cooldowns are shown separately from arithmetic. A smaller
score cannot override those rules. The inspector distinguishes plan and completion
rank so a trial reservation does not look like a scoring error.

Metadata observations count actual active windows seen by the observer. They
record acquisition, a continuously observed window ending without metadata, or
an unconfirmed window when observation was insufficient. They do not infer old
attempts, lifetime failure counts or tracker behavior. Unknown size has no
completion score. Existing retry/probe timestamps are shown when the policy has
them. Installing this feature does not reset, start, stop or reprioritize a trial.

`snapshot.torrents[].ranking` is the allowlisted version-1 explanation in HTTP and
WebSocket snapshots. Its OpenAPI schema is `TorrentRanking`. Older feeds remain
readable with `ranking: null`; stale observations remain labeled stale. At most
5,000 explanations and 8 MiB of encoded explanation data are published, selected
work and eligible plan order first. `optimizer.ranking_total` records the number
before clipping, and omitted torrents explicitly show an unavailable explanation.
The existing 25-row metrics projection remains bounded independently.

Settings uses the available content width. Related fields occupy two aligned
columns on larger screens and collapse to one column on smaller screens. Labels,
help text, source and input stay together in each field.
