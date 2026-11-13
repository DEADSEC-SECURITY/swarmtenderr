"""Rank native qBittorrent queue entries without stopping transfers."""
from __future__ import annotations

import math

ACTIVE_STATES = {'downloading', 'forcedDL', 'stalledDL', 'queuedDL', 'metaDL', 'checkingDL'}
RUNNING_STATES = ACTIVE_STATES - {'queuedDL'}
QUEUE_PREFERENCES = {'queueing_enabled': True, 'max_active_downloads': 3,
                     'dont_count_slow_torrents': False}
STOPPED_STATES = {'stoppedDL', 'pausedDL'}
MAX_DOWNLOADS = 3
WORK_BUDGET = 500 * 1024**3
MEDIA_RESERVE = 50 * 1024**3
# Cleanuparr needs an initial 15-minute observation plus three five-minute
# checks. Parking at ten minutes prevented it from ever repairing a probe.
# Five extra minutes cover poll alignment and API latency at the last check.
PROBE_SECONDS = 35 * 60
RETRY_SECONDS = 2 * 60 * 60
RATE_FRESH_SECONDS = 15 * 60
REQUESTED_TRIAL_SECONDS = 10 * 60
SEED_TRIAL_SECONDS = 10 * 60
SEED_TRIAL_RETRY_SECONDS = 2 * 60 * 60
MIN_TRIAL_SEEDS = 5
MIN_PROVEN_SPEED = 32 * 1024
QUEUE_PREVIEW = 10
QUEUE_POLICY = 'metadata-discovery-v4'
METADATA_TRIAL_SECONDS = 3 * 60
METADATA_RETRY_SECONDS = 2 * 60 * 60
METADATA_UNOBSERVED_RETRY_SECONDS = 10 * 60
METADATA_REPAIR_FAILURES = 3
MIN_RATE_SAMPLE_SECONDS = 2 * 60
TRIAL_IMPROVEMENT = 0.80
SWARM_TRIAL_SECONDS = 3 * 60
SWARM_WARMUP_SECONDS = 2 * 60
SWARM_MIN_SAMPLE_SECONDS = 60
SWARM_RETRY_SECONDS = 2 * 60 * 60
SWARM_SLOW_SPEED = 256 * 1024


def queue_preferences(preferences: dict) -> dict:
    """Respect finite operator slot trials while preserving native queue safety."""
    slots=preferences.get('max_active_downloads',MAX_DOWNLOADS)
    if type(slots) is not int or not 0 <= slots <= 100:
        raise RuntimeError('Use a finite active-download limit between 0 and 100')
    uploads=preferences.get('max_active_uploads',5)
    total=preferences.get('max_active_torrents',10)
    if type(uploads) is not int or type(total) is not int:
        raise RuntimeError('Native queue limits are unavailable')
    # A combined cap must not silently consume the download trial's slots or
    # remove upload capacity. Unlimited uploads require an unlimited total.
    total=-1 if uploads<0 or total<0 else max(total,slots+uploads)
    return {**QUEUE_PREFERENCES,'max_active_downloads':slots,'max_active_torrents':total}


def discovery_signals(row: dict) -> dict:
    popularity=row.get('popularity',0)
    return {'popularity_hint':min(10000,max(0,popularity)) if math.isfinite(popularity) else 0,
            'peer_hint':max(0,row.get('num_incomplete',0),row.get('num_leechs',0))}


