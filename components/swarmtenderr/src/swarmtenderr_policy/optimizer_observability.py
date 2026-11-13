"""Bounded optimizer explanations, confirmed changes and public metrics."""
from __future__ import annotations

from swarmtenderr_policy.tuning import DEFAULT_POLICY

import json
import math

from . import admission, explanations

MAX_TORRENTS = 25
MAX_EVENTS = 200


def public_text(value, limit=160):
    return ''.join(c if c.isprintable() else ' ' for c in str(value or ''))[:limit]


def reason_text(reason, estimate, *, policy=DEFAULT_POLICY):
    parts = [reason]
    if reason=='Metadata discovery trial':
        parts.extend((f'{policy.metadata_trial_seconds / 60:g}-minute sample',f"{estimate['seeds']} reported seeders",
                      f"{estimate['peer_hint']} known peers"))
    if reason=='Metadata repair observation':parts.append('35-minute Cleanuparr observation after repeated timeouts')
    if reason in ('Seeded speed trial','Next seeded trial'):
        parts.append(f"{estimate['seeds']} reported seeders")
    if reason in ('Swarm discovery trial','Next swarm discovery'):
        if reason=='Swarm discovery trial':parts.append(f'{policy.swarm_trial_seconds / 60:g}-minute sample')
        if estimate['popularity_hint']>0:parts.append(f"popularity hint {estimate['popularity_hint']:.0f}")
        if estimate['peer_hint']>0:parts.append(f"{estimate['peer_hint']} known peers")
    if reason == 'Completion score':
        if estimate['proven']:
            parts.append('measured throughput')
        if estimate['completion_boost'] > 1:
            parts.append('85% complete boost')
        if estimate['age_boost'] > 1:
            parts.append('age boost')
        if estimate.get('small_boost',1)>1:
            parts.append('remaining-size preference')
    return '; '.join(parts)


def remember_change(state, before, identity, action, now, *, policy=DEFAULT_POLICY):
    """Keep accepted requests until qBittorrent's asynchronous position cache updates."""
    pending = state.setdefault('optimizer_pending_changes',
                               {'requested_at':now,'before':{},'actions':{}})
    values = admission.estimate(before[identity],state.get('history',{}).get(identity,{}),now, policy=policy)
    pending['actions'][identity] = dict(action,explanation=reason_text(action['reason'],values, policy=policy),
                                        eta_seconds=values['eta_seconds'],estimate_source=values['source'],
                                        requested_at=now)
    for item in set(pending['actions']) | set(state.get('optimizer_applied_front',[])):
        row = before.get(item)
        if row:
            pending['before'].setdefault(item,{'priority':row.get('priority',-1),
                                              'progress':row.get('progress',0)})


def changes(state, before, after, actions, now, *, policy=DEFAULT_POLICY):
    """Record confirmed requests and displacement of the previously chosen front."""
    if not actions:
        return
    history = state.setdefault('history', {})
    events = state.setdefault('optimizer_events', [])
    counters = state.setdefault('optimizer_changes', {})
    watched = set(actions) | set(state.get('optimizer_applied_front', []))
    for identity in sorted(watched):
        old, row = before.get(identity), after.get(identity)
        if (not old or not row or row.get('progress',1) >= 1
                or row.get('state') == 'forcedDL' or row.get('force_start',False)):
            continue
        previous, current = old.get('priority',-1), row.get('priority',-1)
        action = actions.get(identity)
        if action is None:
            if current <= previous:
                continue
            action = {'action':'Moved back', 'reason':'Yielded to promoted downloads'}
        elif previous == current and action['action'] != 'Probe deferred':
            continue
        elif action['action'] == 'Promoted' and current > previous:
            action = {'action':'Moved back','reason':'Yielded to promoted downloads'}
        values = admission.estimate(row, history.get(identity,{}), now, policy=policy)
        event = {'event':'cinema_optimizer_priority_change','at':now,
                 'torrent_id':identity,'torrent':public_text(row.get('name',identity)),
                 'action':action['action'],'reason':action.get('explanation',reason_text(action['reason'],values, policy=policy)),
                 'before':previous,'after':current,'eta_seconds':action.get('eta_seconds',values['eta_seconds']),
                 'estimate_source':action.get('estimate_source',values['source']),
                 'requested_at':action.get('requested_at',now)}
        history.setdefault(identity,{})['priority_change'] = event
        events.append(event)
        counters[action['action']] = counters.get(action['action'],0)+1
        print(json.dumps(event,sort_keys=True,separators=(',',':')),flush=True)
    state['optimizer_events'] = events[-MAX_EVENTS:]
    if actions:
        state['optimizer_applied_front'] = list(state.get('queue_front', []))


