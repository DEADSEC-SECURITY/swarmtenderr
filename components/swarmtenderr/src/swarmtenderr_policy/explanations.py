"""Public, bounded explanations of an evaluated plan; never changes selection."""
from __future__ import annotations

import json
import math

MAX_RANKINGS = 5000
MAX_PUBLIC_BYTES = 8 * 1024 * 1024


def number(value, default=0):
    return value if type(value) in (int, float) and math.isfinite(value) else default


def text(value, limit=180):
    return ''.join(c if c.isprintable() else ' ' for c in value)[:limit] if isinstance(value, str) else ''


def observe_metadata(row, entry, now, running_states):
    """Count observed active windows, not guessed lifetime attempts or failures."""
    record = entry.setdefault('metadata_observation', {'since': now, 'attempts': 0,
        'successes': 0, 'failures': 0, 'unobserved': 0})
    known = row.get('amount_left', 0) > 0 or row.get('progress', 0) >= 1
    active = not known and row.get('state') in running_states
    elapsed = now - record.get('observed_at', now)
    if record.get('active'):
        if not 0 <= elapsed <= 60:
            record['gap'] = True
        if active and 0 <= elapsed <= 60:
            record['active_seconds'] = record.get('active_seconds', 0) + elapsed
        if not active:
            outcome = ('metadata' if known else 'no_metadata'
                       if record.get('active_seconds', 0) >= 60 and not record.get('gap', False)
                       else 'unobserved')
            counter = {'metadata': 'successes', 'no_metadata': 'failures', 'unobserved': 'unobserved'}[outcome]
            record[counter] += 1
            record.update(last_outcome=outcome, last_at=now, active=False)
    elif active:
        record.update(active=True, started_at=now, active_seconds=0, gap=False)
        record['attempts'] += 1
    record['observed_at'] = now
    return known, record


