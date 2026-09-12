"""Container-owned browser egress policy and optional WARP supervisor.

Never inherit proxy environment into the OIDC broker. Rules cover private desktop
UIDs and the legacy browser UID, while root/nginx retain normal ingress routing.
"""
import ipaddress
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
from urllib.parse import urlsplit

LOG = logging.getLogger('browser-network')
STATE = Path('/run/brave-network')
POLICY = Path('/etc/brave/policies/managed/network.json')


def configuration(env=os.environ):
    enabled = env.get('WARP_ENABLED', 'false')
    if enabled not in ('true', 'false'):
        raise ValueError('WARP_ENABLED must be true or false')
    mode = 'warp' if enabled == 'true' else env.get('BROWSER_NETWORK_MODE', 'direct')
    if mode not in ('direct', 'proxy', 'warp'):
        raise ValueError('BROWSER_NETWORK_MODE must be direct, proxy or warp')
    if mode == 'direct':
        return {'mode': mode}
    if mode == 'warp' and env.get('WARP_ACCEPT_TOS') != 'true':
        raise ValueError('WARP mode requires WARP_ACCEPT_TOS=true')
    if mode == 'warp' and not shutil.which('warp-svc'):
        raise ValueError('This image was built without WARP; rebuild with INSTALL_WARP=true')
    value = 'socks5://127.0.0.1:40000' if mode == 'warp' else env.get('BROWSER_PROXY_URL', '')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'socks5') or not parsed.hostname or not parsed.port
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)):
        raise ValueError('Use an unauthenticated http://host:port or socks5://host:port proxy')
    # Resolve once as root. Pin Brave and its firewall to the same endpoint.
    address = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)[0][4][0]
    ip = ipaddress.ip_address(address)
    if ip.is_unspecified or ip.is_multicast:
        raise ValueError('Proxy address must be a unicast destination')
    host = f'[{ip}]' if ip.version == 6 else str(ip)
    return {'mode': mode, 'address': str(ip), 'port': parsed.port,
            'proxy': f'{parsed.scheme}://{host}:{parsed.port}'}


def firewall(config, uid):
    ip = ipaddress.ip_address(config['address'])
    family = 'ip6' if ip.version == 6 else 'ip'
    return f'''table inet brave_egress {{
 chain output {{
  type filter hook output priority -10; policy accept;
  meta skuid {{ {int(uid)}, 200000-1000199999 }} jump browser
 }}
 chain browser {{
  ct direction reply accept
  {family} daddr {ip} tcp dport {int(config['port'])} accept
  reject with icmpx type admin-prohibited
 }}
}}
'''


def policies(config):
    if config['mode'] == 'direct':
        return {}
    return {'ProxySettings': {'ProxyMode': 'fixed_servers', 'ProxyServer': config['proxy'],
                              'ProxyBypassList': '<-loopback>'},
            'QuicAllowed': False, 'DnsOverHttpsMode': 'off',
            'WebRtcIPHandling': 'disable_non_proxied_udp',
            'NetworkPredictionOptions': 2}


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def setup():
    config = configuration()
    STATE.mkdir(mode=0o755, exist_ok=True)
    if config['mode'] != 'direct':
        # No fallback: missing NET_ADMIN or an invalid ruleset aborts startup
        # before any browser is launched. Replace only our own table atomically.
        uid = pwd.getpwnam('braveuser').pw_uid
        existing = subprocess.run(['nft', 'list', 'table', 'inet', 'brave_egress'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        rules = ('delete table inet brave_egress\n' if existing else '') + firewall(config, uid)
        subprocess.run(['nft', '-f', '-'], input=rules, text=True, check=True)
    else:
        existing = subprocess.run(['nft', 'list', 'table', 'inet', 'brave_egress'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if existing:
            subprocess.run(['nft', 'delete', 'table', 'inet', 'brave_egress'], check=True)
    POLICY.parent.mkdir(parents=True, exist_ok=True)
    write_json(POLICY, policies(config))
    write_json(STATE / 'config.json', config)
    write_json(STATE / 'status.json', {'mode': config['mode'], 'state': 'direct' if config['mode'] == 'direct' else 'unavailable'})
    return config


def cli(*args):
    return subprocess.run(['warp-cli', '--accept-tos', *args], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=20, check=True).stdout


def probe(config):
    # End-to-end check through the proxy; a listening socket alone is not health.
    result = subprocess.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '8',
                             '--noproxy', '', '--proxy', config['proxy'].replace('socks5:', 'socks5h:'),
                             'https://www.cloudflare.com/cdn-cgi/trace'],
                            capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and (config['mode'] != 'warp' or
                                      any(line in ('warp=on', 'warp=plus') for line in result.stdout.splitlines()))


def serve():
    config = json.loads((STATE / 'config.json').read_text())
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    daemon = None
    initialized = False
    try:
        while not stopping:
            updated = json.loads((STATE / 'config.json').read_text())
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
            for _ in range(15):
                if stopping:
                    break
                time.sleep(1)
    finally:
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
    elif sys.argv[1:] == ['serve']:
        serve()
    else:
        raise SystemExit('Usage: browser-network.py setup|serve')
