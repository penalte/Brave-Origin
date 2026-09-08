#!/usr/bin/env python3
"""Audit locked web dependencies without installing packages or running scripts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
failed = False
for lock in sorted((root / 'dependencies').glob('*.package-lock.json')):
    with tempfile.TemporaryDirectory(prefix='brave-web-audit-') as name:
        work = Path(name)
        data = json.loads(lock.read_text())
        (work / 'package-lock.json').write_text(lock.read_text())
        (work / 'package.json').write_text(json.dumps(data['packages']['']))
        for config in ('user.npmrc', 'global.npmrc'):
            (work / config).write_text('')
        env = {
            'PATH': os.environ['PATH'], 'HOME': name,
            'NPM_CONFIG_USERCONFIG': str(work / 'user.npmrc'),
            'NPM_CONFIG_GLOBALCONFIG': str(work / 'global.npmrc'),
            'NPM_CONFIG_REGISTRY': 'https://registry.npmjs.org',
            'NPM_CONFIG_CACHE': str(work / 'cache'),
        }
        result = subprocess.run(['npm', 'audit', '--package-lock-only', '--ignore-scripts',
                                 '--audit-level=high', '--json'], cwd=work, env=env,
                                capture_output=True, text=True)
        if result.stdout:
            report = json.loads(result.stdout)
            print(lock.name, report.get('metadata', {}).get('vulnerabilities', report.get('error')))
        if result.returncode:
            failed = True
            print(result.stderr)
if failed:
    raise SystemExit('Web dependency audit failed; investigate before publishing.')
