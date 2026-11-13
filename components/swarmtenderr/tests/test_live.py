"""Established streams recheck account state rather than trusting the handshake."""
from pathlib import Path
import tempfile
import time
import unittest

import asyncio
from contextlib import suppress
import json

from database_fixture import database_url
from swarmtenderr.live import create_app
from swarmtenderr.settings import Settings


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        self.app = create_app(Settings(database=database_url(self, path/'state.db'), data_dir=str(path),
            app_password='test-only-live-password'), start_observer=False)
        self.service = self.app.state.service
        self.identity = self.service.auth.create_user({'username': 'viewer', 'display_name': 'Viewer', 'role': 'viewer',
            'password': 'test-only-viewer-password'}, 'fixture')
        self.token = self.service.auth.login('viewer', 'test-only-viewer-password', 'fixture')
        self.headers = {'Origin': 'http://localhost:8787', 'Cookie': 'swarmtenderr_session='+self.token}

    async def exchange(self, headers, mutation=None):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        incoming.put_nowait({'type': 'websocket.connect'})
        scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws',
                 'path': '/api/v1/live', 'raw_path': b'/api/v1/live', 'query_string': b'',
                 'root_path': '', 'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()],
                 'client': ('127.0.0.1', 1234), 'server': ('localhost', 8787), 'subprotocols': []}
        task = asyncio.create_task(self.app(scope, incoming.get, outgoing.put))
        try:
            first = await asyncio.wait_for(outgoing.get(), 5)
            if mutation is None:
                return first
            self.assertEqual(first['type'], 'websocket.accept')
            frame = await asyncio.wait_for(outgoing.get(), 5)
            self.assertEqual(json.loads(frame['text'])['type'], 'snapshot')
            mutation()
            closed = await asyncio.wait_for(outgoing.get(), 5)
            self.assertEqual(closed['type'], 'websocket.close')
            self.assertEqual(closed['code'], 4401)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def assert_revoked(self, mutation):
        asyncio.run(self.exchange(self.headers, mutation))

    def test_deactivation_closes_an_established_socket(self):
        self.assert_revoked(lambda: self.service.auth.update_user(self.identity, {'active': False}, 'fixture'))

    def test_role_change_closes_an_established_socket(self):
        self.assert_revoked(lambda: self.service.auth.update_user(self.identity, {'role': 'operator'}, 'fixture'))

    def test_idle_expiry_closes_an_established_socket(self):
        def expire():
            with self.service.store.connection() as connection:
                self.service.store.execute(connection, 'UPDATE swarmtenderr_sessions SET seen=?', (time.time()-4000,))
        self.assert_revoked(expire)

    def test_origin_and_missing_session_are_denied(self):
        for headers in ({'Origin': 'http://localhost:8787'}, self.headers | {'Origin': 'https://other.example'}):
            frame = asyncio.run(self.exchange(headers))
            self.assertEqual(frame['type'], 'websocket.close')
            self.assertEqual(frame['code'], 1008)
