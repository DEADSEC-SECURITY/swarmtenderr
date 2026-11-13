"""Real socket push, cookie sessions, origin checks, resync and revocation."""
import json
import sys
import time

import httpx
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus



def assert_revoked(socket):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.recv(timeout=max(.1, deadline-time.monotonic()))
        except ConnectionClosed as error:
            assert error.code == 4401
            return
    raise AssertionError('Established socket retained revoked access')


def account_flows(admin, headers, url, origin, administrator):
    password = 'test-only-new-account-pass'
    accounts = {}
    for role in ('viewer', 'operator'):
        response = admin.post('/api/v1/users', json={'username': 'acceptance-' + role,
            'display_name': 'Acceptance ' + role, 'role': role, 'password': password}, headers=headers)
        assert response.status_code == 200, response.text
        accounts[role] = next(user for user in response.json()['users'] if user['username'] == 'acceptance-' + role)
    def sign_in(role, secret=password):
        client = httpx.Client(base_url=str(admin.base_url), trust_env=False, timeout=10)
        response = client.post('/api/v1/auth/login', json={'username': 'acceptance-' + role, 'password': secret}, headers={'Origin': origin})
        assert response.status_code == 200
        return client, {'Origin': origin, 'X-CSRF-Token': response.json()['csrf']}
    viewer, viewer_headers = sign_in('viewer')
    operator, operator_headers = sign_in('operator')
    try:
        for path in ('/api/v1/status', '/api/v1/statistics', '/api/v1/completions', '/api/v1/activity'):
            assert viewer.get(path).status_code == 200
        assert viewer.get('/api/v1/settings').status_code == 403
        assert viewer.get('/api/v1/users').status_code == 403
        assert viewer.post('/api/v1/users', json={}, headers=viewer_headers).status_code == 403
        assert viewer.post('/api/v1/torrents/' + 'a'*40 + '/actions', json={'action':'pause'}, headers=viewer_headers).status_code == 403
        assert operator.get('/api/v1/settings').status_code == 403
        def socket_for(client):
            cookie = '; '.join(f'{key}={value}' for key,value in client.cookies.items())
            return connect(url, origin=origin, additional_headers={'Cookie':cookie}, open_timeout=5)
        with socket_for(viewer) as stream:
            assert json.loads(stream.recv(timeout=10))['status']['user']['role'] == 'viewer'
            assert admin.patch('/api/v1/users/' + accounts['viewer']['id'], json={'role':'operator'}, headers=headers).status_code == 200
            assert_revoked(stream)
        assert viewer.get('/api/v1/status').status_code == 401
        viewer.close()
        viewer, viewer_headers = sign_in('viewer')
        assert viewer.get('/api/v1/me').json()['user']['role'] == 'operator'
        with socket_for(viewer) as stream:
            stream.recv(timeout=10)
            assert admin.patch('/api/v1/users/' + accounts['viewer']['id'], json={'active':False}, headers=headers).status_code == 200
            assert_revoked(stream)
        assert viewer.get('/api/v1/status').status_code == 401
        reset = 'test-only-reset-account-pass'
        assert admin.post('/api/v1/users/' + accounts['operator']['id'] + '/password', json={'password':reset}, headers=headers).status_code == 200
        assert operator.get('/api/v1/status').status_code == 401
        operator.close()
        operator, operator_headers = sign_in('operator', reset)
        replacement = 'test-only-changed-account-pass'
        assert operator.post('/api/v1/account/password', json={'current_password':None,'new_password':replacement}, headers=operator_headers).status_code == 400
        assert operator.post('/api/v1/account/password', json={'current_password':reset,'new_password':replacement}, headers=operator_headers).status_code == 200
        assert operator.get('/api/v1/status').status_code == 401
        operator.close()
        operator, operator_headers = sign_in('operator', replacement)
        assert operator.get('/api/v1/statistics').status_code == 200
        assert operator.post('/api/v1/auth/logout', json={}, headers=operator_headers).status_code == 200
        assert admin.patch('/api/v1/users/' + administrator['id'], json={'active':False}, headers=headers).status_code == 400
    finally:
        viewer.close()
        operator.close()
    print('Real HTTP accounts: three roles, role/deactivation socket revocation, last-admin protection, password reset/change and login/logout passed', flush=True)


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
    account_flows(client, headers, url, origin, session['user'])
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
