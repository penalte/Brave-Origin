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
    docker.write_text('#!/bin/bash\nif [ "$1" = login ]; then cat >/dev/null; else printf "%s\\n" "$*" >> "$TEST_LOG"; fi\n')
    docker.chmod(0o755)
    git = root / 'git'
    git.write_text('#!/bin/bash\nexit "${TEST_GIT_STATUS:-0}"\n')
    git.chmod(0o755)
    env = dict(os.environ, PATH=f'{root}:/usr/bin:/bin', TEST_LOG=str(log), REGISTRY_TOKEN='test-only', GHCR_TOKEN='test-only')
    cases = [
        ('refs/heads/main', 0, set()),
        ('refs/heads/feature/example', 0, set()),
        ('refs/heads/beta', 0, set()),
        ('refs/heads/x11', 0, set()),
        ('refs/heads/x11-beta', 0, {'x11-beta','x11-sha-abc123'}),
        ('refs/tags/x11-v1.0.0-beta.1', 0, {'1.0.0-beta.1-x11','x11-beta','x11-sha-abc123'}),
        ('refs/tags/v1.0.0', 0, set()),
        ('refs/tags/x11-v1.0.0', 0, {'1.0.0-x11','x11','x11-sha-abc123'}),
        ('refs/tags/x11-vbad', 1, set()),
    ]
    for ref, code, tags in cases:
        log.write_text('')
        result = subprocess.run(['bash',str(script),'test-image',ref,'abc123'], env=env, capture_output=True, text=True)
        assert result.returncode == code, (ref,result.stderr)
        pushes = [line.removeprefix('push ') for line in log.read_text().splitlines() if line.startswith('push ')]
        expected = {f'{registry}:{tag}' for registry in ('ghcr.io/shoyrock/brave-origin','forgejo.foss.homes/shoy/brave-origin') for tag in tags}
        assert set(pushes) == expected, (ref,pushes)
    log.write_text('')
    result = subprocess.run(['bash',str(script),'test-image','refs/tags/x11-v1.0.0','abc123'], env=dict(env,TEST_GIT_STATUS='1'), capture_output=True)
    assert result.returncode != 0 and log.read_text() == '', 'Stable release outside x11 was allowed'
print('Release channels passed: development never publishes latest; stable must be on x11; both registries are required.')