def estimate(row: dict, history: dict, now: float) -> dict:
    remaining = max(1,row.get('amount_left',0))
    # Queued jobs do not generate zero-speed samples. Expire their frozen rate
    # instead of treating an hours-old fast transfer as current evidence.
    fresh = now-history.get('last_progress_at',now) <= RATE_FRESH_SECONDS
    observed = max(0,history.get('rate',0)) if fresh else 0
    current = max(0,row.get('dlspeed',0))
    samples = [s for s in history.get('rate_samples',[]) if now-s['at']<=SWARM_TRIAL_SECONDS]
    seconds = sum(s['seconds'] for s in samples)
    sustained = sum(s['bytes'] for s in samples)/seconds if seconds>=MIN_RATE_SAMPLE_SECONDS else None
    speed = sustained if sustained is not None else max(observed,current)
    seeds = max(0,row.get('num_complete',0),row.get('num_seeds',0))
    waited = max(0,now-row.get('added_on',now))
    age_boost = 1+min(waited/86400,30)/7
    prior = 64*1024*min(16,1+math.log2(1+seeds))
    effective = speed if speed>0 else prior
    source = ('Current speed' if current>=observed else 'Measured average') if speed>0 else 'Seeder estimate'
    if sustained is not None and speed>0:source='Measured average'
    # Availability may include our own pieces. An incomplete swarm with no
    # seeders must not beat a viable download just because little is left.
    viable = (seeds>0 or row.get('availability',-1)>=1 or current>0
              or speed>0 and 'availability' not in row)
    if not viable:source = 'No complete source'
    eta = remaining/effective
    completion_boost = 2 if row.get('progress',0)>=0.85 else 1
    small_boost = 1+1/(1+remaining/(4*1024**3))
    return {'eta_seconds':eta if row.get('amount_left',0)>0 and viable else None,
            'score_seconds':eta/completion_boost/age_boost/small_boost,'estimated_speed':effective,
            'source':source if row.get('amount_left',0)>0 else 'Unknown size',
            'age_boost':age_boost,'completion_boost':completion_boost,'small_boost':small_boost,'seeds':seeds,
            'has_measured_speed':speed>0,'viable':viable,'proven':speed>=MIN_PROVEN_SPEED,
            **discovery_signals(row)}


def priority(row: dict, history: dict, now: float) -> tuple:
    values=estimate(row,history,now)
    # A tiny guessed ETA must not evict a transfer already demonstrating useful
    # throughput. The separate discovery slot measures untested swarms.
    return (not values['viable'],not values['proven'],values['score_seconds'],-values['seeds'],
            row.get('added_on',now),row['hash'])


def discovery_hint(row: dict, history: dict, now: float) -> tuple:
    """Weak hints choose experiments; they never supply a speed or ETA."""
    prior = max((r.get('rate',0) for key in ('seed_trial_result','swarm_trial_result')
                 if (r := history.get(key,{})) and 0 <= now-r.get('at',0) < 7*86400),default=0)
    signals=discovery_signals(row)
    # A previous successful sample is useful but cannot dominate discovery for
    # days after seed information was lost. Its contribution is bounded.
    hint=math.log1p(signals['popularity_hint'])+min(1,math.log1p(max(0,prior)/MIN_PROVEN_SPEED))
    return (-hint,-signals['peer_hint'],
            history.get('last_probe_at',0),row.get('added_on',now),row['hash'])


def last_sample_at(history: dict, now: float) -> float:
    previous=history.get('seed_trial_at',now-SEED_TRIAL_RETRY_SECONDS)
    if history.get('swarm_trial_result',{}).get('outcome') in ('progress','no_progress'):
        previous=max(previous,history.get('swarm_trial_at',previous))
    return previous


def metadata_candidate(unknown, history, now):
    """Reserve one fixed sample; untried magnets precede previously sampled ones."""
    continuing=[r for r in unknown if history.get(r['hash'],{}).get('metadata_trial_until',0)>now]
    if continuing:
        return min(continuing,key=lambda r:(history[r['hash']]['metadata_trial_at'],r['hash']))
    eligible=[r for r in unknown if history.get(r['hash'],{}).get('metadata_retry_at',0)<=now]
    return min(eligible,key=lambda r:(history.get(r['hash'],{}).get('metadata_trial_at',0),
        -max(0,r.get('num_complete',0),r.get('num_seeds',0)),
        -discovery_signals(r)['peer_hint'],r.get('added_on',now),r['hash'])) if eligible else None


