"""Container-owned browser egress policy and optional WARP supervisor.

Never inherit proxy environment into the OIDC broker. Rules cover private desktop
UIDs and the legacy browser UID, while root/nginx retain normal ingress routing.
"""
import json
import logging
import os
from pathlib import Path
import pwd
import signal
import shutil
import socket
import subprocess
import sys
import time

LOG = logging.getLogger('browser-network')
STATE = Path('/run/brave-network')
POLICY = Path('/etc/brave/policies/managed/network.json')


def configuration(env=os.environ):
    enabled = env.get('WARP_ENABLED', 'false')
    if enabled not in ('true', 'false'):
        raise ValueError('WARP_ENABLED must be true or false')
    mode = 'warp' if enabled == 'true' else 'direct'
    if mode == 'warp' and env.get('WARP_ACCEPT_TOS') != 'true':
        raise ValueError('WARP mode requires WARP_ACCEPT_TOS=true')
    if mode == 'warp' and not shutil.which('warp-svc'):
        raise ValueError('This image was built without WARP; rebuild with INSTALL_WARP=true')
    return {'mode': mode}



def firewall(config, uid):
    return f'''table inet brave_egress {{
 set clients {{
  type uid . inet_service
  elements = {{ {int(uid)} . 40001 }}
 }}
 chain output {{
  type filter hook output priority -10; policy accept;
  meta skuid {{ {int(uid)}, 200000-1000199999 }} jump browser
 }}
 chain browser {{
  ct direction reply accept
  ip daddr 127.0.0.1 meta skuid . tcp dport @clients accept
  reject with icmpx type admin-prohibited
 }}
}}
'''


