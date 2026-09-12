"""Run in a disposable image: environment boundary, diagnostics and health."""
import asyncio
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

source = {'OIDC_CLIENT_SECRET': 'never-forward', 'HOME': '/root',
          'SELKIES_UNIX_SOCKET': '/run/other-user.sock',
          'SELKIES_COMMAND_ENABLED': 'true', 'SELKIES_ENABLE_SHARING': 'true',
          'LD_PRELOAD': '/tmp/inject.so', 'SELKIES_FRAMERATE': '30',
          'SELKIES_USE_BROWSER_CURSORS': 'true', 'DRINODE': '/dev/dri/renderD129',
          'AUTO_GPU': 'nvidia', 'LANG': 'C.UTF-8', 'XKB_DEFAULT_LAYOUT': 'pt'}
env = m.session_environment('/private/home', 'identity', '/private/runtime', source)
assert env['HOME'] == '/private/home'
assert env['XDG_RUNTIME_DIR'] == '/private/runtime'
assert env['AUTO_GPU'] == 'nvidia' and env['SELKIES_FRAMERATE'] == '30'
assert env['DRINODE'] == '/dev/dri/renderD129' and env['XKB_DEFAULT_LAYOUT'] == 'pt'
for key in ('OIDC_CLIENT_SECRET', 'SELKIES_UNIX_SOCKET', 'SELKIES_COMMAND_ENABLED',
            'SELKIES_ENABLE_SHARING', 'LD_PRELOAD'):
    assert key not in env, key

off = subprocess.run(['bash', '-c', 'source /usr/local/bin/session-gpu.sh; '
                      'test "$AUTO_GPU" = false && test -z "${DRINODE:-}" && test -z "${DRI_NODE:-}"'],
                     env={'PATH': '/usr/bin:/bin', 'ENABLE_GPU': 'false',
                          'DRINODE': '/missing', 'DRI_NODE': '/missing'}, capture_output=True)
assert off.returncode == 0, off.stderr
invalid = subprocess.run(['bash', '-c', 'source /usr/local/bin/session-gpu.sh'],
                         env={'PATH': '/usr/bin:/bin', 'DRINODE': '/missing'}, capture_output=True)
assert invalid.returncode != 0 and b'not an accessible' in invalid.stderr

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    runtime = base / 'runtime'
    runtime.mkdir()
    (runtime / 'browser.log').write_bytes(b'x' * 100000 + b'last error')
    root = base / 'failures'
    for _ in range(7):
        m.retain_failure_logs(runtime, root)
    assert len(list(root.iterdir())) == 5
    assert root.stat().st_mode & 0o777 == 0o700
    for archive in root.iterdir():
        log = archive / 'browser.log'
        assert log.stat().st_mode & 0o777 == 0o600
        assert log.stat().st_size == 65536 and log.read_bytes().endswith(b'last error')
    (runtime / 'browser.log').unlink()
    (runtime / 'browser.log').symlink_to('/etc/passwd')
    m.retain_failure_logs(runtime, root)
    assert len(list(root.iterdir())) == 5
    assert not any(b'root:' in p.read_bytes() for p in root.glob('*/browser.log'))

async def health():
    broker = object.__new__(m.Broker)
    broker.state, broker.sessions = 'IDLE', {}
    assert (await broker.health(None)).status == 200
    broker.sessions['private-identity'] = SimpleNamespace(
        state='RUNNING', browser=SimpleNamespace(running=lambda: False))
    response = await broker.health(None)
    assert response.status == 503
    assert json.loads(response.body)['failed_sessions'] == 1
    assert b'private-identity' not in response.body
    broker.sessions['private-identity'].browser.running = lambda: True
    assert (await broker.health(None)).status == 200

asyncio.run(health())
print('PASS: private environment boundary, bounded root-only logs, idle/degraded health')
