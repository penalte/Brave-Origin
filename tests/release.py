"""Check publication channels without accessing either registry."""
import os
from pathlib import Path
import subprocess
import tempfile

script = Path(__file__).resolve().parents[1] / 'scripts/publish-images.sh'
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    log = root / 'docker.log'
    docker = root / 'docker'
    docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_LOG"\nif [ "$1" = login ]; then cat >/dev/null; fi\n')
    docker.chmod(0o755)
    git = root / 'git'
    git.write_text('#!/bin/bash\nexit "${TEST_GIT_STATUS:-0}"\n')
    git.chmod(0o755)
    registry_image = 'registry.example.test:5443/team/brave-origin'
    env = dict(os.environ, PATH=f'{root}:/usr/bin:/bin', TEST_LOG=str(log), REGISTRY_IMAGE=registry_image, REGISTRY_USERNAME='test-user', REGISTRY_TOKEN='test-only', GHCR_TOKEN='test-only')
    cases = [
        ('refs/heads/main', 0, set()),
        ('refs/heads/feature/example', 0, set()),
        ('refs/heads/beta', 0, {'beta','sha-abc123'}),
        ('refs/tags/v1.0.0-beta.1', 0, {'1.0.0-beta.1','beta','sha-abc123'}),
        ('refs/tags/v1.0.0', 0, {'1.0.0','latest','sha-abc123'}),
        ('refs/tags/vbad', 1, set()),
    ]
    for ref, code, tags in cases:
        log.write_text('')
        result = subprocess.run(['bash',str(script),'test-image',ref,'abc123'], env=env, capture_output=True, text=True)
        assert result.returncode == code, (ref,result.stderr)
        pushes = [line.removeprefix('push ') for line in log.read_text().splitlines() if line.startswith('push ')]
        expected = {f'{registry}:{tag}' for registry in ('ghcr.io/shoyrock/brave-origin',registry_image) for tag in tags}
        assert set(pushes) == expected, (ref,pushes)
        logins = [line for line in log.read_text().splitlines() if line.startswith('login ')]
        if tags:
            assert 'login registry.example.test:5443 -u test-user --password-stdin' in logins
        else:
            assert not logins, (ref,logins)
    log.write_text('')
    result = subprocess.run(['bash',str(script),'test-image','refs/tags/v1.0.0','abc123'], env=dict(env,TEST_GIT_STATUS='1'), capture_output=True)
    assert result.returncode != 0 and log.read_text() == '', 'Stable release outside main was allowed'
    for key in ('REGISTRY_IMAGE','REGISTRY_USERNAME','REGISTRY_TOKEN','GHCR_TOKEN'):
        log.write_text('')
        missing = dict(env)
        missing.pop(key)
        result = subprocess.run(['bash',str(script),'test-image','refs/heads/beta','abc123'], env=missing, capture_output=True)
        assert result.returncode != 0 and log.read_text() == '', f'Missing {key} reached Docker'
    for invalid in ('registry.example.test', 'https://registry.example.test/team/image', 'registry.example.test/team/image:latest'):
        log.write_text('')
        result = subprocess.run(['bash',str(script),'test-image','refs/heads/beta','abc123'], env=dict(env,REGISTRY_IMAGE=invalid), capture_output=True)
        assert result.returncode != 0 and log.read_text() == '', 'Invalid registry path reached Docker'
print('Release channels passed: development never publishes latest; stable must be on main; both registries are required.')
