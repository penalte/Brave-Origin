#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for file in entrypoint.sh scripts/*.sh; do bash -n "$file"; done
git --no-pager diff --check
python3 - <<'PY'
from pathlib import Path
import subprocess, xml.etree.ElementTree as ET
profile = ET.parse('ca_profile.xml').getroot()
assert profile.tag == 'CommunityApplications', 'Invalid Community Applications profile root'
assert (profile.findtext('Profile') or '').strip(), 'Community Applications requires a non-empty Profile'
root = ET.parse('templates/brave-origin.xml').getroot()
assert root.tag == 'Container' and root.attrib['version'] == '2'
config = {x.attrib['Target']: x for x in root.findall('Config')}
assert len(config) == len(root.findall('Config')), 'Duplicate Unraid configuration targets'
assert config['/config'].attrib['Default'] == '/mnt/user/appdata/brave-origin'
assert config['8443'].attrib['Mode'] == 'tcp'
assert config['AUTH_ENABLED'].text == 'true'
assert root.findtext('Privileged') == 'false'
assert root.findtext('WebUI') == 'https://[IP]:[PORT:8443]/'
assert root.findtext('Project') == 'https://github.com/shoyrock/Brave-Origin'
assert root.findtext('Support').endswith('/issues')
assert root.findtext('Repository') == 'ghcr.io/shoyrock/brave-origin:latest'
branches = root.findall('Branch')
# CA adds the default automatically and replaces whole Config lists per branch.
assert [b.findtext('Tag') for b in branches] == ['beta', 'x11', 'x11-beta']
names, paths, ports = {root.findtext('Name')}, {config['/config'].text}, {config['8443'].text}
for branch in branches:
    settings = {c.attrib['Target']: c for c in branch.findall('Config')}
    assert len(settings) == len(branch.findall('Config'))
    assert settings.keys() == (config.keys() if branch.findtext('Tag') == 'beta' else config.keys() - {'DISPLAY_AUTO_RESIZE', 'BROWSER_LOCK_MAXIMIZED'})
    assert settings['AUTH_ENABLED'].text == 'true'
    assert settings['AUTH_PASSWORD'].attrib['Mask'] == 'true'
    for seen, value in ((names, branch.findtext('Name')), (paths, settings['/config'].text), (ports, settings['8443'].text)):
        assert value and value not in seen, 'Unraid channels must use separate names, storage, and ports'
        seen.add(value)
    assert settings['/config'].text == settings['/config'].attrib['Default']
    assert settings['8443'].text == settings['8443'].attrib['Default']
tracked = subprocess.check_output(['git','ls-files'], text=True).splitlines()
for p in tracked:
    assert p not in ('AGENTS.md','CLAUDE.md','.env') and not p.startswith(('skills/','.agents/','appdata/','scratch/')), p
assert 'FROM debian:trixie-slim\n' in Path('Dockerfile').read_text()
print('Shell syntax, whitespace, distribution rules, Unraid template, and Community Applications profile passed.')
PY
python3 -m py_compile scripts/prepare-storage.py scripts/security-scan.py tests/storage.py
python3 -m py_compile scripts/session-manager.py tests/oidc-session.py tests/oidc-tokens.py
python3 -m py_compile scripts/multi-session.py tests/multi-session.py tests/oidc-recovery.py tests/browser-gpu.py
python3 -m py_compile scripts/file-picker.py tests/file-picker.py tests/picker_browser.py
python3 -m py_compile tests/cursor_browser.py
python3 -m py_compile tests/session-operations.py
python3 tests/release.py
docker compose config --quiet
docker compose -f compose.yaml -f compose.gpu.yaml config --quiet
docker compose -f compose.yaml -f compose.nvidia.yaml config --quiet
if command -v shellcheck >/dev/null; then shellcheck entrypoint.sh scripts/*.sh; fi
