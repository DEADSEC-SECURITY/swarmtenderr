"""Controller ownership, capacity safety, measured ranking and native protections."""
import copy
from pathlib import Path
import tempfile
import time
import unittest

from database_fixture import database_url
from swarmtenderr.app import create_app
from swarmtenderr.controller import Controller
from swarmtenderr.settings import Settings
from swarmtenderr_policy import admission, space_planner

GIB = 1024**3


def row(index, state='queuedDL', **fields):
    return {'hash': f'{index:040x}', 'name': f'Linux {index}', 'category': 'linux', 'state': state,
            'progress': .2, 'amount_left': 2*GIB, 'size': 3*GIB, 'completed': GIB,
            'priority': index, 'num_seeds': 10, 'dlspeed': 0, 'added_on': time.time()-500, **fields}


class Client:
    def __init__(self, rows, free=200*GIB):
        self.rows, self.free = rows, free
        self.prefs = {'queueing_enabled': True, 'dont_count_slow_torrents': False,
                      'max_active_downloads': 5, 'max_active_uploads': 11, 'max_active_torrents': -1}
        self.calls = []
        self.fail = False

    def preferences(self):
        return dict(self.prefs)

    def system_state(self):
        return {'free_space_on_disk': self.free}

    def download_cap(self, slots, *, expected=None):
        self.calls.append(('ceiling', slots))
        if self.fail:
            raise TimeoutError()
        self.prefs['max_active_downloads'] = slots

    def action(self, identity, action, *, eligible=None):
        self.calls.append((identity, action))
        if self.fail:
            raise TimeoutError()
        selected = next(item for item in self.rows if item['hash'] == identity)
        for item in self.rows:
            if item['priority'] < selected['priority']:
                item['priority'] += 1
        selected['priority'] = 1


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        self.app = create_app(Settings(database=database_url(self, path/'state.db'), data_dir=str(path),
            app_password='test-only-policy-password'), False)
        self.controller = Controller(self.app.settings, self.app.store)
        self.config = self.app.runtime.values() | {'managed_categories': ['linux'], 'download_slots': 3}

    def test_observe_recommends_without_mutations_and_survives_restart(self):
        client = Client([row(1), row(2)])
        result = self.controller.poll(client, client.rows, self.config)
        self.assertEqual(client.calls, [])
        self.assertTrue(result['connected'])
        self.assertTrue(result['rows'])
        self.assertIn(row(1)['hash'], self.app.store.policy_state()['history'])
        other = Controller(self.app.settings, self.app.store)
        self.assertIn('Another instance', other.poll(client, client.rows, self.config)['reason'])
        self.app.store.controller_lease(self.controller.owner, release=True)
        self.assertTrue(other.poll(client, client.rows, self.config)['rows'])

    def test_enforcement_preserves_manual_stops_forces_tags_categories_and_uploads(self):
        rows = [row(1, priority=9), row(2, 'stoppedDL'), row(3, 'forcedDL'), row(4, tags='swarmtenderr-keep'),
                row(5, category='manual'), row(6, 'uploading', progress=1), row(7, tags='private-keep')]
        client = Client(rows)
        original = copy.deepcopy(client.prefs)
        config = self.config | {'policy_mode': 'enforce', 'excluded_tags': ['private-keep']}
        self.controller.poll(client, rows, config)
        self.assertIn((row(1)['hash'], 'prioritize'), client.calls)
        self.assertFalse(any(item[0] == row(i)['hash'] for item in client.calls for i in range(2, 8)))
        self.assertEqual(client.prefs['max_active_uploads'], original['max_active_uploads'])
        self.assertEqual(client.prefs['max_active_torrents'], original['max_active_torrents'])
        self.assertFalse(any('pause' in item or 'resume' in item or 'delete' in item for item in client.calls))
        self.controller.poll(client, rows, config)
        self.assertTrue(all(item['outcome'] == 'confirmed' for item in self.app.store.history()['actions']))

    def test_aggregate_reservations_and_low_capacity_do_not_delete_or_pause(self):
        client = Client([row(1, amount_left=30*GIB), row(2, amount_left=30*GIB)], free=90*GIB)
        result = self.controller.poll(client, client.rows, self.config | {'policy_mode': 'enforce'})
        self.assertEqual(len(result['capacity']['selected']), 1)
        self.assertLessEqual(result['capacity']['ssd_reserved_bytes'], result['capacity']['ssd_budget_bytes'])
        self.assertEqual(client.prefs['max_active_downloads'], 1)
        self.assertFalse(result['capacity']['library_capacity_applicable'])
        self.assertIsNone(result['capacity']['library_budget_bytes'])

    def test_zero_cap_is_owned_and_observe_restores_only_our_cap(self):
        client = Client([row(1)], free=20*GIB)
        self.controller.poll(client, client.rows, self.config | {'policy_mode': 'enforce'})
        self.assertEqual(client.prefs['max_active_downloads'], 0)
        self.controller.poll(client, client.rows, self.config)
        self.assertEqual(client.prefs['max_active_downloads'], 5)
        self.controller.poll(client, client.rows, self.config | {'policy_mode': 'enforce'})
        client.prefs['max_active_downloads'] = 7
        self.controller.poll(client, client.rows, self.config)
        self.assertEqual(client.prefs['max_active_downloads'], 7)

    def test_timeout_is_durable_and_never_blindly_retried(self):
        client = Client([row(1, priority=9)])
        client.fail = True
        config = self.config | {'policy_mode': 'enforce'}
        with self.assertRaises(TimeoutError):
            self.controller.poll(client, client.rows, config)
        count = len(client.calls)
        result = self.controller.poll(client, client.rows, config)
        self.assertEqual(len(client.calls), count)
        self.assertIn('could not be confirmed', result['reason'])
        self.assertEqual(self.app.store.history()['actions'][0]['outcome'], 'uncertain')

    def test_native_queue_safety_is_required(self):
        client = Client([row(1)])
        client.prefs['dont_count_slow_torrents'] = True
        result = self.controller.poll(client, client.rows, self.config | {'policy_mode': 'enforce'})
        self.assertEqual(client.calls, [])
        self.assertIn('count slow torrents', result['reason'])

    def test_measured_speed_fairness_metadata_and_cooldown(self):
        now = time.time()
        rows = {item['hash']: item for item in [row(1, dlspeed=1024**2), row(2, added_on=now-30*86400),
            row(3, amount_left=0, size=0), row(4, amount_left=0, size=0)]}
        proposed = admission.queue_plan(rows, rows, {}, now, max_downloads=3)
        self.assertLessEqual(sum(rows[key]['amount_left'] == 0 for key in proposed['front']), 1)
        history = {}
        admission.observe(rows, history, now)
        admission.commit_plan(proposed, rows, history, now)
        expiry = history.get(proposed.get('metadata_trial'), {}).get('metadata_trial_until')
        admission.commit_plan(proposed, rows, history, now+30)
        self.assertEqual(history.get(proposed.get('metadata_trial'), {}).get('metadata_trial_until'), expiry)
        state = {'queue_front': proposed['front'], 'queue_ranked_at': now, 'history': history}
        self.assertFalse(admission.ranking_ready(state, proposed, now+30))
        self.assertTrue(admission.ranking_ready(state, proposed, now+301))
        estimate = admission.estimate(row(1, dlspeed=1024**2), {}, now)
        self.assertEqual(estimate['eta_seconds'], 2048)
        self.assertIn('speed', estimate['source'].lower())

    def test_library_reserve_and_external_jobs_are_charged_first(self):
        rows = {item['hash']: item for item in [row(1), row(2, 'downloading', category='manual', amount_left=100*GIB)]}
        owned = {row(1)['hash']: {}}
        proposed = admission.queue_plan(rows, owned, {}, time.time())
        capacity = space_planner.plan(rows, owned, {}, 3, proposed, 150*GIB, 2*GIB, 200*GIB, 500*GIB, time.time())
        self.assertEqual(capacity['selected'], [])
        self.assertGreater(capacity['external_reserved_bytes'], 100*GIB)
        capacity = space_planner.plan(rows, owned, {}, 3, proposed, 500*GIB, 2*GIB, 51*GIB, 500*GIB, time.time())
        self.assertEqual(capacity['selected'], [])
