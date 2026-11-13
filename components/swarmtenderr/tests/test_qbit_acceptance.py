"""Installed app against two real clients on a network without Internet access.

Transfers only generated fixture bytes. No production client, torrent, network,
volume, credential or preference is used. Every resource has a unique test owner.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

QBIT_IMAGE = 'qbittorrentofficial/qbittorrent-nox@sha256:43b2d352cf76330a7ca34c7320b9b74227d6f31e662e0e123d0457206b09a7ea'
PASSWORD = 'test-only-swarm-client'


def bencode(value):
    if isinstance(value, int):
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, dict):
        return b'd' + b''.join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b'e'
    raise TypeError(type(value))


def fixture(index):
    payload = (f'Swarmtenderr generated acceptance fixture {index}\n'.encode() * 50000)[:2*1024*1024]
    info = {'name': f'fixture-{index}.bin', 'length': len(payload), 'piece length': 16384,
            'pieces': b''.join(hashlib.sha1(payload[i:i+16384]).digest() for i in range(0, len(payload), 16384)), 'private': 1}
    return payload, bencode({'announce': 'http://127.0.0.1:1/announce', 'info': info}), hashlib.sha1(bencode(info)).hexdigest()


def eventually(check, description, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.25)
    raise AssertionError('Timed out: ' + description)


def worker():
    import httpx
    from swarmtenderr.app import create_app
    from swarmtenderr.qbit import Qbit
    from swarmtenderr.settings import Settings

    clients = []
    for host in ('seed', 'client'):
        client = Qbit('http://' + socket.gethostbyname(host) + ':8080', 'admin', password=PASSWORD)
        deadline = time.monotonic() + 45
        for attempt in range(80):
            try:
                client.preferences()
                break
            except Exception:
                if attempt == 79 or time.monotonic() >= deadline:
                    raise
                time.sleep(.25)
        client.request('app/setPreferences', {'json': json.dumps({'queueing_enabled': True,
            'dont_count_slow_torrents': False, 'max_active_downloads': 5, 'max_active_uploads': 7,
            'max_active_torrents': -1, 'dht': False, 'pex': False, 'lsd': False, 'upnp': False})})
        clients.append(client)
    print('Real-client authentication and preference setup passed', flush=True)
    seed, native = clients

    def add(client, index, **fields):
        _, torrent, identity = fixture(index)
        cookies = {cookie.name: cookie.value for cookie in client.cookies}
        with httpx.Client(trust_env=False, timeout=10, cookies=cookies) as http:
            response = http.post(client.url + '/api/v2/torrents/add',
                files={'torrents': ('fixture.torrent', torrent, 'application/x-bittorrent')},
                data={'savepath': '/downloads', 'category': 'acceptance', **fields},
                headers={'Origin': client.url, 'Referer': client.url + '/'})
            assert response.status_code == 200, response.status_code
        eventually(lambda: any(row['hash'] == identity for row in client.torrents()), 'native torrent add')
        return identity

    identity = add(seed, 1)
    eventually(lambda: seed.torrents()[0]['progress'] == 1, 'seed fixture hash check')
    identity = add(native, 1)
    stopped = add(native, 2, stopped='true')
    tagged = add(native, 3, tags='swarmtenderr-keep')
    forced = add(native, 4)
    native.request('torrents/setForceStart', {'hashes': forced, 'value': 'true'})
    protected = {stopped, tagged, forced}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        password_file = path/'qbit-password'
        password_file.write_text(PASSWORD)
        app = create_app(Settings(database='sqlite:///' + str(path/'state.db'), data_dir=directory,
            qbit_url=native.url, qbit_password_file=str(password_file), app_password='test-only-app-password',
            controls=True, defaults={'managed_categories': ['acceptance'], 'download_slots': 1,
                'minimum_free_gib': 1, 'work_budget_gib': 1}), start_observer=False)
        app.observer.poll()
        assert app.analytics.summary('UTC')['total_completed'] == 0
        user = app.auth.users()[0]
        for action, state in [('pause', 'stoppedDL'), ('resume', None), ('prioritize', None), ('reannounce', None)]:
            status, result = app.action(identity, {'action': action}, user)
            assert status == 202, result
            if state:
                eventually(lambda: next(r for r in native.torrents() if r['hash'] == identity)['state'] == state, 'native stop')
            app.observer.poll()
        for key in (tagged, forced):
            assert app.action(key, {'action': 'pause'}, user)[0] == 409
        print('Four native controls and protected-transfer rejection passed', flush=True)
        before = native.preferences()
        config = app.runtime.public()
        app.runtime.save({'policy_mode': 'enforce'}, config['revision'], 'acceptance')
        app.observer.poll()
        eventually(lambda: native.preferences()['max_active_downloads'] == 1, 'enforced native ceiling')
        app.observer.poll()
        assert next(row for row in native.torrents() if row['hash'] == stopped)['state'] == 'stoppedDL'
        audit = app.store.history(admin=True)['audit']
        assert not any(row['detail'].get('torrent') in protected and row['actor'] == 'optimizer' for row in audit)
        after = native.preferences()
        assert after['max_active_uploads'] == before['max_active_uploads']
        assert after['max_active_torrents'] == before['max_active_torrents']
        config = app.runtime.public()
        app.runtime.save({'policy_mode': 'observe'}, config['revision'], 'acceptance')
        app.observer.poll()
        app.observer.poll()
        assert native.preferences()['max_active_downloads'] == 5
        print('Enforce/observe restoration and upload-policy preservation passed', flush=True)
        native.request('torrents/addPeers', {'hashes': identity, 'peers': socket.gethostbyname('seed') + ':' + str(seed.preferences()['listen_port'])})
        def complete():
            app.observer.poll()
            return app.analytics.summary('UTC')['total_completed'] == 1
        try:
            eventually(complete, 'actual peer transfer and completion accounting', seconds=60)
        except AssertionError:
            print('Fixture transfer diagnostics:', [{k:r.get(k) for k in ('name','state','progress','downloaded','completion_on','added_on','num_seeds','priority')} for r in native.torrents()], app.analytics.summary('UTC')['total_completed'], flush=True)
            with app.store.connection() as connection:
                print('Fixture sessions:', app.store.execute(connection, 'SELECT downloaded,progress,moved,seen FROM swarmtenderr_download_sessions').fetchall(), flush=True)
                print('Fixture completions:', app.store.execute(connection, 'SELECT name,completed FROM swarmtenderr_completions').fetchall(), flush=True)
            raise
        app.observer.poll()
        assert app.analytics.summary('UTC')['total_completed'] == 1
        assert app.action(identity, {'action': 'pause'}, user)[0] == 409
        print('Real qBittorrent: cookie auth, four native controls, protected transfers, enforce/observe restoration, actual peer transfer and deduplicated completion passed', flush=True)


def main(image):
    owner = 'swarmtenderr-qbit-' + uuid.uuid4().hex[:10]
    created = []
    def docker(*args):
        return subprocess.check_output(['docker', *args], text=True).strip()
    docker('network', 'create', '--internal', owner)
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            for alias in ('seed', 'client'):
                config = root/alias/'config/qBittorrent/config'
                config.mkdir(parents=True)
                downloads = root/alias/'downloads'
                downloads.mkdir()
                downloads.chmod(0o777)
                if alias == 'seed':
                    (downloads/'fixture-1.bin').write_bytes(fixture(1)[0])
                salt = b'acceptance-fixtur'
                digest = hashlib.pbkdf2_hmac('sha512', PASSWORD.encode(), salt, 100000, 64)
                encoded = base64.b64encode(salt).decode() + ':' + base64.b64encode(digest).decode()
                (config/'qBittorrent.conf').write_text('[Preferences]\nWebUI\\Password_PBKDF2="@ByteArray(' + encoded + ')"\n[WebUI]\nPassword_PBKDF2="@ByteArray(' + encoded + ')"\n[BitTorrent]\nSession\\DefaultSavePath=/downloads\nSession\\DHTEnabled=false\nSession\\LSDEnabled=false\nSession\\PeXEnabled=false\n[Meta]\nMigrationVersion=9999\n')
                name = owner + '-' + alias
                docker('run', '-d', '--name', name, '--network', owner, '--network-alias', alias,
                    '--read-only', '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp', '-e', 'QBT_LEGAL_NOTICE=confirm',
                    '--mount', f'type=bind,src={root/alias/"config"},dst=/config',
                    '--mount', f'type=bind,src={downloads},dst=/downloads', QBIT_IMAGE)
                created.append(name)
            worker_name = owner + '-worker'
            created.append(worker_name)
            subprocess.run(['docker', 'run', '--rm', '--name', worker_name, '--network', owner, '--read-only', '--tmpfs', '/tmp',
                '--entrypoint', 'python', '--mount', f'type=bind,src={Path(__file__).resolve().parent},dst=/tests,readonly',
                image, '-B', '/tests/test_qbit_acceptance.py', '--worker'], check=True, timeout=180)
    finally:
        for name in reversed(created):
            subprocess.run(['docker', 'rm', '-fv', name], stdout=subprocess.DEVNULL, check=False)
        subprocess.run(['docker', 'network', 'rm', owner], stdout=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    worker() if sys.argv[1] == '--worker' else main(sys.argv[1])