def completion_order(known, rows, history, now):
    """Require a material measured improvement before a trial replaces its incumbent."""
    ordered=list(known)
    for row in known:
        entry=history.get(row['hash'],{})
        for kind in ('seed','swarm'):
            result=entry.get(f'{kind}_trial_result',{})
            incumbent=rows.get(entry.get(f'{kind}_trial_replaces'))
            if (not incumbent or incumbent not in ordered or result.get('bytes',0)<=0
                    or not 0<=now-result.get('at',0)<RATE_FRESH_SECONDS):continue
            trial_values=estimate(row,entry,now)
            previous=estimate(incumbent,history.get(incumbent['hash'],{}),now)
            if (trial_values['has_measured_speed'] and previous['proven']
                    and trial_values['score_seconds']>previous['score_seconds']*TRIAL_IMPROVEMENT
                    and ordered.index(row)<ordered.index(incumbent)):
                ordered.remove(row);ordered.insert(ordered.index(incumbent)+1,row)
    return ordered


def discover_swarm_slots(front, reasons, ready, rows, history, now, probe, trial, budget, metadata=None):
    """Keep useful transfers; sample unknown swarms instead of filling with stalls."""
    def stalled(row):
        entry=history.get(row['hash'],{})
        return (row.get('state') in RUNNING_STATES and row.get('dlspeed',0)<=0
                and now-max(entry.get('active_since',now),entry.get('last_progress_at',now))
                >= SWARM_WARMUP_SECONDS)
    continuing=[r for r in ready if history.get(r['hash'],{}).get('swarm_trial_until',0)>now]
    protected={r['hash'] for r in (probe,trial,metadata) if r}
    within_budget=lambda r: budget is None or r.get('amount_left',0)<=budget
    continuing=sorted((r for r in continuing if r['hash'] not in protected and within_budget(r)),
                      key=lambda r:(history[r['hash']]['swarm_trial_at'],
                                    history[r['hash']].get('swarm_trial_rank',0),r['hash']))
    pool=sorted((r for r in ready if r['hash'] not in protected
                 and r.get('state')=='queuedDL' and within_budget(r)
                 and r.get('amount_left',0)>0
                 and not estimate(r,history.get(r['hash'],{}),now)['proven']
                 and history.get(r['hash'],{}).get('swarm_trial_retry_at',0)<=now
                 and r not in continuing),
                key=lambda r:discovery_hint(r,history.get(r['hash'],{}),now))
    front=list(front);reasons=dict(reasons)
    continuing_ids={r['hash'] for r in continuing}
    replaceable=sorted((i for i,h in enumerate(front) if h not in protected|continuing_ids),
                       key=lambda i:priority(rows[front[i]],history.get(front[i],{}),now),reverse=True)
    missing=[r for r in continuing if r['hash'] not in front]
    for target,index in zip(missing,sorted(replaceable[:len(missing)])):
        reasons.pop(front[index],None);front[index]=target['hash']
    reserved_positions=[i for i,h in enumerate(front) if h in continuing_ids]
    for index,target in zip(reserved_positions,[r for r in continuing if r['hash'] in front]):
        front[index]=target['hash']
    measured={h:estimate(rows[h],history.get(h,{}),now) for h in front if h not in protected}
    speeds=[v['estimated_speed'] for v in measured.values() if v['viable'] and v['has_measured_speed']]
    # After sampling, challenge a very slow second transfer while retaining the
    # useful winner. Do not delay an imminent completion or a connecting sample.
    challenge=None
    if len(speeds)>=2 and pool:
        slower=[h for h,v in measured.items() if v['viable'] and v['has_measured_speed']
                and v['estimated_speed']<min(SWARM_SLOW_SPEED,max(speeds)/4)
                and (v['eta_seconds'] or 0)>600 and rows[h].get('progress',0)<0.85
                and now-history.get(h,{}).get('active_since',now)>=SWARM_TRIAL_SECONDS
                and history.get(h,{}).get('swarm_trial_until',0)<=now]
        challenge=max(slower,key=lambda h:measured[h]['score_seconds']) if slower else None
    chosen=[]
    for index,identity in enumerate(front):
        row=rows[identity]
        if identity in protected:
            continue
        if any(r['hash']==identity for r in continuing):
            chosen.append(identity);reasons[identity]='Swarm discovery trial';continue
        values=estimate(row,history.get(identity,{}),now)
        if identity!=challenge and (values['viable'] or row.get('state') in RUNNING_STATES and not stalled(row)):
            continue
        target=next((r for r in continuing+pool if r['hash'] not in front),None)
        if target:
            front[index]=target['hash'];chosen.append(target['hash'])
            reasons.pop(identity,None);reasons[target['hash']]='Swarm discovery trial'
    upcoming=[r['hash'] for r in pool if r['hash'] not in front]
    return front,reasons,chosen,upcoming


