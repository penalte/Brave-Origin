#!/usr/bin/env python3
"""Scan a local image without giving the scanner Docker access or credentials."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import tempfile

SCANNER = 'anchore/grype@sha256:8a93fc48da96bd6ec5981279d099b69de11541dc68fdf222fb9161f8ff284af7'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image')
parser.add_argument('--report', default='security-report.json')
parser.add_argument('--database-cache', help='Optional existing Grype database cache directory')
args = parser.parse_args()
report = Path(args.report).resolve()
with tempfile.TemporaryDirectory(prefix='brave-security-') as directory:
    work = Path(directory)
    archive = work / 'image.tar'
    cache = Path(args.database_cache).resolve() if args.database_cache else work / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    (work / 'tmp').mkdir()
    subprocess.run(['docker', 'save', '-o', str(archive), args.image], check=True)
    command = [
        'docker', 'run', '--rm', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--memory', '3g', '--cpus', '2',
        '--user', f'{os.getuid()}:{os.getgid()}',
        '-v', f'{archive}:/scan/image.tar:ro', '-v', f'{cache}:/cache',
        '-v', f'{work / "tmp"}:/tmp',
        '-e', 'GRYPE_DB_CACHE_DIR=/cache', '-e', 'GRYPE_CHECK_FOR_APP_UPDATE=false',
        '-e', 'GODEBUG=http2client=0', SCANNER,
        'docker-archive:/scan/image.tar', '-o', 'json',
    ]
    with report.open('w') as output:
        subprocess.run(command, stdout=output, check=True)
result = json.loads(report.read_text())
matches = result['matches']
counts = Counter(match['vulnerability']['severity'] for match in matches)
print('Vulnerability matches:', dict(sorted(counts.items())))
print('Full report:', report)
if any(match['vulnerability']['severity'] in ('High', 'Critical') for match in matches):
    raise SystemExit('Release blocked: investigate High/Critical vulnerability findings before publishing.')
