"""Actual code/token/JWKS exchanges with an isolated RSA signing issuer."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.testclient import TestClient

from swarmtenderr.live import create_app
from swarmtenderr.settings import Settings
from database_fixture import database_url


class OidcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = RSAKey.generate_key(2048, {'kid': 'fixture'})
        cls.other_key = RSAKey.generate_key(2048, {'kid': 'fixture'})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.variant, self.codes, self.exchanges = '', {}, []
        self.groups, self.extra_claims, self.omit_groups = ['media'], {}, False
        self.during_exchange = None
        owner = self

        class Issuer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, content, status=200):
                body = json.dumps(content).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/.well-known/openid-configuration':
                    return self.reply({'issuer': owner.issuer, 'authorization_endpoint': owner.issuer + '/authorize',
                        'token_endpoint': owner.issuer + '/token', 'jwks_uri': owner.issuer + '/jwks',
                        'response_types_supported': ['code'], 'subject_types_supported': ['public'],
                        'id_token_signing_alg_values_supported': ['RS256'], 'code_challenge_methods_supported': ['S256']})
                if self.path == '/jwks':
                    return self.reply({'keys': [owner.key.as_dict()]})
                return self.reply({'error': 'not_found'}, 404)

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                owner.exchanges.append(form)
                code = form.get('code', [''])[0]
                authorization = owner.codes.pop(code, None)
                verifier = form.get('code_verifier', [''])[0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                if not authorization or not verifier or challenge != authorization['code_challenge'][0]:
                    return self.reply({'error': 'invalid_grant'}, 400)
                now = int(time.time())
                claims = {'iss': owner.issuer, 'sub': 'stable-subject', 'aud': 'swarm-fixture', 'exp': now + 300,
                          'iat': now, 'nonce': authorization['nonce'][0], 'preferred_username': 'sso-fixture',
                          'email': 'admin@example.test', 'groups': owner.groups, **owner.extra_claims}
                claims.update({'issuer': {'iss': owner.issuer + '/wrong'}, 'audience': {'aud': 'other-client'},
                               'expired': {'exp': now - 3600}, 'nonce': {'nonce': 'wrong-nonce'},
                               'groups': {'groups': ['denied']}}.get(owner.variant, {}))
                if owner.variant == 'no_nonce':
                    del claims['nonce']
                if owner.omit_groups:
                    del claims['groups']
                key = owner.other_key if owner.variant == 'signature' else owner.key
                if owner.during_exchange:
                    action, owner.during_exchange = owner.during_exchange, None
                    action()
                token = jwt.encode({'alg': 'RS256', 'kid': 'fixture'}, claims, key)
                self.reply({'access_token': 'isolated-access-token', 'token_type': 'Bearer', 'expires_in': 300, 'id_token': token})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Issuer)
        self.issuer = 'http://127.0.0.1:' + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        path = Path(self.temp.name)
        self.app = create_app(Settings(database=database_url(self, path / 'state.db'), data_dir=str(path),
            app_password='test-only-administrator-password', allow_http_oidc=True), start_observer=False)
        self.service = self.app.state.service
        self.service.runtime.save({'sso_enabled': True, 'sso_issuer': self.issuer,
            'sso_client_id': 'swarm-fixture', 'sso_client_secret': 'fixture-client-secret',
            'sso_allowed_groups': ['media']}, 0, 'fixture')
        self.client = TestClient(self.app, base_url='http://localhost:8787', follow_redirects=False)
        self.addCleanup(self.client.close)

    def begin(self):
        response = self.client.get('/auth/oidc/login')
        self.assertEqual(response.status_code, 302, response.text)
        target = urlsplit(response.headers['location'])
        self.assertEqual(target.path, '/authorize')
        query = parse_qs(target.query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertTrue(query['nonce'][0])
        code = secrets.token_urlsafe(20)
        self.codes[code] = query
        return '/auth/oidc/callback?' + urlencode({'code': code, 'state': query['state'][0]})

    def test_signed_flow_pkce_provisions_viewer_and_rejects_replay(self):
        callback = self.begin()
        response = self.client.get(callback)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], 'http://localhost:8787/')
        me = self.client.get('/api/v1/me').json()['user']
        self.assertEqual(me['role'], 'viewer')
        self.assertEqual(me['provider'], 'sso')
        self.assertTrue(self.exchanges[0]['code_verifier'][0])
        self.assertEqual(self.client.get(callback).headers['location'], '/?auth_error=sso_denied')
        self.assertEqual(len(self.exchanges), 1)

    def test_wrong_issuer_audience_signature_expiry_nonce_and_groups_are_denied(self):
        for variant in ('issuer', 'audience', 'signature', 'expired', 'nonce', 'no_nonce', 'groups'):
            with self.subTest(variant=variant):
                self.variant = variant
                callback = self.begin()
                response = self.client.get(callback)
                self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')
                self.assertEqual(self.client.get('/api/v1/me').status_code, 401)
        self.assertEqual(len(self.service.auth.users()), 1)

    def test_state_tampering_and_user_cancellation_fail_without_token_exchange(self):
        callback = self.begin()
        query = parse_qs(urlsplit(callback).query)
        response = self.client.get('/auth/oidc/callback?' + urlencode({'code': query['code'][0], 'state': 'tampered'}))
        self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')
        self.assertEqual(self.exchanges, [])
        response = self.client.get('/auth/oidc/callback?error=access_denied')
        self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')
        self.assertEqual(self.exchanges, [])

    def test_discovery_test_requires_admin_origin_csrf(self):
        self.assertEqual(self.client.post('/api/v1/settings/test-sso', json={}).status_code, 401)
        session = self.service.auth.login('admin', 'test-only-administrator-password', 'fixture')
        self.client.cookies.set('swarmtenderr_session', session)
        self.assertEqual(self.client.post('/api/v1/settings/test-sso', json={}).status_code, 403)
        response = self.client.post('/api/v1/settings/test-sso', json={},
            headers={'Origin': 'http://localhost:8787', 'X-CSRF-Token': self.service.auth.csrf(session)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['ok'])

    def configure_roles(self, **changes):
        values = {'sso_admin_groups': ['administrators'], 'sso_operator_groups': ['operators'], **changes}
        self.service.runtime.save(values, self.service.runtime.public()['revision'], 'fixture')

    def sign_in(self):
        response = self.client.get(self.begin())
        self.assertEqual(response.status_code, 303, response.text)
        return self.client.get('/api/v1/me').json()['user']

    def test_verified_groups_provision_admin_with_precedence_and_admin_api_access(self):
        self.configure_roles()
        self.groups = ['media', 'operators', 'administrators']
        user = self.sign_in()
        self.assertEqual(user['role'], 'admin')
        self.assertEqual(self.client.get('/api/v1/users').status_code, 200)
        local = next(u for u in self.service.auth.users() if u['provider'] == 'local')
        self.assertEqual(local['username'], 'admin')
        self.assertNotEqual(user['id'], local['id'])

    def test_existing_viewer_is_promoted_then_demoted_and_old_admin_sessions_revoked(self):
        viewer = self.sign_in()
        self.configure_roles()
        self.groups = ['media', 'administrators']
        admin = self.sign_in()
        self.assertEqual((admin['id'], admin['role']), (viewer['id'], 'admin'))
        first_admin = self.client.cookies.get('swarmtenderr_session')
        self.sign_in()
        second_admin = self.client.cookies.get('swarmtenderr_session')
        self.groups = ['media', 'operators']
        operator = self.sign_in()
        self.assertEqual((operator['id'], operator['role']), (viewer['id'], 'operator'))
        self.assertIsNone(self.service.auth.current(first_admin))
        self.assertIsNone(self.service.auth.current(second_admin))
        self.assertEqual(self.client.get('/api/v1/users').status_code, 403)
        old_operator = self.client.cookies.get('swarmtenderr_session')
        self.groups = ['media']
        self.assertEqual(self.sign_in()['role'], 'viewer')
        self.assertIsNone(self.service.auth.current(old_operator))

    def test_removed_allowed_membership_denies_and_commits_old_session_revocation(self):
        self.configure_roles()
        self.groups = ['media', 'administrators']
        user = self.sign_in()
        token = self.client.cookies.get('swarmtenderr_session')
        self.groups = ['administrators']
        response = self.client.get(self.begin())
        self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')
        self.assertIsNone(self.service.auth.current(token))
        self.assertEqual(next(u for u in self.service.auth.users() if u['id'] == user['id'])['role'], 'viewer')

    def test_malformed_groups_cannot_keep_admin_or_reuse_an_old_session(self):
        self.configure_roles()
        for groups in ('administrators', {'name': 'administrators'}, [['administrators']], None, [1]):
            with self.subTest(groups=groups):
                self.groups = ['media', 'administrators']
                self.assertEqual(self.sign_in()['role'], 'admin')
                token = self.client.cookies.get('swarmtenderr_session')
                self.groups = groups
                response = self.client.get(self.begin())
                self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')
                self.assertIsNone(self.service.auth.current(token))

    def test_clearing_role_lists_demotes_previously_mapped_accounts_only(self):
        self.configure_roles()
        self.groups = ['media', 'administrators']
        user = self.sign_in()
        self.configure_roles(sso_admin_groups=[], sso_operator_groups=[])
        self.assertEqual(self.sign_in()['role'], 'viewer')
        self.assertNotIn(user['id'], self.service.store.meta('sso_group_managed_users_v1'))
        self.service.auth.update_user(user['id'], {'role': 'admin'}, 'fixture')
        self.assertEqual(self.sign_in()['role'], 'admin')

    def test_claimed_role_and_case_mismatch_do_not_grant_admin(self):
        self.configure_roles()
        self.extra_claims = {'role': 'admin', 'is_superuser': True}
        self.groups = ['media', 'Administrators']
        self.assertEqual(self.sign_in()['role'], 'viewer')

    def test_missing_groups_demotes_when_allowlist_is_unrestricted(self):
        self.configure_roles(sso_allowed_groups=[])
        self.groups = ['administrators']
        self.assertEqual(self.sign_in()['role'], 'admin')
        token = self.client.cookies.get('swarmtenderr_session')
        self.omit_groups = True
        self.assertEqual(self.sign_in()['role'], 'viewer')
        self.assertIsNone(self.service.auth.current(token))

    def test_configured_operator_fallback_and_disabled_user_remain_enforced(self):
        self.configure_roles(sso_default_role='operator')
        self.assertEqual(self.sign_in()['role'], 'operator')
        self.groups = ['media', 'administrators']
        user = self.sign_in()
        self.service.auth.update_user(user['id'], {'active': False}, 'fixture')
        response = self.client.get(self.begin())
        self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')

    def test_policy_change_during_code_exchange_cannot_issue_stale_admin_session(self):
        self.configure_roles()
        self.groups = ['media', 'administrators']
        self.during_exchange = lambda: self.configure_roles(sso_admin_groups=['other-administrators'])
        response = self.client.get(self.begin())
        self.assertEqual(response.headers['location'], '/?auth_error=sso_denied')
        self.assertEqual(len(self.service.auth.users()), 1)
        self.assertEqual(self.sign_in()['role'], 'viewer')
