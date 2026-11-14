"""Durable manual metadata lifecycle, ownership and uncertain-response recovery."""
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from database_fixture import database_url
from swarmtenderr.metadata import MetadataRequests, has_metadata
from swarmtenderr.qbit import UpstreamError
from swarmtenderr.store import Store, Conflict


HASH = 'b' * 40


class Client:
    url, username = 'http://fixture:8080', 'admin'

    def __init__(self, stopped=False):
        self.row = dict(hash=HASH, state='stoppedDL' if stopped else 'queuedDL', progress=0,
                        size=0, total_size=0, amount_left=0, force_start=False)
        self.calls, self.fail = [], {}

    def torrent(self, identity):
        if identity != HASH or self.row is None:
            raise ValueError('Torrent no longer exists')
        return copy.deepcopy(self.row)

    def rows(self):
        return [copy.deepcopy(self.row)] if self.row else []

    def action(self, identity, action, *, eligible=None, manual=False):
        assert manual
        row = self.torrent(identity)
        if eligible and not eligible(row):
            raise ValueError('Torrent changed')
        self.calls.append(action)
        failure = self.fail.pop(action, None)
        if failure == 'before':
            raise UpstreamError('Lost response')
        if action == 'force_start':
            self.row.update(force_start=True, state='forcedMetaDL')
        elif action == 'unforce':
            self.row.update(force_start=False, state='downloading' if has_metadata(self.row) else 'queuedDL')
        elif action == 'pause':
            self.row.update(force_start=False, state='stoppedDL')
        elif action == 'resume':
            self.row.update(state='metaDL')
        if failure == 'after':
            raise UpstreamError('Lost response')


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(database_url(self, Path(self.directory.name)/'state.db'))
        self.store.initialize()
        self.manager = MetadataRequests(self.store, 'fixture-owner')
        self.client = Client()

    def start(self, duration=180):
        audit = self.store.begin_action(HASH, 'fetch_metadata', 'operator')
        self.manager.start(self.client, HASH, audit, duration)
        self.store.finish_action(audit, 'accepted')
        return audit

    def poll(self):
        return self.manager.poll(self.client, self.client.rows())

    def record(self):
        return self.store.metadata_requests()[HASH]

    def expire(self):
        with patch('swarmtenderr.metadata.time.time', return_value=self.record()['deadline'] + 1):
            self.poll()

    def test_metadata_acquired_releases_force_and_confirms_audit(self):
        audit = self.start()
        self.assertEqual(self.client.calls, ['force_start', 'reannounce'])
        self.client.row.update(size=4096, total_size=4096, amount_left=4096)
        self.poll()
        self.assertFalse(self.client.row['force_start'])
        self.poll()
        self.assertEqual(self.record()['phase'], 'acquired')
        self.assertEqual(self.store.history()['actions'][0]['id'], audit)
        self.assertEqual(self.store.history()['actions'][0]['outcome'], 'confirmed')
        self.assertNotIn('client', self.manager.public(self.client)[HASH])

    def test_timeout_releases_without_claiming_metadata_success(self):
        self.start()
        self.expire()
        self.poll()
        self.assertFalse(self.client.row['force_start'])
        self.assertEqual(self.record()['phase'], 'timed_out')
        self.assertFalse(has_metadata(self.client.row))

    def test_stopped_torrent_is_stopped_again_after_acquisition(self):
        self.client = Client(stopped=True)
        self.start()
        self.client.row.update(size=4096)
        for _ in range(3):
            self.poll()
        self.assertEqual(self.client.row['state'], 'stoppedDL')
        self.assertEqual(self.client.calls, ['force_start', 'reannounce', 'unforce', 'pause'])
        self.assertEqual(self.record()['phase'], 'acquired')

    def test_older_client_force_flag_does_not_implicitly_resume(self):
        class OlderClient(Client):
            def action(self, identity, action, **options):
                previous_state = self.row['state']
                super().action(identity, action, **options)
                if action == 'force_start' and previous_state in {'stoppedDL', 'pausedDL'}:
                    self.row['state'] = previous_state
                elif action == 'resume':
                    self.row.update(state='metaDL', force_start=False)
        self.client = OlderClient(stopped=True)
        self.start()
        self.assertNotEqual(self.client.row['state'], 'stoppedDL')
        self.assertTrue(self.client.row['force_start'])
        self.assertEqual(self.client.calls[:3], ['force_start', 'resume', 'force_start'])

    def test_restart_recovers_expired_attempt_without_replaying_start(self):
        self.start()
        reopened = Store(self.store.url if self.store.postgres else 'sqlite:///' + self.store.url)
        reopened.initialize()
        self.store.controller_lease('fixture-owner', release=True)
        self.manager = MetadataRequests(reopened, 'replacement-owner')
        self.expire()
        self.poll()
        self.assertEqual(self.record()['phase'], 'timed_out')
        self.assertEqual(self.client.calls.count('force_start'), 1)

    def test_duplicate_attempt_cannot_create_another_force_request(self):
        self.start()
        with self.assertRaises(Conflict):
            self.start()
        self.assertEqual(self.client.calls.count('force_start'), 1)

    def test_existing_force_start_and_existing_metadata_are_preserved(self):
        for change in ({'force_start':True}, {'total_size':4096}):
            self.client.row.update(change)
            with self.assertRaises(ValueError):
                self.start()
            self.client.row = Client().row
        self.assertEqual(self.client.calls, [])

    def test_lost_start_response_is_observed_without_replay(self):
        self.client.fail['force_start'] = 'after'
        with self.assertRaises(UpstreamError):
            self.start()
        self.poll()
        self.assertEqual(self.record()['phase'], 'fetching')
        self.assertEqual(self.client.calls, ['force_start'])
        self.expire()
        self.poll()
        self.assertEqual(self.record()['phase'], 'timed_out')

    def test_lost_cleanup_response_is_not_replayed_until_explicit_cancel(self):
        self.start()
        self.client.fail['unforce'] = 'before'
        self.expire()
        for _ in range(3):
            self.poll()
        self.assertEqual(self.client.calls.count('unforce'), 1)
        self.assertEqual(self.record()['phase'], 'attention')
        audit = self.store.begin_action(HASH, 'cancel_metadata', 'operator')
        self.manager.cancel(self.client, HASH, audit)
        self.poll()
        self.assertEqual(self.client.calls.count('unforce'), 2)
        self.assertEqual(self.record()['phase'], 'cancelled')

    def test_cleanup_that_succeeded_despite_lost_response_is_confirmed(self):
        self.start()
        self.client.fail['unforce'] = 'after'
        self.expire()
        self.poll()
        self.assertEqual(self.record()['phase'], 'timed_out')
        self.assertEqual(self.client.calls.count('unforce'), 1)

    def test_explicit_cancel_restores_original_stopped_state(self):
        self.client = Client(stopped=True)
        self.start()
        audit = self.store.begin_action(HASH, 'cancel_metadata', 'operator')
        self.manager.cancel(self.client, HASH, audit)
        for _ in range(2):
            self.poll()
        self.assertEqual(self.client.row['state'], 'stoppedDL')
        self.assertEqual(self.record()['phase'], 'cancelled')

    def test_explicit_cancel_can_retry_uncertain_stop_restoration(self):
        self.client = Client(stopped=True)
        self.start()
        self.expire()
        self.client.fail['pause'] = 'before'
        self.poll()
        self.assertEqual(self.record()['phase'], 'attention')
        audit = self.store.begin_action(HASH, 'cancel_metadata', 'operator')
        self.manager.cancel(self.client, HASH, audit)
        self.poll()
        self.assertEqual(self.client.row['state'], 'stoppedDL')
        self.assertEqual(self.record()['phase'], 'cancelled')

    def test_native_manual_stop_is_not_restarted(self):
        self.start()
        self.poll()
        self.client.row.update(force_start=False, state='stoppedDL')
        self.expire()
        self.assertEqual(self.record()['phase'], 'interrupted')
        self.assertEqual(self.client.calls, ['force_start', 'reannounce'])

    def test_new_manual_intent_takes_ownership_from_temporary_attempt(self):
        self.start()
        self.manager.manual_override(HASH, 'force_start')
        self.expire()
        self.assertTrue(self.client.row['force_start'])
        self.assertFalse(self.record()['active'])

    def test_manual_pause_and_resume_do_not_inherit_temporary_force_start(self):
        for action in ('pause', 'resume'):
            self.client = Client()
            self.start()
            self.manager.manual_override(HASH, action, self.client)
            self.client.action(HASH, action, manual=True)
            self.poll()
            self.assertFalse(self.client.row['force_start'])
            self.assertEqual(self.client.row['state'], 'stoppedDL' if action == 'pause' else 'metaDL')
            self.assertFalse(self.record()['active'])

    def test_uncertain_manual_stop_is_not_replayed_by_metadata_cleanup(self):
        self.start()
        self.manager.manual_override(HASH, 'pause', self.client)
        self.client.fail['pause'] = 'before'
        with self.assertRaises(UpstreamError):
            self.client.action(HASH, 'pause', manual=True)
        for _ in range(3):
            self.poll()
        self.assertEqual(self.client.calls.count('pause'), 1)

    def test_changed_client_is_never_mutated_by_previous_attempt(self):
        self.start()
        original = self.client
        self.client = Client()
        self.client.url = 'http://other-client:8080'
        self.poll()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.record()['phase'], 'attention')
        self.client = original
        self.expire()
        self.poll()
        self.assertEqual(self.record()['phase'], 'timed_out')

    def test_second_instance_cannot_start_or_cleanup_owned_attempt(self):
        self.start()
        other = MetadataRequests(self.store, 'standby-owner')
        self.assertFalse(other.poll(self.client, self.client.rows()))
        with self.assertRaises(Conflict):
            other.manual_override(HASH, 'pause')
        self.assertEqual(self.client.calls, ['force_start', 'reannounce'])

    def test_removed_torrent_finishes_without_client_mutation(self):
        self.start()
        self.client.row = None
        self.poll()
        self.assertEqual(self.record()['phase'], 'removed')
        self.assertEqual(self.client.calls, ['force_start', 'reannounce'])


if __name__ == '__main__':
    unittest.main()
