"""Run only inside a disposable image: verify update transactions with fake APT."""
import os
from pathlib import Path
import subprocess
import tempfile

assert Path('/etc/brave-origin-build').exists(), 'Run this test inside the image'
state = Path('/config/state')
state.mkdir(parents=True, exist_ok=True)
bin_dir = Path(tempfile.mkdtemp())
log = bin_dir / 'calls'

def stub(name, body):
    p = bin_dir / name
    p.write_text('#!/bin/bash\n' + body)
    p.chmod(0o755)

stub('apt-get', '''printf '%s\\n' "$*" >> "$TEST_LOG"
case "$*" in
  update*) [ "$SCENARIO" != offline ] ;;
  *--download-only*) [ "$SCENARIO" != download-fail ] ;;
  *--no-download*) [ "$SCENARIO" != install-fail ] ;;
  clean) exit 0 ;;
  *) echo 'Unexpected online package operation' >&2; exit 99 ;;
esac
''')
stub('apt-cache', "echo '  Candidate: 2.0.0'\n")
stub('dpkg-query', "case \"$*\" in *db:Status-Status*) echo installed ;; *) echo 1.0.0 ;; esac\n")
stub('dpkg', '''case "$1" in
  --configure) exit 0 ;;
  *) exec /usr/bin/dpkg "$@" ;;
esac
''')
stub('pgrep', 'exit 1\n')
stub('df', "printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\nroot 99999999 1 99999998 1%% /\\n'\n")
env = dict(os.environ, PATH=f'{bin_dir}:/usr/bin:/bin', TEST_LOG=str(log), MIN_UPDATE_FREE_SPACE_MB='1')

def run(scenario, expected):
    log.write_text('')
    p = subprocess.run(['/usr/local/bin/update-brave.sh'], env=dict(env, SCENARIO=scenario), capture_output=True, text=True)
    assert p.returncode == expected, (scenario, p.returncode, p.stdout, p.stderr)
    return log.read_text().splitlines()

for mode, code in [('offline', 0), ('download-fail', 1), ('install-fail', 1), ('success', 0)]:
    calls = run(mode, code)
    if mode in ('offline', 'download-fail'):
        assert not any('--no-download' in x for x in calls), calls
    if mode in ('success', 'install-fail'):
        download = next(i for i,x in enumerate(calls) if '--download-only' in x)
        install = next(i for i,x in enumerate(calls) if '--no-download' in x)
        assert download < install
        assert 'brave-origin=2.0.0' in calls[download] and 'brave-origin=2.0.0' in calls[install]
        assert all('--no-download' in x for x in calls if '-f install' in x)
    assert not Path('/tmp/brave-update-in-progress').exists()
(state / 'last-brave-version').write_text('3.0.0\n')
assert not any('install' in x for x in run('success', 2))
(state / 'last-brave-version').unlink()
(state / 'quiesce.flag').touch()
assert run('success', 0) == []
(state / 'quiesce.flag').unlink()
# A competing invocation must not clear the active updater's marker.
import fcntl
with open('/run/lock/brave-origin-update.lock', 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    marker = Path('/tmp/brave-update-in-progress')
    marker.touch()
    assert run('success', 0) == [] and marker.exists()
    marker.unlink()
print('Update tests passed: offline, download/install failures, cache-only repair, downgrade, backup hold, concurrent updater.')