def build(state, rows, owned, plan, now, admission, *, policy):
    """Explain the exact planner order and score, including overriding gates."""
    history = state.setdefault('history', {})
    capacity = state.get('space_plan', {})
    front = plan.get('front', [])
    ready = plan.get('eligible', [])
    # Old callers may provide a reduced plan. Never invent a rank in that case.
    order = list(dict.fromkeys(front + plan.get('up_next', []) + plan.get('swarm_next', []) + ready))
    rank = {identity: index + 1 for index, identity in enumerate(order)}
    estimates = {identity: admission.estimate(row, history.get(identity, {}), now, policy=policy)
                 for identity, row in rows.items() if row.get('progress', 1) < 1}
    scored = sorted((identity for identity in ready if identity in estimates
                     and rows[identity].get('amount_left', 0) > 0),
                    key=lambda identity: admission.priority(rows[identity], history.get(identity, {}), now, policy=policy))
    score_rank = {identity: index + 1 for index, identity in enumerate(scored)}
    next_at = max(now, state.get('queue_ranked_at', 0) + policy.ranking_interval_seconds)
    result = []
    for identity, row in rows.items():
        entry = history.setdefault(identity, {})
        known, metadata = observe_metadata(row, entry, now, admission.RUNNING_STATES)
        values = estimates.get(identity)
        blockers = []
        def block(code, label, detail, until=0):
            blockers.append({'code': code, 'label': label, 'detail': text(detail), 'until': until})
        complete = row.get('progress', 1) >= 1
        forced = row.get('force_start') or row.get('state') == 'forcedDL'
        keep = bool(set(str(row.get('tags', '')).replace(' ', '').split(',')) & {'apollo-keep', 'swarmtenderr-keep'})
        if complete:
            block('complete', 'Download complete', 'Completed transfers are outside download ranking; uploads are preserved.')
        elif forced or keep:
            block('protected', 'Manual protection', 'Force-started or protected transfers bypass automatic ranking.')
        elif identity not in owned:
            block('unmanaged', 'Outside managed queue', 'This optimizer does not own this transfer; category rules or a manual stop may exclude it.')
        if identity in state.get('held', {}):
            block('hold', 'Guard hold', 'The guard is holding this transfer for safety or retired-release protection.')
        if entry.get('deferred_until', 0) > now:
            block('probe_cooldown', 'No-progress probe cooldown', 'The active no-progress observation ended; wait for the next eligible probe.', entry['deferred_until'])
        if not known and entry.get('metadata_retry_at', 0) > now:
            block('metadata_cooldown', 'Metadata retry cooldown', 'The metadata window ended; another attempt is not eligible yet.', entry['metadata_retry_at'])
        if (entry.get('swarm_trial_retry_at', 0) > now and row.get('dlspeed', 0) <= 0
                and entry.get('swarm_trial_result', {}).get('outcome') == 'no_progress'):
            block('swarm_cooldown', 'Swarm retry cooldown', 'The last observed swarm sample made no progress.', entry['swarm_trial_retry_at'])
        deferred = capacity.get('deferred', {}).get(identity)
        if deferred:
            block('metadata_slot' if deferred == 'Waiting for metadata slot' else 'capacity',
                  'Metadata slot occupied' if deferred == 'Waiting for metadata slot' else 'Capacity reservation', deferred)
        if values and not values['viable']:
            block('availability', 'No complete source confirmed', 'No seeder, full availability or current throughput confirms a complete source. Discovery can still sample this swarm.')
        if values and not values['proven']:
            block('unproven', 'Useful throughput not demonstrated',
                  f"Measured speed is below {policy.min_proven_speed / 1024:g} KiB/s. Proven transfers form a higher ranking tier.")
        if not known and not complete:
            block('metadata', 'Metadata not acquired', 'Size and remaining work are unknown. A normal completion score cannot be evaluated; a metadata/discovery slot is needed.')
        selected = identity in front
        hard = any(b['code'] in {'complete', 'protected', 'unmanaged', 'hold', 'probe_cooldown', 'metadata_cooldown', 'swarm_cooldown', 'capacity', 'metadata_slot'} for b in blockers)
        if selected and not admission.ranking_ready(state, plan, now, policy=policy):
            # A recommendation may already match the native queue; this is only
            # a blocker when the selected front differs from the actual order.
            actual = sorted((key for key, value in rows.items() if key in owned
                and value.get('progress', 1) < 1 and value.get('state') in admission.ACTIVE_STATES
                and not value.get('force_start') and value.get('state') != 'forcedDL'),
                key=lambda key: (rows[key].get('priority', 10**9), key))[:len(front)]
            if actual != front:
                block('ranking_cooldown', 'Waiting for ranking interval', 'The recommendation is pending the next permitted queue reshuffle.', next_at)
        selection = plan.get('reasons', {}).get(identity, '') if selected else (
            'Next seeded trial' if identity in plan.get('up_next', []) else
            'Next swarm discovery' if identity in plan.get('swarm_next', []) else '')
        if selected:
            decision, reason = 'Selected', selection or 'Selected by the current plan'
        elif hard:
            decision, reason = 'Not eligible' if identity not in rank else 'Waiting', blockers[0]['detail']
        else:
            decision = 'Running' if row.get('state') in admission.RUNNING_STATES else 'Waiting'
            reason = (selection or ('Running in the native queue; the current recommendation puts higher-ranked work or reserved discovery trials ahead.'
                      if decision == 'Running' else 'Available download slots are assigned to transfers ahead in the plan; reserved discovery trials can override completion score order.'))
        score = None
        if values and known:
            base = max(1, row.get('amount_left', 0)) / values['estimated_speed']
            running = base
            factors = []
            for key, label in [('completion_boost', 'Completion boost'), ('age_boost', 'Waiting-age boost'), ('small_boost', 'Remaining-size preference')]:
                adjusted = running / values[key]
                factors.append({'key': key, 'label': label, 'multiplier': values[key], 'delta_seconds': adjusted - running})
                running = adjusted
            score = {'base_seconds': base, 'seconds': values['score_seconds'], 'factors': factors,
                     'estimated_speed': values['estimated_speed'], 'remaining_bytes': max(0, row.get('amount_left', 0)),
                     'source': values['source'], 'viable': values['viable'], 'proven': values['proven']}
        result.append({'torrent': identity, 'schema_version': 1, 'policy': admission.QUEUE_POLICY,
            'observed_at': now, 'rank': rank.get(identity), 'score_rank': score_rank.get(identity),
            'candidate_count': len(rank), 'selected_rank': front.index(identity) + 1 if selected else None,
            'decision': decision, 'reason': reason, 'selection_reason': selection,
            'score': score, 'blockers': blockers, 'next_ranking_at': next_at,
            'signals': {'seeders': values['seeds'] if values else 0,
                        'known_peers': values['peer_hint'] if values else 0,
                        'popularity': values['popularity_hint'] if values else 0},
            'metadata': {'known': known, 'tracking_since': metadata['since'],
                'attempts': metadata['attempts'], 'successes': metadata['successes'],
                'failures': metadata['failures'], 'unobserved': metadata['unobserved'],
                'active': bool(metadata.get('active')), 'active_seconds': metadata.get('active_seconds', 0),
                'last_outcome': metadata.get('last_outcome', ''), 'last_at': metadata.get('last_at', 0),
                'retry_at': entry.get('metadata_retry_at', 0),
                'trial_until': max(entry.get('metadata_trial_until', 0), entry.get('swarm_trial_until', 0), entry.get('seed_trial_until', 0)),
                'last_probe_at': entry.get('last_probe_at', 0)}})
    # Selected work first, then planner order; a cap is explicitly reported.
    result.sort(key=lambda item: (item['rank'] is None, item['rank'] or 0, item['torrent']))
    public = sanitize(result)
    return public, len(result)


