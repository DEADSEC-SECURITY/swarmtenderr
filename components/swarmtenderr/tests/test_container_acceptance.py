"""Required acceptance against installed release bytes on both database engines."""
import http.cookiejar
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def request(base, path, payload=None, method=None, opener=None, csrf=''):
    raw = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base+path, data=raw, method=method,
        headers={'Origin': 'http://localhost:8787', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
    with (opener or urllib.request.build_opener()).open(req, timeout=5) as response:
        body = response.read()
        return json.loads(body) if response.headers.get_content_type() == 'application/json' else body


def ready(url):
    for _ in range(60):
        try:
            return request(url, '/readyz')
        except (OSError, ValueError):
            time.sleep(.5)
    raise RuntimeError('Packaged application did not become ready')


def main():
    image = sys.argv[1]
    tests = str(Path(__file__).resolve().parent)
    prefix = 'swarmtenderr-test-' + uuid.uuid4().hex[:10]
    postgres_image = 'postgres:18@sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4'
    created, volumes = [], []
    network = False
    mount = ['--mount', f'type=bind,src={tests},dst=/tests,readonly']
    def suite(network_name, database=''):
        args = ['docker', 'run', '--rm', '--network', network_name, '--read-only', '--tmpfs', '/tmp', '--entrypoint', 'python', *mount]
        if database:
            args += ['-e', 'SWARMTENDERR_TEST_DATABASE_URL='+database]
        subprocess.run([*args, image, '-B', '-m', 'unittest', 'discover', '-s', '/tests', '-v'], check=True)
    try:
        suite('none')
        docker('network', 'create', prefix)
        network = True
        postgres = prefix+'-postgres'
        docker('run', '-d', '--name', postgres, '--network', prefix, '--network-alias', 'postgres',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=swarmtenderr', '--tmpfs', '/var/lib/postgresql', postgres_image)
        created.append(postgres)
        for _ in range(60):
            if subprocess.run(['docker', 'exec', postgres, 'pg_isready', '-U', 'postgres', '-d', 'swarmtenderr'], capture_output=True).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Disposable PostgreSQL did not become ready')
        suite(prefix, 'postgresql://postgres@postgres/swarmtenderr')
        for database in ('sqlite', 'postgresql'):
            name = prefix+'-'+database
            docker('volume', 'create', name)
            volumes.append(name)
            args = ['run', '-d', '--name', name, '--network', prefix, '--read-only', '--tmpfs', '/tmp',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '-e', 'SWARMTENDERR_DEMO=true',
                '-p', '127.0.0.1::8787', '--mount', f'type=volume,src={name},dst=/data']
            if database == 'postgresql':
                args += ['-e', 'SWARMTENDERR_DATABASE_URL=postgresql://postgres@postgres/swarmtenderr']
            docker(*args, image)
            created.append(name)
            url = 'http://'+docker('port', name, '8787/tcp')
            ready(url)
            try:
                request(url, '/api/v1/status')
                raise AssertionError('Application data was available without a session')
            except urllib.error.HTTPError as error:
                assert error.code == 401
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            login = request(url, '/api/v1/auth/login', {'username': 'demo', 'password': 'demo'}, opener=opener)
            for _ in range(30):
                result = request(url, '/api/v1/status', opener=opener)
                if result['snapshot']:
                    break
                time.sleep(.5)
            assert result['database'] == database and not result['stale']
            assert result['snapshot']['demo'] and not result['snapshot']['controls']
            assert len(result['snapshot']['torrents']) == 6
            stats = request(url, '/api/v1/statistics', opener=opener)
            assert stats['total_completed'] > 0 and len(stats['daily']) == 30 and len(stats['monthly']) == 12
            settings = request(url, '/api/v1/settings', opener=opener)
            saved = request(url, '/api/v1/settings', {'revision': settings['revision'], 'changes': {'retention_days': 37}},
                method='PATCH', opener=opener, csrf=login['csrf'])
            assert saved['values']['retention_days'] == 37
            for asset in ('/', '/app.js', '/charts.js', '/echarts-6.1.0.min.js', '/style.css', '/logo.svg', '/healthz'):
                assert request(url, asset)
            subprocess.run(['docker', 'run', '--rm', '--network', prefix, '--entrypoint', 'python', *mount,
                image, '-B', '/tests/test_websocket_acceptance.py', f'ws://{name}:8787/api/v1/live'], check=True)
            docker('restart', name)
            url = 'http://'+docker('port', name, '8787/tcp')
            ready(url)
            assert request(url, '/api/v1/settings', opener=opener)['values']['retention_days'] == 37
            assert request(url, '/api/v1/statistics', opener=opener)['total_completed'] == stats['total_completed']
            print(f'Packaged {database}: login, history, settings, socket events, assets and persistent restart passed', flush=True)
        with tempfile.TemporaryDirectory() as temporary:
            credential = Path(temporary)/'credential'
            credential.write_text('test-only-dashboard-pass')
            credential.chmod(0o444)
            Path(temporary).chmod(0o755)
            name = prefix+'-production-auth'
            docker('run', '-d', '--name', name, '--network', prefix, '--tmpfs', '/tmp',
                '-e', 'SWARMTENDERR_DATA_DIR=/tmp/app', '-e', 'SWARMTENDERR_DATABASE_URL=sqlite:////tmp/app/auth.db',
                '-e', 'SWARMTENDERR_PASSWORD_FILE=/run/secrets/password',
                '--mount', f'type=bind,src={credential},dst=/run/secrets/password,readonly', image)
            created.append(name)
            for _ in range(30):
                if subprocess.run(['docker', 'exec', name, 'python', '-c', "import urllib.request;urllib.request.urlopen('http://localhost:8787/readyz').close()"], capture_output=True).returncode == 0:
                    break
                time.sleep(.5)
            else:
                raise RuntimeError('Production-mode bootstrap failed')
            subprocess.run(['docker', 'run', '--rm', '--network', prefix, '--entrypoint', 'python', *mount,
                image, '-B', '/tests/test_websocket_acceptance.py', f'ws://{name}:8787/api/v1/live', 'authenticated'], check=True)
    finally:
        for name in reversed(created):
            subprocess.run(['docker', 'rm', '-fv', name], stdout=subprocess.DEVNULL, check=False)
        for volume in volumes:
            subprocess.run(['docker', 'volume', 'rm', volume], stdout=subprocess.DEVNULL, check=False)
        if network:
            subprocess.run(['docker', 'network', 'rm', prefix], stdout=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    main()
