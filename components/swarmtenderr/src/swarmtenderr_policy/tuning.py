"""Public, bounded optimizer tuning; callers pass immutable policy explicitly."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Policy:
    ranking_interval_seconds: int = 300
    probe_seconds: int = 2100
    retry_seconds: int = 7200
    rate_fresh_seconds: int = 900
    seed_trial_seconds: int = 600
    seed_trial_retry_seconds: int = 7200
    min_trial_seeds: int = 5
    min_proven_speed_kib: int = 32
    swarm_trial_seconds: int = 180
    swarm_warmup_seconds: int = 120
    swarm_min_sample_seconds: int = 60
    swarm_retry_seconds: int = 7200
    swarm_slow_speed_kib: int = 256
    metadata_trial_seconds: int = 180
    metadata_retry_seconds: int = 7200
    minimum_rate_sample_seconds: int = 120
    trial_improvement_percent: int = 20
    unknown_size_gib: int = 2
    other_buffer_gib: int = 5

    @classmethod
    def from_values(cls, values, supported=None):
        supported = set(POLICY_DEFAULTS) if supported is None else set(POLICY_DEFAULTS) & set(supported)
        selected = {key: values[key] for key in supported if key in values}
        merged = {**POLICY_DEFAULTS, **selected}
        validate({key: merged[key] for key in supported}, supported)
        return cls(**selected)

    @property
    def min_proven_speed(self):
        return self.min_proven_speed_kib * 1024

    @property
    def swarm_slow_speed(self):
        return self.swarm_slow_speed_kib * 1024

    @property
    def trial_improvement(self):
        return 1 - self.trial_improvement_percent / 100

    @property
    def unknown_reservation(self):
        return self.unknown_size_gib * 1024**3

    @property
    def other_buffer(self):
        return self.other_buffer_gib * 1024**3


DEFAULT_POLICY = Policy()
POLICY_DEFAULTS = asdict(DEFAULT_POLICY)
# key: minimum, maximum, label, help, section
RULES = {
    'ranking_interval_seconds': (30, 3600, 'Queue ranking interval (seconds)', 'Minimum time between ordinary priority reshuffles. New bounded trials may run sooner.', 'Ranking'),
    'probe_seconds': (300, 7200, 'No-progress probe window (seconds)', 'How long an active transfer is observed before a priority-only deferral. Files are retained.', 'Discovery'),
    'retry_seconds': (300, 86400, 'No-progress retry delay (seconds)', 'Time before a deferred no-progress transfer is considered again.', 'Discovery'),
    'rate_fresh_seconds': (60, 86400, 'Speed evidence lifetime (seconds)', 'Discard old measured speed when a queued transfer has made no recent progress.', 'Ranking'),
    'seed_trial_seconds': (60, 1800, 'Seeded trial duration (seconds)', 'Fixed sampling window for a queued torrent with enough seeders.', 'Discovery'),
    'seed_trial_retry_seconds': (300, 86400, 'Seeded trial retry delay (seconds)', 'Minimum time between seeded discovery attempts for the same torrent.', 'Discovery'),
    'min_trial_seeds': (1, 10000, 'Minimum seeders for a seeded trial', 'Required complete sources before reserving a seeded discovery slot.', 'Discovery'),
    'min_proven_speed_kib': (1, 10240, 'Useful measured speed (KiB/s)', 'Transfers at or above this measured speed are preferred over untested estimates.', 'Ranking'),
    'swarm_trial_seconds': (60, 1800, 'Swarm trial duration (seconds)', 'Fixed window for sampling an unproven swarm without churning the queue each poll.', 'Discovery'),
    'swarm_warmup_seconds': (30, 1800, 'Connection warm-up (seconds)', 'Give a newly active swarm time to connect before considering it stalled.', 'Discovery'),
    'swarm_min_sample_seconds': (15, 600, 'Minimum active sample (seconds)', 'A no-progress result needs this much observed active time; otherwise it is unobserved.', 'Discovery'),
    'swarm_retry_seconds': (300, 86400, 'Swarm retry delay (seconds)', 'Backoff after a measured swarm trial. Existing trial deadlines remain fixed.', 'Discovery'),
    'swarm_slow_speed_kib': (1, 10240, 'Slow-transfer threshold (KiB/s)', 'A slow secondary transfer may be challenged while a useful winner remains protected.', 'Ranking'),
    'metadata_trial_seconds': (60, 1800, 'Metadata trial duration (seconds)', 'Bounded time reserved for a magnet to obtain its metadata.', 'Metadata'),
    'metadata_retry_seconds': (300, 86400, 'Metadata retry delay (seconds)', 'Backoff after a genuinely observed metadata failure.', 'Metadata'),
    'minimum_rate_sample_seconds': (30, 600, 'Sustained-speed sample (seconds)', 'Use measured bytes over this minimum sample before trusting a sustained rate.', 'Ranking'),
    'trial_improvement_percent': (5, 50, 'Required trial improvement (%)', 'A trial must improve the measured completion score by this amount to displace its incumbent.', 'Ranking'),
    'unknown_size_gib': (1, 100000, 'Unknown-size reservation (GiB)', 'Reserve this capacity for a magnet until its real wanted size is known.', 'Capacity'),
    'other_buffer_gib': (1, 100000, 'Additional working buffer (GiB)', 'Capacity kept outside download reservations for other work and bookkeeping.', 'Capacity'),
    'download_slots': (0, 100, 'Download slots', 'A positive client-wide ceiling. On Apollo, zero follows the existing qBittorrent requested ceiling.', 'Capacity'),
    'minimum_free_gib': (1, 100000, 'Download free-space reserve (GiB)', 'Free download capacity kept outside proactive reservations.', 'Capacity'),
    'work_budget_gib': (1, 100000, 'Download working budget (GiB)', 'Maximum download working data before the guard holds unfinished transfers.', 'Capacity'),
    'library_reserve_gib': (1, 100000, 'Import destination reserve (GiB)', 'Keep this capacity free on the library/import destination.', 'Capacity'),
    'pause_free_gib': (1, 100000, 'Low-space hold threshold (GiB)', 'Hold unfinished transfers below this threshold. Completed data and uploads are protected.', 'Protection'),
    'resume_free_gib': (2, 100000, 'Resume threshold (GiB)', 'Resume guard-owned work after available space recovers above this value.', 'Protection'),
    'reclaim_at_percent': (50, 99, 'Start recovery at working usage (%)', 'Ask the existing recovery owner for bounded import-first recovery at this utilization.', 'Protection'),
    'recovery_target_percent': (10, 90, 'Recovery target usage (%)', 'Keep recovery latched until usage falls below this lower target.', 'Protection'),
}
GUARD_DEFAULTS = {**POLICY_DEFAULTS, 'download_slots': 0, 'minimum_free_gib': 50,
                  'work_budget_gib': 500, 'library_reserve_gib': 50, 'pause_free_gib': 25,
                  'resume_free_gib': 50, 'reclaim_at_percent': 85, 'recovery_target_percent': 70}
# Apollo v3 intentionally retains its commissioned ranking behavior. Metadata
# admission and v4's sustained-sample comparison are not advertised as v3 knobs.
GUARD_KEYS = set(GUARD_DEFAULTS) - {'metadata_trial_seconds', 'metadata_retry_seconds',
                                 'minimum_rate_sample_seconds', 'trial_improvement_percent'}


def validate(values, allowed=None, *, relationships=True):
    allowed = set(RULES) if allowed is None else allowed
    if set(values) - allowed:
        raise ValueError('Unsupported optimizer setting')
    for key, value in values.items():
        low, high, label, *_ = RULES[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{label} must be a whole number between {low} and {high}')
    if not relationships:
        return
    v = {**GUARD_DEFAULTS, **values}
    if v['swarm_min_sample_seconds'] > v['swarm_trial_seconds'] or ('metadata_trial_seconds' in allowed and v['swarm_min_sample_seconds'] > v['metadata_trial_seconds']):
        raise ValueError('Minimum active sample must fit inside swarm and metadata trials')
    if v['swarm_warmup_seconds'] > v['swarm_trial_seconds']:
        raise ValueError('Connection warm-up must not exceed the swarm trial duration')
    if 'minimum_rate_sample_seconds' in allowed and v['minimum_rate_sample_seconds'] > v['swarm_trial_seconds']:
        raise ValueError('Sustained-speed sample must fit inside the swarm trial duration')
    if 'pause_free_gib' in allowed and not v['pause_free_gib'] < v['resume_free_gib'] <= v['minimum_free_gib']:
        raise ValueError('Require hold threshold < resume threshold <= download free-space reserve')
    if 'recovery_target_percent' in allowed and v['recovery_target_percent'] >= v['reclaim_at_percent']:
        raise ValueError('Recovery target must be below the recovery start percentage')
    if 'pause_free_gib' in allowed and v['work_budget_gib'] <= v['minimum_free_gib'] + v['other_buffer_gib']:
        raise ValueError('Working budget must exceed the free-space reserve and additional buffer')


def fields(keys, defaults):
    return [{'key': key, 'minimum': RULES[key][0], 'maximum': RULES[key][1],
             'label': RULES[key][2], 'help': RULES[key][3], 'group': RULES[key][4],
             'default': defaults[key]} for key in RULES if key in keys]