def queue_plan(rows: dict, owned: dict, history: dict, now: float, trial_budget: float | None = None,
               max_downloads: int = MAX_DOWNLOADS) -> dict:
    if max_downloads<=0:
        return dict(front=[],reasons={},order=[],up_next=[],swarm_trials=[],swarm_next=[],
                    metadata_trial=None,seed_trial=None,eligible=[])
    candidates = [r for h,r in rows.items() if h in owned and r.get('progress',1) < 1
                  and r.get('state') in ACTIVE_STATES | STOPPED_STATES
                  and r.get('state') != 'forcedDL' and not r.get('force_start',False)
                  and 'apollo-keep' not in str(r.get('tags','')).split(',')]
    ready = [r for r in candidates if history.get(r['hash'],{}).get('deferred_until',0) <= now
             and not (r.get('amount_left',0)<=0
                      and history.get(r['hash'],{}).get('metadata_retry_at',0)>now)
             and not (history.get(r['hash'],{}).get('swarm_trial_retry_at',0)>now
                      and history.get(r['hash'],{}).get('swarm_trial_result',{}).get('outcome')=='no_progress'
                      and r.get('dlspeed',0)<=0)]
    ordered = sorted(ready,key=lambda r:priority(r,history.get(r['hash'],{}),now))
    known = [r for r in ordered if r.get('amount_left',0)>0]
    unknown = [r for r in ordered if r.get('amount_left',0)<=0]
    repairing=[r for r in unknown if history.get(r['hash'],{}).get('metadata_repair_until',0)>now]
    metadata=metadata_candidate([r for r in unknown if r not in repairing],history,now)
    # A user-requested experiment occupies at most one throughput slot and has
    # a fixed deadline; ordinary ranking resumes without a manual reset.
    requested = [r for r in ready if 0 <= now-history.get(r['hash'],{}).get(
        'requested_trial_at',-REQUESTED_TRIAL_SECONDS) < REQUESTED_TRIAL_SECONDS]
    trial = max(requested,key=lambda r:(history[r['hash']]['requested_trial_at'],r['hash'])) if requested else None
    trial_reason = 'Requested speed trial'
    if trial and trial.get('amount_left',0)<=0:
        metadata=trial;trial=None
    # Hold a discovery reservation for its full sampling window. Do not choose
    # another swarm on every 30-second observation as speeds/seed counts vary.
    seeded = [r for r in known if estimate(r,history.get(r['hash'],{}),now)['seeds']>=MIN_TRIAL_SEEDS
              and (trial_budget is None or r['amount_left']<=trial_budget)]
    continuing = [r for r in ready if history.get(r['hash'],{}).get('seed_trial_until',0)>now]
    opportunities = sorted((r for r in seeded
                            if r.get('state') not in RUNNING_STATES
                            and now-last_sample_at(history.get(r['hash'],{}),now)
                            >= SEED_TRIAL_RETRY_SECONDS),
                           key=lambda r:(-estimate(r,history.get(r['hash'],{}),now)['seeds'],
                                         *priority(r,history.get(r['hash'],{}),now)))
    if not trial:
        reserved_swarm=any(history.get(r['hash'],{}).get('swarm_trial_until',0)>now for r in ready)
        trial = (min(continuing,key=lambda r:(history[r['hash']]['seed_trial_at'],r['hash']))
                 if continuing else None if reserved_swarm else next(iter(opportunities),None))
        trial_reason = 'Seeded speed trial'
    if metadata and max_downloads==1:trial=None
    if repairing and max_downloads==3:trial=None
    # Keep one actual active no-progress probe through Cleanuparr's strike window.
    probes = [r for r in known if (not trial or r['hash']!=trial['hash'])
              and history.get(r['hash'],{}).get('swarm_trial_until',0)<=now
              and r.get('state') in RUNNING_STATES and r.get('dlspeed',0)<=0
              and now-history.get(r['hash'],{}).get('active_since',now) < PROBE_SECONDS]
    probe_reason='Active probe window'
    if not probes:
        probe_reason='Older download trial'
        probes = [r for r in known if (not trial or r['hash']!=trial['hash'])
                             and history.get(r['hash'],{}).get('swarm_trial_until',0)<=now
                             and now-r.get('added_on',now)>=7*86400
                             and now-history.get(r['hash'],{}).get('last_probe_at',0)>=6*3600]
    probe = min(probes,key=lambda r:(history.get(r['hash'],{}).get('last_probe_at',0),
                                     r.get('added_on',now),r['hash'])) if probes else None
    repair_pool=repairing or [r for r in unknown if r is not metadata
        and history.get(r['hash'],{}).get('metadata_failures',0)>=METADATA_REPAIR_FAILURES
        and history.get(r['hash'],{}).get('metadata_trial_until',0)<=now]
    metadata_repair=None
    if repair_pool and max_downloads>=3+bool(trial):
        metadata_repair=min(repair_pool,key=lambda r:(history.get(r['hash'],{}).get('metadata_repair_at',0),
            history.get(r['hash'],{}).get('metadata_trial_at',0),r['hash']))
        probe=metadata_repair;probe_reason='Metadata repair observation'
    if max_downloads==1 and trial:probe=None
    # Keep a regular completion slot when metadata and a speed trial coexist.
    if metadata and max_downloads<=2+bool(trial):probe=None
    reserved = {r['hash'] for r in (trial,probe,metadata) if r}
    regular=completion_order(known,rows,history,now)
    front = [r['hash'] for r in regular if r['hash'] not in reserved][:max(0,max_downloads-len(reserved))]
    if trial:
        if trial_reason=='Requested speed trial':front.insert(0,trial['hash'])
        else:
            front.append(trial['hash'])
            front=[r['hash'] for r in completion_order(
                sorted((rows[h] for h in front),key=lambda r:priority(r,history.get(r['hash'],{}),now)),
                rows,history,now)]
    if probe:front.append(probe['hash'])
    if metadata:front.insert(0,metadata['hash'])
    for r in ordered:
        if len(front)>=max_downloads:break
        if r.get('amount_left',0)<=0:continue
        if r['hash'] not in front:front.append(r['hash'])
    reasons={h:('Completion score' if estimate(rows[h],history.get(h,{}),now)['viable']
                else 'Limited source availability') for h in front}
    if probe:reasons[probe['hash']]=probe_reason
    if trial:reasons[trial['hash']]=trial_reason
    if metadata:reasons[metadata['hash']]='Metadata discovery trial'
    # Keep the next discovery opportunities visible in the real native queue,
    # rather than leaving the 149-seeder candidate at its historical #133.
    up_next = [r['hash'] for r in opportunities if r['hash'] not in front]
    front,reasons,swarm_trials,swarm_next=discover_swarm_slots(
        front,reasons,ready,rows,history,now,probe,trial,trial_budget,metadata)
    up_next=[h for h in up_next if h not in front]
    order = list(dict.fromkeys(front+up_next+swarm_next+[r['hash'] for r in ordered]))[:max(QUEUE_PREVIEW,max_downloads)]
    return {'front':front,'reasons':reasons,'order':order,'up_next':up_next,
            'swarm_trials':swarm_trials,'swarm_next':swarm_next,
            'metadata_trial':metadata['hash'] if metadata else None,
            'metadata_repair':metadata_repair['hash'] if metadata_repair else None,
            'seed_trial':trial['hash'] if trial and trial_reason=='Seeded speed trial' else None,
            'eligible':[r['hash'] for r in ordered]}


