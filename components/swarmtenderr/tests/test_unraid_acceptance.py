"""Run the Unraid template's Docker contract, not the Unraid WebGUI, in isolation."""
import http.cookiejar
import json
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]


def docker(*args, input=None):
    return subprocess.check_output(['docker', *args], input=input, text=True).strip()


def main():
    template = ET.parse(ROOT/'templates/swarmtenderr.xml').getroot()
    profile = ET.parse(ROOT/'ca_profile.xml').getroot()
    assert profile.findtext('Profile', '').strip()
    assert template.attrib == {'version': '2'}
    assert template.findtext('Privileged') == 'false'
    assert template.findtext('Network') == 'bridge'
    configs = {field.attrib['Target']: field.attrib for field in template.findall('Config')}
    assert configs['/data']['Mode'] == 'rw'
    assert configs['/run/secrets/swarmtenderr']['Mode'] == 'ro'
    assert set(key for key, field in configs.items() if field['Type'] == 'Path') == {'/data', '/run/secrets/swarmtenderr'}
    assert configs['SWARMTENDERR_PUBLIC_URL']['Required'] == 'true'
    extra = shlex.split(template.findtext('ExtraParams', ''))
    assert '--read-only' in extra and '--cap-drop=ALL' in extra
    assert not any('privileged' in value or 'docker.sock' in value for value in extra)
    image = sys.argv[1] if len(sys.argv) > 1 else template.findtext('Repository')
    prefix = 'swarmtenderr-unraid-test-'+uuid.uuid4().hex[:10]
    volumes, containers = [], []
    password = secrets.token_urlsafe(24)
    network = False

    def request(base, path, opener, payload=None, method=None, csrf='', origin=None):
        req = urllib.request.Request(base+path, data=json.dumps(payload).encode() if payload is not None else None,
            method=method, headers={'Origin': origin or base, 'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
        with opener.open(req, timeout=5) as response:
            return json.load(response)

    try:
        docker('network', 'create', '--label', 'swarmtenderr.test.owner='+prefix, prefix)
        network = True
        postgres = prefix+'-postgres'
        docker('run', '-d', '--name', postgres, '--label', 'swarmtenderr.test.owner='+prefix,
            '--network', prefix, '--network-alias', 'postgres', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            '--tmpfs', '/var/lib/postgresql', 'postgres:18@sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4')
        containers.append(postgres)
        for _ in range(60):
            if subprocess.run(['docker', 'exec', postgres, 'pg_isready', '-U', 'postgres'], capture_output=True).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Isolated PostgreSQL did not become ready')

        for backend in ['sqlite', 'postgresql']:
            data, private, name = (prefix+'-'+backend+suffix for suffix in ['-data', '-private', '-app'])
            for volume in [data, private]:
                docker('volume', 'create', '--label', 'swarmtenderr.test.owner='+prefix, volume)
                volumes.append(volume)
            # Private values enter the helper over stdin, never arguments or logs.
            prepare = """import json,os,pathlib,sys
values=json.load(sys.stdin)
for path in ['/data','/private']:
 os.chown(path,10001,10001);os.chmod(path,0o700)
for name,value in values.items():
 p=pathlib.Path('/private')/name;p.write_text(value);os.chown(p,10001,10001);os.chmod(p,0o600)
"""
            private_values = {'admin-password': password}
            if backend == 'postgresql':
                private_values['postgres-url'] = 'postgresql://postgres@postgres/postgres'
            docker('run', '--rm', '-i', '--user', '0:0', '--network', 'none', '--entrypoint', 'python',
                '-v', data+':/data', '-v', private+':/private', image, '-c', prepare, input=json.dumps(private_values))
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            base = 'http://127.0.0.1:'+str(port)
            args = ['run', '-d', '--name', name, '--label', 'swarmtenderr.test.owner='+prefix, '--network', prefix, *extra]
            for field in template.findall('Config'):
                item = field.attrib
                if item['Type'] == 'Port':
                    args += ['-p', '127.0.0.1:'+str(port)+':'+item['Target']+'/'+item['Mode']]
                elif item['Type'] == 'Path':
                    args += ['--mount', 'type=volume,source='+(data if item['Target'] == '/data' else private)+',target='+item['Target']+(',readonly' if item['Mode'] == 'ro' else '')]
                elif item['Type'] == 'Variable':
                    value = item.get('Default', '')
                    if item['Target'] == 'SWARMTENDERR_PUBLIC_URL':
                        value = base
                    if item['Target'] == 'SWARMTENDERR_DATABASE_URL_FILE' and backend == 'postgresql':
                        value = '/run/secrets/swarmtenderr/postgres-url'
                    if value:
                        args += ['-e', item['Target']+'='+value]
            args.append(image)
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

            for recreated in [False, True]:
                docker(*args)
                containers.append(name)
                for _ in range(60):
                    try:
                        request(base, '/readyz', opener)
                        break
                    except OSError:
                        time.sleep(.5)
                else:
                    diagnostic = subprocess.run(['docker', 'logs', '--tail', '25', name], capture_output=True, text=True)
                    log = (diagnostic.stdout+diagnostic.stderr).replace(password, '[redacted]')
                    raise RuntimeError('Template container did not become ready: '+log)
                info = json.loads(docker('inspect', name))[0]
                assert info['Config']['User'] == '10001:10001'
                assert info['HostConfig']['ReadonlyRootfs'] and not info['HostConfig']['Privileged']
                mounts = {mount['Destination']: mount for mount in info['Mounts']}
                assert not mounts['/run/secrets/swarmtenderr']['RW'] and mounts['/data']['RW']
                assert not any('docker.sock' in mount['Source'] for mount in info['Mounts'])
                login = request(base, '/api/v1/auth/login', opener, {'username': 'admin', 'password': password})
                assert login['user']['role'] == 'admin'
                settings = request(base, '/api/v1/settings', opener)
                assert password not in json.dumps(settings)
                assert settings['deployment']['database'] == backend
                if recreated:
                    assert settings['values']['poll_seconds'] == 5
                else:
                    try:
                        request(base, '/api/v1/settings', opener, {'revision': settings['revision'], 'changes': {'poll_seconds': 5}}, 'PATCH', login['csrf'], origin='http://wrong-origin.invalid')
                    except urllib.error.HTTPError as error:
                        assert error.code == 403
                    else:
                        raise AssertionError('Cross-origin settings mutation was accepted')
                    saved = request(base, '/api/v1/settings', opener, {'revision': settings['revision'], 'changes': {'poll_seconds': 5}}, 'PATCH', login['csrf'])
                    assert saved['values']['poll_seconds'] == 5
                    docker('exec', name, 'swarmtenderr-maintenance', 'backup', '--file', '/data/template-backup.json')
                    docker('exec', name, 'python', '-c', "import pathlib; p=pathlib.Path('/run/secrets/swarmtenderr/admin-password'); assert p.stat().st_mode & 0o777 == 0o600; assert pathlib.Path('/data/template-backup.json').is_file()")
                docker('rm', '-f', name)
                containers.remove(name)
            print('Unraid Docker contract '+backend+': private-file bootstrap, origin checks, settings, backup and recreation passed')
    finally:
        for name in reversed(containers):
            docker('rm', '-f', name)
        for volume in reversed(volumes):
            docker('volume', 'rm', volume)
        if network:
            docker('network', 'rm', prefix)


if __name__ == '__main__':
    main()
