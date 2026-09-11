"""Run ONLY in a disposable container without GPU devices; replaces test binaries."""
import os
import json
from pathlib import Path
import subprocess
import tempfile

assert not Path('/dev/nvidiactl').exists(), 'Test requires a container without GPU passthrough'
assert not Path('/dev/dri').exists(), 'Test requires a container without GPU passthrough'
Path('/dev/nvidiactl').touch()
Path('/dev/dri').mkdir()
Path('/dev/dri/renderD128').touch()
loader = Path('/sbin/ldconfig')
loader.write_text('#!/bin/sh\nprintf "libEGL_nvidia.so.0\\n"\nseq 1 10000\n')
loader.chmod(0o755)
capture = Path('/usr/local/bin/dbus-run-session')
capture.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
capture.chmod(0o755)
with tempfile.TemporaryDirectory() as home:
    preferences = Path(home) / 'profile/Default/Preferences'
    preferences.parent.mkdir(parents=True)
    preferences.write_text(json.dumps({'browser': {'custom_chrome_frame': False}, 'homepage': 'https://example.test'}))
    (Path(home)/'profile/.system-titlebar-default-v1').touch()
    env = {'HOME': home, 'PATH': '/usr/local/bin:/usr/bin:/bin', 'ENABLE_GPU': 'true'}
    def flags():
        result = subprocess.run(['/bin/bash', '/usr/local/bin/browser-session.sh'], env=env,
                                capture_output=True, text=True, check=True)
        assert 'command not found' not in result.stderr
        return result.stdout.splitlines()
    selected = flags()
    saved = json.loads(preferences.read_text())
    assert saved['browser']['custom_chrome_frame'] is True
    assert saved['homepage'] == 'https://example.test'
    # The migration sets the default once; later user preference changes survive.
    saved['browser']['custom_chrome_frame'] = False
    preferences.write_text(json.dumps(saved))
    flags()
    assert json.loads(preferences.read_text())['browser']['custom_chrome_frame'] is False
    assert '--enable-gpu-rasterization' in selected
    assert '--enable-zero-copy' not in selected
    assert '--disable-features=Vulkan' in selected
    # An NVIDIA device without its driver must not fall into generic zero-copy.
    loader.write_text('#!/bin/sh\nexit 0\n')
    assert '--disable-gpu' in flags()
    Path('/dev/nvidiactl').unlink()
    assert '--enable-zero-copy' in flags()
    env['ENABLE_GPU'] = 'false'
    assert '--disable-gpu' in flags()
print('PASS: restricted-PATH NVIDIA selection, missing-driver fallback, generic GPU and GPU-off flags')
