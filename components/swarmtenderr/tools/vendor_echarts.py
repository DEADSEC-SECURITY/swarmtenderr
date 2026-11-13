"""Reproducibly vendor Apache ECharts; offline verification is the default."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '6.1.0'
FILES = {'package/dist/echarts.min.js': f'echarts-{VERSION}.min.js',
         'package/LICENSE': 'echarts.LICENSE.txt', 'package/NOTICE': 'echarts.NOTICE.txt'}


def download():
    with urllib.request.urlopen(f'https://registry.npmjs.org/echarts/{VERSION}', timeout=30) as response:
        metadata = json.load(response)
    dist = metadata['dist']
    if not dist['tarball'].startswith('https://registry.npmjs.org/echarts/-/'):
        raise ValueError('Unexpected package source')
    with urllib.request.urlopen(dist['tarball'], timeout=60) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
    integrity = 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode()
    if len(raw) > 32 * 1024 * 1024 or integrity != dist['integrity']:
        raise ValueError('Package integrity verification failed')
    hashes = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for source, target in FILES.items():
            item = archive.getmember(source)
            if not item.isfile() or item.size > 2 * 1024 * 1024:
                raise ValueError('Unexpected package member')
            data = archive.extractfile(item).read()
            (ROOT / 'src/swarmtenderr/static' / target).write_bytes(data)
            hashes[target] = hashlib.sha256(data).hexdigest()
    (ROOT/'frontend.lock.json').write_text(json.dumps({'name':'echarts', 'version':VERSION,
        'license':'Apache-2.0', 'source':'https://echarts.apache.org/',
        'tarball':dist['tarball'], 'integrity':integrity, 'files':hashes}, indent=2)+'\n')


def verify():
    lock = json.loads((ROOT/'frontend.lock.json').read_text())
    if lock['version'] != VERSION or set(lock['files']) != set(FILES.values()):
        raise ValueError('Unexpected frontend dependency lock')
    for name, expected in lock['files'].items():
        if hashlib.sha256((ROOT/'src/swarmtenderr/static'/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Frontend integrity mismatch: ' + name)
    print('Apache ECharts ' + VERSION + ': all vendored assets verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    if parser.parse_args().download:
        download()
    verify()