def snapshot(state, rows, owned, plan, now, *, policy=DEFAULT_POLICY):
    history = state.get('history', {})
    eligible = {h:r for h,r in rows.items() if r.get('progress',1)<1
                and (h in owned or r.get('state') == 'forcedDL')}
    actual_front = sorted((h for h,r in eligible.items()
                           if r.get('state') in admission.ACTIVE_STATES
                           and r.get('state') != 'forcedDL' and not r.get('force_start',False)),
                          key=lambda h:(eligible[h].get('priority',10**9),h))[:len(plan['front'])]
    # Always include selected/running/held work, then fill with actual queue order.
    ordered = sorted(eligible,key=lambda h:(
        h not in plan['front'],eligible[h].get('state') not in admission.RUNNING_STATES,
        h not in state.get('held',{}),eligible[h].get('priority',10**9),h))[:MAX_TORRENTS]
    result = []
    for identity in ordered:
        row = eligible[identity]
        values = admission.estimate(row,history.get(identity,{}),now, policy=policy)
        change = history.get(identity,{}).get('priority_change',{})
        forced = row.get('state') == 'forcedDL' or row.get('force_start',False)
        if forced:
            decision, reason = 'Manual force-start','Bypasses automatic ranking'
        elif identity in state.get('held',{}):
            decision, reason = 'Held','Safety or retired-release hold'
        elif history.get(identity,{}).get('deferred_until',0)>now:
            decision, reason = 'Probe deferred',f'{policy.probe_seconds / 60:g} active minutes without progress'
        elif (row.get('amount_left',0)<=0
              and history.get(identity,{}).get('metadata_retry_at',0)>now):
            decision, reason = 'Metadata deferred','Metadata sample ended; bounded retry cooldown'
        elif (history.get(identity,{}).get('swarm_trial_retry_at',0)>now
              and history.get(identity,{}).get('swarm_trial_result',{}).get('outcome')=='no_progress'
              and row.get('dlspeed',0)<=0):
            decision, reason = 'Probe deferred',('No progress in swarm sample; two-hour retry cooldown' if policy.swarm_retry_seconds == 7200 else f'No progress in swarm sample; {policy.swarm_retry_seconds / 60:g}-minute retry cooldown')
        elif identity in state.get('space_plan',{}).get('deferred',{}):
            decision,reason='Waiting for space',state['space_plan']['deferred'][identity]
        elif identity in plan['front']:
            decision = 'Chosen'
            reason = reason_text(plan['reasons'][identity],values, policy=policy)
            if plan['front'] != actual_front:
                decision = 'Recommended' if admission.ranking_ready(state,plan,now, policy=policy) else 'Pending cooldown'
        else:
            decision = 'Running' if row.get('state') in admission.RUNNING_STATES else 'Waiting'
            reason = (reason_text('Next seeded trial',values, policy=policy) if identity in plan.get('up_next',[])
                      else reason_text('Next swarm discovery',values, policy=policy) if identity in plan.get('swarm_next',[])
                      else 'Seeded trial waiting for space' if values['seeds']>=admission.MIN_TRIAL_SEEDS
                      and row.get('amount_left',0)>state.get('seed_trial_budget_bytes',float('inf'))
                      else 'Waiting for metadata trial' if values['source']=='Unknown size'
                      else 'No complete source; waiting for repair trial' if not values['viable']
                      else 'Lower completion score priority')
        active = row.get('state') in admission.RUNNING_STATES
        eta = values['eta_seconds']
        finish = now+eta if active and eta is not None and values['has_measured_speed'] else 0
        result.append({'torrent':identity,'name':public_text(row.get('name',identity)),
                       'app':public_text(row.get('category',''),24),
                       'state':public_text(row.get('state','unknown'),24),
                       'estimate_source':values['source'],'decision':decision,'reason':reason,
                       'last_action':change.get('action','No recorded change'),
                       'last_reason':change.get('reason','Changes recorded from rollout'),
                       'values':{'queue_position':max(-1,row.get('priority',-1)),
                                 'selected_rank':plan['front'].index(identity)+1 if identity in plan['front'] else 0,
                                 'progress_percent':row.get('progress',0)*100,
                                 'remaining_bytes':max(0,row.get('amount_left',0)),
                                 'current_speed_bytes':max(0,row.get('dlspeed',0)),
                                 'estimated_speed_bytes':values['estimated_speed'],
                                 'eta_seconds':eta if eta is not None else -1,
                                 'finish_timestamp_ms':finish*1000,
                                 'score_seconds':values['score_seconds'],
                                 'last_change_timestamp_ms':change.get('at',0)*1000}})
    deadlines=[h[field] for h in history.values() for field in ('swarm_trial_until','metadata_trial_until')
               if h.get(field,0)>now]
    state['optimizer'] = {'checked_at':now,'rows':result,'total_candidates':len(eligible),
                          'next_ranking_at':min([state.get('queue_ranked_at',0)+policy.ranking_interval_seconds,*deadlines])}
    rankings, total = explanations.build(state, rows, owned, plan, now, admission, policy=policy)
    state['optimizer'].update(rankings=rankings, ranking_total=total)


