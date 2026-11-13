"""Ranking provenance, observed metadata history and public-boundary contracts."""
import copy
import json
import math
from pathlib import Path
import tempfile
import time
import unittest

from swarmtenderr.observer import optimizer, projection
from swarmtenderr_policy import admission, explanations
from swarmtenderr_policy.tuning import DEFAULT_POLICY as POLICY


def row(key='a', **changes):
    return dict(hash=key, state='queuedDL', progress=.2, amount_left=1024**3,
                size=2*1024**3, dlspeed=0, num_seeds=8, priority=1, added_on=0, **changes)


class ExplanationTests(unittest.TestCase):
    def explain(self, rows, state=None, plan=None, now=86400):
        state = {} if state is None else state
        plan = plan or admission.queue_plan(rows, rows, state.get('history', {}), now)
        result, total = explanations.build(state, rows, rows, plan, now, admission, policy=POLICY)
        return {item['torrent']: item for item in result}, state

    def test_all_600_queue_entries_have_a_rank_beyond_legacy_25_row_metrics(self):
        rows = {str(n): row(str(n)) for n in range(600)}
        result, _ = self.explain(rows)
        self.assertEqual(len(result), 600)
        self.assertEqual({r['rank'] for r in result.values()}, set(range(1, 601)))

    def test_breakdown_recombines_exact_real_score_without_changing_plan(self):
        torrent = row(); torrent.update(progress=.9, dlspeed=4*1024**2)
        rows = {'a': torrent}; state = {'queue_front': ['a'], 'queue_ranked_at': 86400}
        before = admission.queue_plan(rows, rows, {}, 86400)
        result, state = self.explain(rows, state, before)
        score = result['a']['score']
        actual = admission.estimate(torrent, {}, 86400)
        self.assertAlmostEqual(score['seconds'], actual['score_seconds'])
        self.assertAlmostEqual(score['base_seconds'] + sum(f['delta_seconds'] for f in score['factors']), score['seconds'])
        self.assertTrue(all(f['delta_seconds'] <= 0 for f in score['factors']))
        self.assertEqual(admission.queue_plan(rows, rows, state['history'], 86400), before)

    def test_more_remaining_or_lower_speed_raises_base_cost(self):
        slow=row(); slow.update(dlspeed=100)
        fast=dict(slow, hash='b', dlspeed=200)
        larger=dict(slow, hash='c', amount_left=2*slow['amount_left'])
        result, _ = self.explain({'a':slow, 'b':fast, 'c':larger})
        self.assertGreater(result['a']['score']['base_seconds'], result['b']['score']['base_seconds'])
        self.assertGreater(result['c']['score']['base_seconds'], result['a']['score']['base_seconds'])

    def test_trial_reservation_and_completion_order_are_distinct(self):
        rows = {'a':row(), 'b':row('b')}
        plan={'front':['b'], 'reasons':{'b':'Seeded speed trial'}, 'eligible':['a','b']}
        result, _ = self.explain(rows, plan=plan)
        self.assertEqual(result['b']['rank'], 1)
        self.assertEqual(result['b']['score_rank'], 2)
        self.assertEqual(result['b']['selection_reason'], 'Seeded speed trial')

    def test_capacity_and_cooldown_are_gates_not_fake_point_penalties(self):
        rows={'a':row(), 'b':row('b')}
        state={'history':{'a':{'deferred_until':90000}}, 'space_plan':{'deferred':{'b':'Waiting for library import space'}}}
        plan={'front':[], 'reasons':{}, 'eligible':['b']}
        result, _ = self.explain(rows, state, plan)
        self.assertIsNone(result['a']['rank'])
        self.assertIn('probe_cooldown', {b['code'] for b in result['a']['blockers']})
        self.assertIn('capacity', {b['code'] for b in result['b']['blockers']})

    def test_missing_metadata_has_no_invented_score_or_lifetime_failures(self):
        torrent=row(); torrent.update(amount_left=0, size=0)
        result, state = self.explain({'a':torrent})
        self.assertIsNone(result['a']['score'])
        self.assertEqual(result['a']['metadata']['attempts'], 0)
        self.assertEqual(result['a']['metadata']['failures'], 0)
        self.assertFalse(result['a']['metadata']['known'])
        self.assertIn('metadata', {b['code'] for b in result['a']['blockers']})

    def test_observed_metadata_attempt_no_metadata_then_success(self):
        entry={}; torrent=row(); torrent.update(amount_left=0, state='metaDL')
        for now in (100,130,160): explanations.observe_metadata(torrent,entry,now,admission.RUNNING_STATES)
        torrent['state']='queuedDL'
        _, record=explanations.observe_metadata(torrent,entry,190,admission.RUNNING_STATES)
        self.assertEqual((record['attempts'],record['failures'],record['successes']), (1,1,0))
        self.assertEqual(record['last_outcome'],'no_metadata')
        torrent['state']='metaDL'; explanations.observe_metadata(torrent,entry,220,admission.RUNNING_STATES)
        torrent['amount_left']=1024
        _,record=explanations.observe_metadata(torrent,entry,250,admission.RUNNING_STATES)
        self.assertEqual((record['attempts'],record['failures'],record['successes']), (2,1,1))
        self.assertEqual(record['last_outcome'],'metadata')
        self.assertEqual(json.loads(json.dumps(entry)),entry)

    def test_observation_gap_never_becomes_a_metadata_failure(self):
        entry={}; torrent=row(); torrent.update(amount_left=0,state='metaDL')
        for now in (100,130,160): explanations.observe_metadata(torrent,entry,now,admission.RUNNING_STATES)
        torrent['state']='queuedDL'
        _,record=explanations.observe_metadata(torrent,entry,1000,admission.RUNNING_STATES)
        self.assertEqual(record['failures'],0)
        self.assertEqual(record['unobserved'],1)

    def test_projection_is_allowlisted_and_keeps_nested_secrets_out(self):
        result,_=self.explain({'a':row()})
        raw=result['a']; raw['private_path']='PRIVATE';raw['metadata']['password']='PRIVATE';raw['score']['credential']='PRIVATE'
        raw['blockers'].append({'code':'test', 'label':'test', 'detail':'test', 'secret':'PRIVATE'})
        raw['signals']['token']='PRIVATE'
        public=explanations.sanitize([raw])
        self.assertNotIn('PRIVATE',json.dumps(public))
        raw['score']['seconds']=math.inf
        self.assertEqual(explanations.sanitize([raw])[0]['score']['seconds'],0)
        self.assertEqual(explanations.sanitize([None,{}]),[])

    def test_external_feed_joins_full_ranking_and_marks_staleness(self):
        result,_=self.explain({'a':row()},now=time.time())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'feed.json'
            path.write_text(json.dumps({'optimizer':{'checked_at':time.time(),'rankings':list(result.values()),'ranking_total':1}}))
            engine=optimizer(path)
            projected=projection([row()],engine)[0]
            self.assertFalse(engine['stale'])
            self.assertEqual(projected['ranking']['rank'],1)
            self.assertEqual(projected['reason'],result['a']['reason'])
            path.write_text(json.dumps({'optimizer':{'checked_at':1,'rankings':list(result.values())}}))
            self.assertTrue(optimizer(path)['stale'])

    def test_old_feed_remains_compatible_but_does_not_invent_a_rank(self):
        item=projection([row()],{'rows':[{'torrent':'a','decision':'Waiting','reason':'Old reason'}]})[0]
        self.assertIsNone(item['ranking'])
        self.assertEqual(item['reason'],'Old reason')

    def test_public_bounds_keep_metrics_and_websocket_payload_finite(self):
        result,_=self.explain({'a':row()})
        public=explanations.sanitize([result['a']] * (explanations.MAX_RANKINGS+1))
        self.assertLessEqual(len(public),explanations.MAX_RANKINGS)
        self.assertLessEqual(len(json.dumps(public).encode()),explanations.MAX_PUBLIC_BYTES)
