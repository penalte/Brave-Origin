#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for file in entrypoint.sh scripts/*.sh; do bash -n "$file"; done
git --no-pager diff --check
python3 - <<'PY'
from pathlib import Path
import subprocess, xml.etree.ElementTree as ET
root = ET.parse('templates/brave-origin.xml').getroot()
assert root.tag == 'Container' and root.attrib['version'] == '2'
config = {x.attrib['Target']: x for x in root.findall('Config')}
assert len(config) == len(root.findall('Config')), 'Duplicate Unraid configuration targets'
assert config['/config'].attrib['Default'] == '/mnt/user/appdata/brave-origin-x11'
assert config['8443'].attrib['Mode'] == 'tcp'
assert config['AUTH_ENABLED'].text == 'true'
assert root.findtext('Privileged') == 'false'
assert root.findtext('Repository') == 'ghcr.io/shoyrock/brave-origin:x11'
assert '/x11/templates/' in root.findtext('TemplateURL')
assert '--stop-timeout 30' in root.findtext('ExtraParams')
assert root.findtext('WebUI') == 'https://[IP]:[PORT:8443]/'
assert root.findtext('Project') == 'https://github.com/shoyrock/Brave-Origin'
assert root.findtext('Support').endswith('/issues')
tracked = subprocess.check_output(['git','ls-files'], text=True).splitlines()
for p in tracked:
    assert p not in ('AGENTS.md','CLAUDE.md','.env') and not p.startswith(('skills/','.agents/','appdata/','scratch/')), p
assert 'FROM debian:trixie-slim\n' in Path('Dockerfile').read_text()
print('Shell syntax, whitespace, distribution rules, and Unraid template passed.')
PY
python3 -m py_compile scripts/audio-server.py scripts/prepare-storage.py tests/*.py
python3 -m py_compile scripts/prepare-storage.py scripts/security-scan.py tests/storage.py
python3 tests/release.py
node tests/client-clipboard.mjs
docker compose config --quiet
docker compose -f compose.yaml -f compose.gpu.yaml config --quiet
if command -v shellcheck >/dev/null; then shellcheck entrypoint.sh scripts/*.sh; fi