def queue_front(rows: dict, owned: dict, history: dict, now: float) -> list[str]:
    return queue_plan(rows,owned,history,now)['front']


def ranking_ready(state: dict, plan: dict, now: float) -> bool:
    if not state.get('queue_front') or now-state.get('queue_ranked_at',0)>=300:
        return True
    if plan.get('swarm_trials') and plan['front']!=state.get('queue_front'):
        return True  # Fixed discovery reservations prevent per-poll churn.
    if plan.get('metadata_trial') and plan['metadata_trial'] not in state.get('queue_front',[]):
        return True
    return any(plan['reasons'].get(h)=='Requested speed trial'
               and state.get('history',{}).get(h,{}).get('requested_trial_at',0)
               > state.get('queue_ranked_at',0) for h in plan['front'])


def commit_plan(plan: dict, rows: dict, history: dict, now: float) -> None:
    """Persist a fixed discovery deadline before submitting priority requests."""
    identity=plan.get('metadata_trial')
    if identity and identity in plan['front']:
        entry=history.setdefault(identity,{})
        if entry.get('metadata_trial_until',0)<=now:
            entry.update(metadata_trial_at=now,metadata_trial_until=now+METADATA_TRIAL_SECONDS,
                         metadata_trial_active_seconds=0,metadata_trial_running=False)
    identity=plan.get('metadata_repair')
    if identity and identity in plan['front']:
        entry=history.setdefault(identity,{})
        if entry.get('metadata_repair_until',0)<=now:
            entry.update(metadata_repair_at=now,metadata_repair_until=now+PROBE_SECONDS)
    displaced=[r for h,r in rows.items() if h in plan.get('eligible',[]) and h not in plan['front']
               and r.get('state') in RUNNING_STATES and r.get('state')!='forcedDL'
               and not r.get('force_start') and 'apollo-keep' not in str(r.get('tags','')).split(',')
               and r.get('progress',1)<0.85 and estimate(r,history.get(h,{}),now)['proven']
               and (estimate(r,history.get(h,{}),now)['eta_seconds'] or 0)>600]
    incumbent=max(displaced,key=lambda r:priority(r,history.get(r['hash'],{}),now)) if displaced else None
    for identity in plan['front']:
        if plan['reasons'].get(identity)=='Requested speed trial':
            entry=history.setdefault(identity,{})
            entry['seed_trial_at']=max(entry.get('seed_trial_at',0),entry['requested_trial_at'])
    identity = plan.get('seed_trial')
    if identity:
        entry = history.setdefault(identity,{})
        if entry.get('seed_trial_until',0)<=now:
            entry.update(seed_trial_at=now,seed_trial_until=now+SEED_TRIAL_SECONDS,
                         seed_trial_start_completed=rows[identity].get('completed',0),
                         seed_trial_replaces=incumbent['hash'] if incumbent else None)
    for rank,identity in enumerate(plan.get('swarm_trials',[])):
        entry=history.setdefault(identity,{})
        if entry.get('swarm_trial_until',0)<=now:
            entry.update(swarm_trial_at=now,swarm_trial_until=now+SWARM_TRIAL_SECONDS,
                         swarm_trial_start_completed=rows[identity].get('completed',0),
                         swarm_trial_active_seconds=0,swarm_trial_running=False,swarm_trial_rank=rank,
                         swarm_trial_replaces=incumbent['hash'] if incumbent else None)


