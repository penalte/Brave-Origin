"""Disposable container: real nftables and unprivileged TCP/UDP egress."""
import importlib.util
import os
import socket
import subprocess
import threading

spec = importlib.util.spec_from_file_location('network', '/usr/local/bin/browser-network.py')
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)

for value in ('socks5://user:pass@localhost:1080', 'http://localhost:80/path', 'file:///tmp/x',
              'http://localhost:80;DIRECT', 'socks5://localhost:0'):
    try:
        n.configuration({'BROWSER_NETWORK_MODE': 'proxy', 'BROWSER_PROXY_URL': value})
    except ValueError:
        pass
    else:
        raise AssertionError(value)
assert n.configuration({}) == {'mode': 'direct'}
assert n.policies({'mode': 'direct'}) == {}
subprocess.run(['useradd', '-u', '200001', 'network-test'], check=True)
os.environ.update(BROWSER_NETWORK_MODE='proxy', BROWSER_PROXY_URL='http://127.0.0.1:18080')
servers = []
for port in (18080, 18081):
    server = socket.socket()
    server.bind(('127.0.0.1', port))
    server.listen()
    servers.append(server)
    def serve(listener):
        while True:
            connection, _ = listener.accept()
            connection.sendall(b'ok')
            connection.close()
    threading.Thread(target=serve, args=(server,), daemon=True).start()
n.setup()
def check(port, allowed):
    code = f'import socket; s=socket.create_connection(("127.0.0.1",{port}),1); assert s.recv(2)==b"ok"'
    result = subprocess.run(['runuser', '-u', 'network-test', '--', 'python3', '-c', code], capture_output=True)
    assert (result.returncode == 0) == allowed, (port, result.stderr)
check(18080, True)
check(18081, False)
# Root ingress/controller requests remain unaffected.
with socket.create_connection(('127.0.0.1',18081),1) as s:
    assert s.recv(2) == b'ok'
# IPv6 and direct DNS remain denied for the browser UID.
for family, address in [('AF_INET', "('1.1.1.1',53)"), ('AF_INET6', "('::1',53)")]:
    code = f'import socket; s=socket.socket(socket.{family},socket.SOCK_DGRAM); s.connect({address}); s.send(b"test")'
    result = subprocess.run(['runuser','-u','network-test','--','python3','-c',code],capture_output=True)
    assert result.returncode != 0, family
# A stopped proxy does not unlock another route.
servers[0].close()
check(18081, False)
os.environ['BROWSER_NETWORK_MODE'] = 'direct'
n.setup()
check(18081, True)
print('PASS: proxy-only egress, direct TCP/DNS/IPv6 denial, root access and direct-mode restore')
