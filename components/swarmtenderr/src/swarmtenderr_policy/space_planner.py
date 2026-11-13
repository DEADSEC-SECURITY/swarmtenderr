"""Reserve finishable batches without crediting speculative imports or deletion."""
from __future__ import annotations

import math

GIB = 1024**3
RESERVE = 50 * GIB
OTHER_BUFFER = 5 * GIB
UNKNOWN_RESERVATION = 2 * GIB
STATE_PATH = '/srv/cinema-state/state/download-space-guard.json'


def requested_slots(preferences, state):
    """A controller-applied cap must not overwrite the operator's ceiling."""
    current=preferences.get('max_active_downloads',3)
    previous=state.get('space_plan',{})
    if current in (previous.get('applied_slots'),previous.get('observed_slots')):
        current=previous.get('requested_slots',current)
    if type(current) is not int or not 1<=current<=100:
        raise RuntimeError('Use a finite requested download limit between 1 and 100')
    return current


def preserved_slots(current):
    """Only accept a zero-slot cap when durable, root-owned state proves ownership."""
    if current!=0:return current
    import json,stat
    from pathlib import Path
    path=Path(STATE_PATH);metadata=path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=0 or stat.S_IMODE(metadata.st_mode)!=0o600:
        raise RuntimeError('Capacity ownership record is unsafe')
    previous=json.loads(path.read_text()).get('space_plan',{})
    if 0 not in (previous.get('applied_slots'),previous.get('observed_slots')) or not 1<=previous.get('requested_slots',0)<=100:
        raise RuntimeError('Zero download slots are not owned by the capacity planner')
    return 0


def requirements(row):
    remaining=row.get('amount_left',0)
    # Missing metadata cannot be booked as a zero-byte download. Discovery and
    # a separate repeated-failure repair probe each pay this same reservation.
    unknown=not isinstance(remaining,(float,int)) or not math.isfinite(remaining) or remaining<=0
    remaining=UNKNOWN_RESERVATION if unknown else int(remaining)
    total=max(remaining,row.get('size',0),row.get('total_size',0),row.get('completed',0)+remaining)
    return {'ssd':math.ceil(remaining*1.02)+64*1024**2,'library':math.ceil(total*1.02),
            'unknown':unknown}


def plan(rows, owned, history, requested, proposed, available, used, library_available, work_budget, now):
    from . import admission
    budget=max(0,min(available-RESERVE-OTHER_BUFFER,work_budget-used-OTHER_BUFFER))
    library_budget=max(0,library_available-RESERVE)
    # External/manual work is never reordered or stopped here, but its remaining
    # allocation must be paid before any automatic work can be promised space.
    external=[r for h,r in rows.items() if r.get('progress',1)<1 and r.get('state') in admission.RUNNING_STATES
              and (h not in owned or r.get('force_start') or r.get('state')=='forcedDL'
                   or 'apollo-keep' in str(r.get('tags','')).split(','))]
    external_ssd=sum(requirements(r)['ssd'] for r in external)
    external_library=sum(requirements(r)['library'] for r in external)
    budget=max(0,budget-external_ssd);library_budget=max(0,library_budget-external_library)
    # Completed torrents remain on the SSD through imports/seeding; their bytes
    # are already in actual usage. Still reserve destination capacity for copies.
    pending_library=sum(max(0,r.get('size',0),r.get('total_size',0),r.get('completed',0))
                        for r in rows.values() if r.get('progress',0)>=1)
    library_budget=max(0,library_budget-pending_library)
    eligible={h:r for h,r in rows.items() if h in owned and r.get('progress',1)<1
              and r.get('state') in admission.ACTIVE_STATES|admission.STOPPED_STATES
              and r.get('state')!='forcedDL' and not r.get('force_start')
              and 'apollo-keep' not in str(r.get('tags','')).split(',')}
    ordered=list(dict.fromkeys(proposed['front']+proposed['order']+
        sorted(eligible,key=lambda h:admission.priority(eligible[h],history.get(h,{}),now))))
    selected=[];deferred={};ssd=0;library=0;unknown=0;discovery_unknown=0
    # Forced downloads bypass native limits; other manual active jobs consume a
    # native slot. Both still consume the disk reservation above.
    external_slots=sum(r.get('state')!='forcedDL' and not r.get('force_start') for r in external)
    slots=max(0,requested-external_slots)
    for identity in ordered:
        if identity not in eligible:continue
        row=eligible[identity];cost=requirements(row)
        # Never turn repair/discovery cooldowns into ordinary admission.
        if identity not in proposed.get('eligible',eligible):continue
        repair=identity==proposed.get('metadata_repair')
        if cost['unknown'] and not repair and discovery_unknown:
            deferred[identity]='Waiting for metadata slot';continue
        if ssd+cost['ssd']>budget:
            deferred[identity]='Waiting for SSD completion space';continue
        if library+cost['library']>library_budget:
            deferred[identity]='Waiting for library import space';continue
        if len(selected)>=slots:continue
        selected.append(identity);ssd+=cost['ssd'];library+=cost['library'];unknown+=cost['unknown']
        discovery_unknown+=cost['unknown'] and not repair
    # Zero native download slots is supported by qBittorrent/libtorrent. It keeps
    # jobs Queued and leaves the independent upload queue running.
    effective=min(requested,len(selected)+external_slots)
    deferred_costs=[requirements(rows[h])['ssd'] for h,reason in deferred.items()
                    if reason=='Waiting for SSD completion space']
    target=(RESERVE+OTHER_BUFFER+external_ssd+ssd+min(deferred_costs)) if deferred_costs else 0
    if target>work_budget:target=0  # Cleanup cannot create a larger SSD.
    return {'requested_slots':requested,'applied_slots':effective,'selected':selected,
            'ssd_budget_bytes':budget,'ssd_reserved_bytes':ssd,'library_budget_bytes':library_budget,
            'library_reserved_bytes':library,'external_reserved_bytes':external_ssd,
            'reserve_bytes':RESERVE,'other_buffer_bytes':OTHER_BUFFER,'deferred':deferred,
            'metadata_trials':unknown,'checked_at':now,'limited':effective<requested,
            'recovery_target_available_bytes':target}