def observe(rows: dict, history: dict, now: float) -> None:
    for identity, row in rows.items():
        old = history.get(identity)
        completed = max(0, row.get('completed', 0))
        if old is None:
            history[identity] = {'completed': completed, 'observed_at': now,
                                 'last_progress_at': now, 'rate': 0}
            old = history[identity]
        if old.get('seed_trial_until',0)<=now and 'seed_trial_start_completed' in old:
            duration=max(1,now-old['seed_trial_at'])
            moved=max(0,completed-old.pop('seed_trial_start_completed'))
            old['seed_trial_result']={'at':now,'bytes':moved,'seconds':duration,'rate':moved/duration}
        elapsed = max(1, now - old['observed_at'])
        moved = completed - old['completed']
        if old.get('metadata_trial_until',0)>0:
            running=row.get('state')=='metaDL'
            if running and old.get('metadata_trial_running') and elapsed<=60:
                old['metadata_trial_active_seconds']=old.get('metadata_trial_active_seconds',0)+elapsed
            old['metadata_trial_running']=running
            if row.get('amount_left',0)>0 or row.get('progress',0)>=1 or old['metadata_trial_until']<=now:
                active=old.get('metadata_trial_active_seconds',0)
                outcome=('metadata' if row.get('amount_left',0)>0 or row.get('progress',0)>=1
                         else 'no_metadata' if active>=SWARM_MIN_SAMPLE_SECONDS else 'unobserved')
                old['metadata_trial_result']={'at':now,'active_seconds':active,'outcome':outcome}
                if outcome=='no_metadata':old['metadata_failures']=old.get('metadata_failures',0)+1
                elif outcome=='metadata':old['metadata_failures']=0
                old['metadata_retry_at']=now+(METADATA_RETRY_SECONDS if outcome=='no_metadata'
                    else METADATA_UNOBSERVED_RETRY_SECONDS if outcome=='unobserved' else 0)
                old['metadata_trial_until']=0
        if old.get('metadata_repair_until',0)>0 and (
                row.get('amount_left',0)>0 or old['metadata_repair_until']<=now):
            old['metadata_repair_until']=0
            old['metadata_retry_at']=now+(METADATA_RETRY_SECONDS if row.get('amount_left',0)<=0 else 0)
        samples=[s for s in old.get('rate_samples',[]) if now-s['at']<=SWARM_TRIAL_SECONDS]
        running=row.get('state') in RUNNING_STATES
        if running and old.get('rate_sample_running') and 0<now-old['observed_at']<=60 and moved>=0:
            samples.append({'at':now,'seconds':elapsed,'bytes':moved})
        old['rate_samples']=samples[-12:]
        old['rate_sample_running']=running
        if 'swarm_trial_start_completed' in old:
            running=row.get('state') in RUNNING_STATES
            if running and old.get('swarm_trial_running') and elapsed<=60:
                old['swarm_trial_active_seconds']=old.get('swarm_trial_active_seconds',0)+elapsed
            old['swarm_trial_running']=running
            if old.get('swarm_trial_until',0)<=now:
                active=old.get('swarm_trial_active_seconds',0)
                sampled=max(0,completed-old.pop('swarm_trial_start_completed'))
                outcome=('progress' if sampled else 'no_progress' if active>=SWARM_MIN_SAMPLE_SECONDS else 'unobserved')
                old['swarm_trial_result']={'at':now,'bytes':sampled,'active_seconds':active,
                                          'rate':sampled/max(1,active),'outcome':outcome}
                old['swarm_trial_retry_at']=now+(SWARM_RETRY_SECONDS if outcome!='unobserved' else 600)
        if row.get('state') in RUNNING_STATES:
            if 'active_since' not in old:
                old.update(active_since=now,last_probe_at=now)
        else:
            old.pop('active_since',None)
        if moved > 0 or row.get('state') in RUNNING_STATES:
            old['rate'] = max(0,0.5 * old.get('rate', 0) + 0.5 * moved / elapsed)
            if old['rate']<1:old['rate']=0
            if moved > 0:old['last_progress_at'] = now
        old.update(completed=completed, observed_at=now)
    for identity in list(history):
        if identity not in rows:del history[identity]
