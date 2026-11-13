"""Real socket push, cookie sessions, origin checks, resync and revocation."""
import json
import sys
import time

import httpx
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


def main():
    url = sys.argv[1]
    origin = 'http://localhost:8787'
    base = url.replace('ws://', 'http://').replace('wss://', 'https://').split('/api/')[0]
    auth = len(sys.argv) > 2 and sys.argv[2] == 'authenticated'
    client = httpx.Client(base_url=base, trust_env=False, timeout=10)
    response = client.post('/api/v1/auth/login', json={'username': 'admin' if auth else 'demo',
        'password': 'test-only-dashboard-pass' if auth else 'demo'}, headers={'Origin': origin})
    assert response.status_code == 200, response.text
    session = response.json()
    headers = {'Origin': origin, 'X-CSRF-Token': session['csrf']}
    cookie = '; '.join(f'{key}={value}' for key, value in client.cookies.items())
    for bad_origin, cookies in [('https://untrusted.example', cookie), (origin, '')]:
        try:
            with connect(url, origin=bad_origin, additional_headers={'Cookie': cookies}, open_timeout=5):
                raise AssertionError('Unauthorized socket handshake succeeded')
        except InvalidStatus as error:
            assert error.response.status_code == 403
    with connect(url, origin=origin, additional_headers={'Cookie': cookie}, open_timeout=5) as socket:
        first = json.loads(socket.recv(timeout=10))
        assert first['type'] == 'snapshot'
        config = client.get('/api/v1/settings').json()
        next_zone = 'Europe/London' if config['values']['timezone'] == 'UTC' else 'UTC'
        changed = client.patch('/api/v1/settings', json={'revision': config['revision'], 'changes': {'timezone': next_zone}}, headers=headers)
        assert changed.status_code == 200, changed.text
        for _ in range(10):
            pushed = json.loads(socket.recv(timeout=10))
            if pushed['type'] == 'snapshot' and pushed['status']['configuration_revision'] == config['revision'] + 1:
                assert pushed['statistics']['timezone'] == next_zone
                break
        else:
            raise AssertionError('Saved configuration did not arrive over the socket')
    # Reconnect receives an authoritative snapshot, with no browser refresh loop.
    with connect(url, origin=origin, additional_headers={'Cookie': cookie}, open_timeout=5) as socket:
        assert json.loads(socket.recv(timeout=10))['status']['timezone'] == next_zone
        assert client.post('/api/v1/auth/logout', json={}, headers=headers).status_code == 200
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.recv(timeout=max(.1, deadline-time.monotonic()))
            except ConnectionClosed as error:
                assert error.code == 4401
                break
            if time.monotonic() > deadline:
                raise AssertionError('Logout did not revoke the established socket')
    assert client.get('/api/v1/status').status_code == 401
    client.close()
    print('Real WebSocket cookie login, origin isolation, pushed configuration, resync and logout revocation passed')


if __name__ == '__main__':
    main()
