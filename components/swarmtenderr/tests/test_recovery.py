from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest

from database_fixture import database_url
from swarmtenderr.app import create_app
from swarmtenderr.maintenance import backup, restore
from swarmtenderr.settings import Settings


class RecoveryTests(unittest.TestCase):
    def test_private_backup_restores_identity_encrypted_settings_and_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            settings = Settings(database='sqlite:///' + str(source / 'state.db'), data_dir=str(source), app_password='test-only-recovery-password')
            app = create_app(settings, False)
            token = app.auth.login('admin', 'test-only-recovery-password', 'fixture')
            app.runtime.save({'qbit_password': 'private-test-value', 'timezone': 'America/New_York'}, 0, 'fixture')
            now = time.time()
            app.store.snapshot({'observed_at': now, 'download_speed': 0, 'upload_speed': 0},
                [{'hash': 'a'*40, 'name': 'Linux', 'progress': 1, 'size': 1234, 'completion_on': now-30, 'added_on': now-300, 'downloaded': 1234}])
            artifact = Path(directory) / 'backup.json'
            backup(settings, artifact)
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('private-test-value', artifact.read_text())
            destination = replace(settings, database=database_url(self, target / 'state.db'), data_dir=str(target))
            restore(destination, artifact)
            recovered = create_app(destination, False)
            self.assertEqual(recovered.runtime.values()['qbit_password'], 'private-test-value')
            self.assertEqual(recovered.runtime.values()['timezone'], 'America/New_York')
            self.assertEqual(recovered.analytics.summary('UTC')['total_bytes'], 1234)
            self.assertIsNone(recovered.auth.current(token))
            self.assertTrue(recovered.auth.login('admin', 'test-only-recovery-password', 'fixture'))
            self.assertEqual(recovered.runtime.values()['policy_mode'], 'observe')
            with self.assertRaises(ValueError):
                restore(destination, artifact)
            with self.assertRaises(FileExistsError):
                backup(settings, artifact)

    def test_restore_refuses_public_backup_and_nonempty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = Settings(database='sqlite:///' + str(path/'source.db'), data_dir=str(path/'source'), app_password='test-only-recovery-password')
            create_app(settings, False)
            artifact = path/'backup.json'
            backup(settings, artifact)
            artifact.chmod(0o644)
            with self.assertRaises(ValueError):
                restore(replace(settings, data_dir=str(path/'target')), artifact)
            artifact.chmod(0o600)
            with self.assertRaises(ValueError):
                restore(replace(settings, data_dir=str(path/'target')), artifact)
            self.assertFalse((path/'target'/'secret.key').exists())
