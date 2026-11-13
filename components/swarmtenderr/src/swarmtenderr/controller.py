"""Durable, single-owner adapter around the shared scheduling policy.

Only native priority and the download ceiling are owned here. Manual stops,
force starts, upload settings, files and repair/removal remain with their owner.
"""
import copy
import shutil
import time
import uuid

from swarmtenderr_policy import admission, space_planner, optimizer_observability
from swarmtenderr_policy.tuning import Policy


class Controller:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.owner = uuid.uuid4().hex

    @staticmethod
    def managed(row, config):
        tags = {tag.strip() for tag in str(row.get('tags', '')).split(',')}
        return (row.get('category') in config['managed_categories']
                and not tags.intersection(set(config['excluded_tags']) | {'apollo-keep', 'swarmtenderr-keep'})
                and row.get('progress', 1) < 1 and not row.get('force_start')
                and row.get('state') in admission.ACTIVE_STATES - {'forcedDL'})

    def mutate(self, client, state, action, target, operation, expected=None):
        # Persist intent before HTTP; a lost response must never trigger a retry.
        if not self.store.controller_lease(self.owner):
            raise RuntimeError('Controller ownership changed')
        if self.store.meta('config_revision', 0) != state.get('configuration_revision'):
            state['queue_ranked_at'] = 0
            self.store.save_policy(state)
            raise RuntimeError('Settings changed before the optimizer request')
        identity = self.store.begin_action(target, action, 'optimizer')
        state.setdefault('pending_mutations', []).append({'id': identity, 'action': action, 'target': target, 'at': time.time(), 'expected': expected})
        self.store.save_policy(state)
        try:
            operation()
        except Exception:
            self.store.finish_action(identity, 'uncertain')
            state['fault_ack_revision'] = state.get('policy_ack_revision')
            state['fault_mode'] = state.get('mode')
            state['mutation_fault'] = 'A request could not be confirmed. Switch to Observe, inspect qBittorrent, then explicitly re-enable enforcement.'
            self.store.save_policy(state)
            raise
        self.store.finish_action(identity, 'accepted')

    def poll(self, client, raw, config):
        now = time.time()
        policy = Policy.from_values(config)
        if not self.store.controller_lease(self.owner):
            return {'connected': True, 'mode': 'observe', 'effective_status': 'standby', 'reason': 'Another instance owns policy evaluation', 'rows': [], 'events': []}
        state = self.store.policy_state()
        rows = {row['hash']: row for row in raw if isinstance(row.get('hash'), str)}
        history = state.setdefault('history', {})
        history = state['history'] = {key: value for key, value in history.items()
                                     if key in rows or now - value.get('observed_at', 0) < 86400}
        admission.observe(rows, history, now, policy=policy)
        owned = {key: {} for key, row in rows.items() if self.managed(row, config)}
        preferences = client.preferences()
        free = client.system_state().get('free_space_on_disk')
        library = shutil.disk_usage(self.settings.library_probe_path).free if self.settings.library_probe_path else None
        used = sum(max(0, row.get('completed', 0)) for row in raw)
        proposed = admission.queue_plan(rows, owned, history, now,
            trial_budget=config['work_budget_gib'] * 1024**3, max_downloads=config['download_slots'], policy=policy)
        # Repair observations depend on a separate repair owner in Apollo. A
        # standalone scheduler has no authority to create those repair trials.
        repair = proposed.get('metadata_repair')
        if repair:
            proposed['front'] = [key for key in proposed['front'] if key != repair]
            proposed['eligible'] = [key for key in proposed['eligible'] if key != repair]
            proposed['metadata_repair'] = None
        capacity = space_planner.plan(rows, owned, history, config['download_slots'], proposed,
            free, used, library, config['work_budget_gib'] * 1024**3, now,
            reserve=config['minimum_free_gib'] * 1024**3,
            library_reserve=config['library_reserve_gib'] * 1024**3, policy=policy)
        capacity['native_slots'] = preferences.get('max_active_downloads')
        state['space_plan'] = capacity
        plan = copy.deepcopy(proposed)
        plan['front'] = capacity['selected']
        for key in plan['front']:
            plan['reasons'].setdefault(key, 'Completion score')
        pending = []
        for request in state.get('pending_mutations', []):
            confirmed = (request['action'] == 'download ceiling' and preferences.get('max_active_downloads') == request['target'])
            confirmed |= (request['action'] == 'policy priority' and rows.get(request['target'], {}).get('priority') == request['expected'])
            if confirmed:
                self.store.finish_action(request['id'], 'confirmed')
            else:
                pending.append(request)
                if now - request['at'] > 120:
                    self.store.finish_action(request['id'], 'uncertain')
                    state['fault_ack_revision'] = state.get('policy_ack_revision')
                    state['fault_mode'] = state.get('mode')
                    state['mutation_fault'] = 'An optimizer request remains unconfirmed. Switch to Observe and inspect the native queue before enabling enforcement again.'
        state['pending_mutations'] = pending
        mode = config['policy_mode']
        previous_mode = state.get('mode', 'observe')
        previous_ack = state.get('policy_ack_revision', 0)
        policy_revision = config.get('policy_revision', 0)
        ack_revision = config.get('policy_ack_revision', 0)
        if state.get('policy_revision', 0) != policy_revision:
            state['queue_ranked_at'] = 0
        state.update(mode=mode, configuration_revision=config['revision'],
                     policy_revision=policy_revision, policy_ack_revision=ack_revision)
        reason = state.get('mutation_fault', '')
        effective = 'observe' if mode == 'observe' else 'blocked'
        native = preferences.get('max_active_downloads')
        if mode == 'observe' and (previous_mode != 'observe' or previous_ack != ack_revision):
            # Explicitly returning/resetting to Observe acknowledges old uncertain
            # requests. General settings changes never acknowledge a mutation.
            for request in state.get('pending_mutations', []):
                self.store.finish_action(request['id'], 'uncertain')
            state['pending_mutations'] = []
            state.pop('mutation_fault', None)
            reason = ''
        if mode == 'observe' and state.get('mutation_fault'):
            effective, reason = 'blocked', state['mutation_fault']
        elif mode == 'observe':
            ceiling = state.get('ceiling')
            if ceiling and native == ceiling['applied'] and native != ceiling['original'] and not state.get('pending_mutations'):
                self.mutate(client, state, 'download ceiling', ceiling['original'], lambda: client.download_cap(ceiling['original'], expected=native))
            if ceiling and native != ceiling['applied']:
                state.pop('ceiling', None)
            if state.get('pending_mutations'):
                effective, reason = 'restoring', 'Restoring the previous native download ceiling; waiting for confirmation'
            else:
                reason = 'Observe mode: recommendations only'
        else:
            if not config['managed_categories']:
                reason = 'Choose managed categories before enforcement'
            elif preferences.get('queueing_enabled') is not True or preferences.get('dont_count_slow_torrents') is not False:
                reason = 'Enable native queueing and count slow torrents in qBittorrent before enforcement'
            elif type(native) is not int or not 0 <= native <= 100 or (native == 0 and not state.get('ceiling')):
                reason = 'A finite native download ceiling is required'
            elif state.get('ceiling') and native not in (state['ceiling']['applied'], state['ceiling']['original']):
                reason = 'The native download ceiling changed outside Swarmtenderr. Return to Observe before taking ownership again.'
            elif not reason and not state.get('pending_mutations'):
                ready = admission.ranking_ready(state, plan, now, policy=policy)
                if ready:
                    admission.commit_plan(plan, rows, history, now, policy=policy)
                    state['queue_front'], state['queue_ranked_at'] = plan['front'], now
                    self.store.save_policy(state)
                    # Preserve selected order through native top-priority calls.
                    current_front = [key for key, row in sorted(rows.items(), key=lambda item: item[1].get('priority', 10**9)) if row.get('priority', -1) > 0][:len(plan['front'])]
                    for key in ([] if current_front == plan['front'] else reversed(plan['front'])):
                        if not self.managed(rows[key], config):
                            continue
                        self.mutate(client, state, 'policy priority', key, lambda key=key: client.action(key, 'prioritize', eligible=lambda fresh: self.managed(fresh, config)), expected=plan['front'].index(key)+1)
                    # All requests are bounded and individually audited. A later
                    # observation verifies priority; no stop/start is submitted.
                target = capacity['applied_slots']
                if native != target:
                    original = state.get('ceiling', {}).get('original', native)
                    state['ceiling'] = {'original': original, 'applied': target}
                    self.mutate(client, state, 'download ceiling', target, lambda: client.download_cap(target, expected=native))
                effective, reason = 'enforcing', 'Enforcing native queue priorities and download capacity'
            if state.get('pending_mutations') and not state.get('mutation_fault'):
                effective, reason = 'pending', 'Requests accepted; waiting for qBittorrent confirmation'
        optimizer_observability.snapshot(state, rows, owned, plan, now, policy=policy)
        self.store.save_policy(state)
        for item in state['optimizer']['rows']:
            entry = history.get(item['torrent'], {})
            item['trial_until'] = max(entry.get('swarm_trial_until', 0), entry.get('metadata_trial_until', 0), entry.get('seed_trial_until', 0))
            item['cooldown_until'] = max(entry.get('deferred_until', 0), entry.get('swarm_trial_retry_at', 0), entry.get('metadata_retry_at', 0))
        result = dict(state['optimizer'], connected=True, owner='swarmtenderr', stale=False, observed_at=now,
                      mode=mode, effective_status=effective, reason=reason, capacity=capacity, events=state.get('optimizer_events', [])[-50:])
        return result