def metrics(state):
    data = state.get('optimizer',{})
    lines = [f'apollo_cinema_optimizer_observed_timestamp_seconds {data.get("checked_at",0)}',
             f'apollo_cinema_optimizer_next_ranking_timestamp_seconds {data.get("next_ranking_at",0)}',
             f'apollo_cinema_optimizer_candidates {data.get("total_candidates",0)}']
    lines.append(f'apollo_cinema_optimizer_download_slots {state.get("native_download_slots",admission.MAX_DOWNLOADS)}')
    capacity=state.get('space_plan',{})
    for field in ('requested_slots','applied_slots','ssd_budget_bytes','ssd_reserved_bytes',
                  'library_budget_bytes','library_reserved_bytes','external_reserved_bytes',
                  'reserve_bytes','other_buffer_bytes','metadata_trials','limited'):
        value=capacity.get(field,0)
        lines.append(f'apollo_cinema_optimizer_capacity_{field} {int(value)}')
    lines.append(f'apollo_cinema_optimizer_capacity_waiting {len(capacity.get("deferred",{}))}')
    for action,value in sorted(state.get('optimizer_changes',{}).items()):
        label=json.dumps(public_text(action,40))
        lines.append(f'apollo_cinema_optimizer_priority_changes_total{{action={label}}} {value}')
    for row in data.get('rows',[])[:MAX_TORRENTS]:
        labels=','.join(f'{key}={json.dumps(row[key],ensure_ascii=False)}' for key in (
            'torrent','name','app','state','estimate_source','decision','reason','last_action','last_reason'))
        for field,value in row['values'].items():
            if type(value) not in (float,int) or not math.isfinite(value):
                continue
            lines.append(f'apollo_cinema_optimizer_torrent{{{labels},field="{field}"}} {value}')
    return lines
