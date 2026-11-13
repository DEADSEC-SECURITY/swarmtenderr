"""Refresh release hashes for the explicitly pinned runtime set from PyPI."""
import json
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[1]
lines = ['# Complete Python 3.13+ runtime lock; hashes from the pinned PyPI release metadata.']
for requirement in (root/'requirements.versions').read_text().splitlines():
    if not requirement or requirement.startswith('#'):
        continue
    name, version = requirement.split('==')
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=30) as response:
        metadata = json.load(response)
    hashes = sorted({file['digests']['sha256'] for file in metadata['urls'] if not file.get('yanked')})
    if not hashes:
        raise ValueError('Pinned package has no accepted distribution: '+name)
    lines.append(requirement + ' \\')
    lines.extend('    --hash=sha256:'+value+(' \\' if index < len(hashes)-1 else '') for index, value in enumerate(hashes))
(root/'requirements.lock').write_text('\n'.join(lines)+'\n')