def policies(config):
    return {'ProxySettings': {'ProxyMode': 'fixed_servers', 'ProxyServer': 'socks5://127.0.0.1:40001',
                              'ProxyBypassList': '<-loopback>'},
            'QuicAllowed': False, 'DnsOverHttpsMode': 'off',
            'WebRtcIPHandling': 'disable_non_proxied_udp',
            'NetworkPredictionOptions': 2}


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def install_firewall(config):
    available = subprocess.run(['nft', 'list', 'tables'], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).returncode == 0
    if not available:
        if config['mode'] == 'warp' or os.environ.get('OIDC_ENABLED') == 'true':
            raise RuntimeError('Private user routing requires NET_ADMIN')
        return  # Ordinary direct-only containers need no extra capability.
    uid = pwd.getpwnam('braveuser').pw_uid
    existing = subprocess.run(['nft', 'list', 'table', 'inet', 'brave_egress'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    rules = ('delete table inet brave_egress\n' if existing else '') + firewall(config, uid)
    subprocess.run(['nft', '-f', '-'], input=rules, text=True, check=True)


def switch():
    config = configuration()
    install_firewall(config)
    with socket.socket(socket.AF_UNIX) as control:
        control.settimeout(15)
        control.connect(str(STATE / 'control.sock'))
        control.sendall(config['mode'].encode() + b'\n')
        if control.recv(32) != b'OK\n':
            raise RuntimeError('Routing switch was not acknowledged')


def setup():
    config = configuration()
    STATE.mkdir(mode=0o755, exist_ok=True)
    install_firewall(config)
    POLICY.parent.mkdir(parents=True, exist_ok=True)
    policy = policies(config)
    if os.environ.get('OIDC_ENABLED') == 'true':
        policy.pop('ProxySettings')  # Each private browser gets its own fixed port.
    write_json(POLICY, policy)
    write_json(STATE / 'config.json', config)
    write_json(STATE / 'status.json', {'mode': config['mode'], 'state': 'direct' if config['mode'] == 'direct' else 'unavailable'})
    return config


def cli(*args):
    return subprocess.run(['warp-cli', '--accept-tos', *args], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=20, check=True).stdout


def probe(config):
    # End-to-end check through the proxy; a listening socket alone is not health.
    result = subprocess.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '8',
                             '--noproxy', '', '--proxy', 'socks5h://127.0.0.1:40000',
                             'https://www.cloudflare.com/cdn-cgi/trace'],
                            capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and (config['mode'] != 'warp' or
                                      any(line in ('warp=on', 'warp=plus') for line in result.stdout.splitlines()))


def desired():
    if os.environ.get('OIDC_ENABLED') != 'true':
        return json.loads((STATE / 'config.json').read_text())
    for path in (STATE / 'users').glob('*/config.json'):
        try:
            if json.loads(path.read_text()).get('mode') == 'warp':
                return {'mode':'warp'}
        except FileNotFoundError:
            continue
    return {'mode':'direct'}


def health_check_interval(config, healthy):
    # Retry promptly during startup/recovery without continuously probing a
    # healthy tunnel. Status is still based on a successful end-to-end check.
    return 2 if config['mode'] == 'warp' and not healthy else 15


def serve():
    config = json.loads((STATE / 'config.json').read_text())
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    daemon = None
    private = os.environ.get('OIDC_ENABLED') == 'true'
    relay = None if private else subprocess.Popen([sys.executable, '/usr/local/bin/browser-relay.py'])
    initialized = False
    try:
        while not stopping:
            if relay is not None and relay.poll() is not None:
                LOG.error('Browser relay exited; restarting with the selected route')
                relay = subprocess.Popen([sys.executable, '/usr/local/bin/browser-relay.py'])
                time.sleep(1)
            updated = desired()
            if updated != config:
                if daemon is not None and daemon.poll() is None:
                    daemon.terminate()
                    try:
                        daemon.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        daemon.kill()
                        daemon.wait()
                daemon = None
                initialized = False
                config = updated
            healthy = False
            try:
                if config['mode'] == 'warp':
                    if daemon is None or daemon.poll() is not None:
                        # WARP's control socket is world-writable by default.
                        # Keep its parent inaccessible to all desktop accounts.
                        for directory in ('/run/cloudflare-warp', '/var/lib/cloudflare-warp'):
                            path = Path(directory)
                            path.mkdir(mode=0o700, parents=True, exist_ok=True)
                            path.chmod(0o700)
                        daemon = subprocess.Popen(['warp-svc'], stdout=sys.stdout, stderr=sys.stderr)
                        initialized = False
                    Path('/run/cloudflare-warp').chmod(0o700)
                    if not initialized:
                        cli('status')  # Wait until IPC is available.
                        try:
                            cli('registration', 'show')
                        except subprocess.CalledProcessError:
                            cli('registration', 'new')
                        cli('mode', 'proxy')
                        cli('tunnel', 'protocol', 'set', 'MASQUE')
                        cli('proxy', 'port', '40000')
                        cli('connect')
                        initialized = True
                healthy = config['mode'] == 'direct' or probe(config)
            except (OSError, subprocess.SubprocessError):
                LOG.warning('Browser proxy unavailable; direct egress remains blocked')
            write_json(STATE / 'status.json', {'mode': config['mode'],
                       'state': 'direct' if config['mode'] == 'direct' else 'connected' if healthy else 'unavailable',
                       'checked_at': time.time()})
            for _ in range(health_check_interval(config, healthy)):
                if stopping or (relay is not None and relay.poll() is not None) or desired() != config:
                    break
                time.sleep(1)
    finally:
        if relay is not None:
            relay.terminate()
            try:
                relay.wait(timeout=5)
            except subprocess.TimeoutExpired:
                relay.kill()
                relay.wait()
        write_json(STATE / 'status.json', {'mode': config['mode'], 'state': 'unavailable'})
        if daemon is not None and daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ['setup']:
        setup()
    elif sys.argv[1:] == ['validate']:
        config = configuration()
        if config['mode'] != 'direct':
            subprocess.run(['nft', 'list', 'tables'], stdout=subprocess.DEVNULL, check=True)
    elif sys.argv[1:] == ['switch']:
        switch()
    elif sys.argv[1:] == ['serve']:
        serve()
    else:
        raise SystemExit('Usage: browser-network.py setup|switch|validate|serve')
