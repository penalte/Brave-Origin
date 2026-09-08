"""Run as root only inside a disposable image; exercise hostile persisted paths."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

assert Path('/etc/brave-origin-build').exists(), 'Run inside the image'
spec = importlib.util.spec_from_file_location('storage', '/usr/local/bin/prepare-storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
storage.os.execv = lambda *args: None
root = Path('/config')
canary = Path('/tmp/root-only-canary')
canary.write_text('untouched\n')
canary.chmod(0o600)

def clear():
    try:
        os.close(7)
    except OSError:
        pass
    for path in root.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

for name in ('state/instance.lock', 'state/profile.lock', '.passwd', 'ssl/cert.key',
             'ssl/cert.pem', 'kasmvnc/kasmvnc.yaml', 'profile', '.cache'):
    clear()
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(canary)
    try:
        storage.prepare()
    except (OSError, ValueError):
        pass
    else:
        raise AssertionError(f'Startup accepted hostile path: {name}')
    assert canary.stat().st_uid == 0 and canary.read_text() == 'untouched\n', name

clear()
# Upgrade old browser-owned storage without following links or losing its locks.
(root / 'state').mkdir()
for name in ('instance.lock', 'profile.lock'):
    path = root / 'state' / name
    path.touch()
    os.chown(path, 1000, 1000)
os.chown(root / 'state', 1000, 1000)
os.chown(root, 1000, 1000)
storage.prepare()
for command in (
    ['rm', '/config/state/profile.lock'],
    ['rm', '/config/state/instance.lock'],
    ['touch', '/config/.passwd'],
    ['touch', '/config/ssl/cert.key'],
    ['touch', '/config/kasmvnc/kasmvnc.yaml'],
    ['touch', '/run/lock/attacker.lock'],
):
    result = subprocess.run(['runuser', '-u', 'braveuser', '--', *command], capture_output=True)
    assert result.returncode != 0, command
subprocess.run(['runuser', '-u', 'braveuser', '--', 'bash', '-c',
                'exec 9>/config/state/profile.lock; flock -n 9; echo RUNNING > /config/state/status'], check=True)
print('Hostile storage paths refused; credentials and locks protected; browser state remains writable.')