def sanitize(items):
    """Allowlist the on-host/public boundary and bound rows and encoded bytes."""
    output, size = [], 2
    if not isinstance(items, list):
        return output
    for item in items[:MAX_RANKINGS]:
        if not isinstance(item, dict) or item.get('schema_version') != 1:
            continue
        row = {key: text(item.get(key), 64 if key == 'torrent' else 180) for key in
               ('torrent', 'policy', 'decision', 'reason', 'selection_reason')}
        row['schema_version'] = 1
        for key in ('rank', 'score_rank', 'selected_rank'):
            value = item.get(key)
            row[key] = value if type(value) is int and 0 < value <= 10**7 else None
        for key in ('observed_at', 'candidate_count', 'next_ranking_at'):
            row[key] = max(0, number(item.get(key)))
        row['blockers'] = [{'code': text(b.get('code'), 40), 'label': text(b.get('label'), 80),
            'detail': text(b.get('detail')), 'until': max(0, number(b.get('until')))}
            for b in item.get('blockers', [])[:10] if isinstance(b, dict)] if isinstance(item.get('blockers'), list) else []
        source = item.get('score')
        row['score'] = None
        if isinstance(source, dict):
            row['score'] = {key: max(0, number(source.get(key))) for key in
                ('base_seconds', 'seconds', 'estimated_speed', 'remaining_bytes')}
            row['score'].update(source=text(source.get('source'), 60), viable=source.get('viable') is True, proven=source.get('proven') is True)
            factors = source.get('factors', [])
            row['score']['factors'] = [{'key': text(f.get('key'), 40), 'label': text(f.get('label'), 60),
                'multiplier': max(1, number(f.get('multiplier'), 1)), 'delta_seconds': number(f.get('delta_seconds'))}
                for f in factors[:3] if isinstance(f, dict)] if isinstance(factors, list) else []
        signals = item.get('signals') if isinstance(item.get('signals'), dict) else {}
        row['signals'] = {key: max(0, number(signals.get(key))) for key in ('seeders', 'known_peers', 'popularity')}
        metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        row['metadata'] = {key: max(0, number(metadata.get(key))) for key in
            ('tracking_since', 'attempts', 'successes', 'failures', 'unobserved', 'active_seconds', 'last_at', 'retry_at', 'trial_until', 'last_probe_at')}
        row['metadata'].update(known=metadata.get('known') is True, active=metadata.get('active') is True,
                               last_outcome=text(metadata.get('last_outcome'), 30))
        size += len(json.dumps(row, ensure_ascii=True, allow_nan=False).encode()) + 2
        if size > MAX_PUBLIC_BYTES:
            break
        output.append(row)
    return output
