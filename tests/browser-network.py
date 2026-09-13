"""Disposable container: real relay, nftables, switching and throughput checks."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import time

spec = importlib.util.spec_from_file_location('network', '/usr/local/bin/browser-network.py')
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
spec = importlib.util.spec_from_file_location('relay', '/usr/local/bin/browser-relay.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
assert n.configuration({}) == {'mode': 'direct'}
assert n.configuration({'BROWSER_NETWORK_MODE':'proxy'}) == {'mode':'direct'}
assert n.policies({'mode':'direct'}) == n.policies({'mode':'warp'})
os.environ['WARP_ENABLED'] = 'false'
n.setup()

async def main():
    async def echo(reader, writer):
        try:
            while data := await reader.read(262144):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
    server = await asyncio.start_server(echo, '127.0.0.1', 18081)
    relay = await asyncio.create_subprocess_exec('python3', '/usr/local/bin/browser-relay.py')
    warp = None
    try:
        for _ in range(100):
            if Path('/run/brave-network/control.sock').exists():
                break
            await asyncio.sleep(.05)
        async def command(mode):
            process = await asyncio.create_subprocess_exec('python3', '/usr/local/bin/browser-network.py', 'switch',
                env=dict(os.environ, WARP_ENABLED=str(mode == 'warp').lower(), WARP_ACCEPT_TOS='true'))
            assert await process.wait() == 0
        async def connect(host='127.0.0.1', port=18081, success=True):
            reader, writer = await asyncio.open_connection('127.0.0.1',40001)
            writer.write(b'\x05\x01\x00')
            assert await reader.readexactly(2) == b'\x05\x00'
            raw = host.encode()
            writer.write(b'\x05\x01\x00\x03' + bytes([len(raw)]) + raw + port.to_bytes(2,'big'))
            reply = await reader.readexactly(10)
            assert (reply[1] == 0) == success, reply
            return reader, writer
        # Desktop UID can use relay but cannot connect directly, even in direct mode.
        code = "import socket; s=socket.create_connection(('127.0.0.1',40001),2); s.sendall(bytes([5,1,0])); assert s.recv(2)==bytes([5,0])"
        process = await asyncio.create_subprocess_exec('runuser','-u','braveuser','--','python3','-c',code)
        assert await process.wait() == 0
        for code in [
            "import socket; socket.create_connection(('127.0.0.1',18081),1)",
            "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/brave-network/control.sock')",
            "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('1.1.1.1',53)); s.send(b'x')",
            "import socket; s=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM); s.connect(('::1',53)); s.send(b'x')",
        ]:
            process = await asyncio.create_subprocess_exec('runuser','-u','braveuser','--','python3','-c',code,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            assert await process.wait() != 0, code
        reader, writer = await connect()
        writer.write(b'hello'); assert await reader.readexactly(5) == b'hello'
        await command('warp')
        assert await asyncio.wait_for(reader.read(),2) == b''
        writer.close()
        reader, writer = await connect(success=False)  # No WARP: fail closed.
        writer.close()
        # Fake WARP speaks real SOCKS, checks DNS stays remote and relays bytes.
        destinations = []
        async def fake_warp(reader, writer):
            upstream = None
            try:
                assert await reader.readexactly(3) == b'\x05\x01\x00'
                writer.write(b'\x05\x00')
                version, cmd, reserved, kind = await reader.readexactly(4)
                host, port, _ = await r.address(reader,kind)
                destinations.append(host)
                remote, upstream = await asyncio.open_connection('127.0.0.1',18081)
                writer.write(r.REPLY)
                async with asyncio.TaskGroup() as group:
                    group.create_task(r.copy(reader,upstream))
                    group.create_task(r.copy(remote,writer))
            finally:
                writer.close()
                if upstream: upstream.close()
        warp = await asyncio.start_server(fake_warp, '127.0.0.1',40000)
        reader, writer = await connect('must-not-resolve.invalid')
        writer.write(b'warp'); assert await reader.readexactly(4) == b'warp'
        assert destinations == ['must-not-resolve.invalid']
        await command('direct')
        assert await asyncio.wait_for(reader.read(),2) == b''
        writer.close()
        # Repeated switches never leave old streams alive; same-mode is a no-op.
        for mode in ('warp','direct') * 10:
            reader, writer = await connect()
            await command(mode)
            assert await asyncio.wait_for(reader.read(),2) == b''
            writer.close()
        reader, writer = await connect()
        await command('direct')
        writer.write(b'preserved'); assert await reader.readexactly(9) == b'preserved'
        writer.close()
        reader, writer = await connect('127.0.0.1',40001,success=False)
        writer.close()
        # Deliberately stall libc DNS. Switching must also stop its resolver
        # thread, including retries that would otherwise outlive cancellation.
        resolver = Path('/etc/resolv.conf')
        original = resolver.read_text()
        queries = []
        query_seen = asyncio.Event()
        class DNS(asyncio.DatagramProtocol):
            def datagram_received(self, data, peer):
                queries.append(data)
                query_seen.set()
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(DNS,
            local_addr=('127.0.0.1',53))
        pending = None
        try:
            resolver.write_text('nameserver 127.0.0.1\noptions timeout:1 attempts:5\n')
            reader, pending = await asyncio.open_connection('127.0.0.1',40001)
            pending.write(b'\x05\x01\x00')
            assert await reader.readexactly(2) == b'\x05\x00'
            name = b'pending.invalid'
            pending.write(b'\x05\x01\x00\x03'+bytes([len(name)])+name+b'\x00\x50')
            await asyncio.wait_for(query_seen.wait(),3)
            await command('warp')
            count = len(queries)
            await asyncio.sleep(1.3)
            assert len(queries) == count, 'Old direct DNS continued after switching to WARP'
            assert await reader.read() == b''
        finally:
            resolver.write_text(original)
            transport.close()
            if pending: pending.close()
            await command('direct')
        # Measure sustained full-duplex relay overhead against a direct local socket.
        async def bench(proxied):
            reader, writer = await connect() if proxied else await asyncio.open_connection('127.0.0.1',18081)
            block = os.urandom(262144)
            total = len(block)*256
            async def send():
                for _ in range(256):
                    writer.write(block)
                    await writer.drain()
            start = time.perf_counter()
            task = asyncio.create_task(send())
            for _ in range(256):
                assert await reader.readexactly(len(block)) == block
            await task
            duration = time.perf_counter()-start
            writer.close()
            await writer.wait_closed()
            return total / duration / 1024 / 1024
        direct = await bench(False)
        proxied = await bench(True)
        print(f'64 MiB echo throughput: direct={direct:.1f} MiB/s relay={proxied:.1f} MiB/s',flush=True)
        assert proxied > 20, 'Unexpectedly slow local relay'
        async def latency(proxied):
            reader, writer = await connect() if proxied else await asyncio.open_connection('127.0.0.1',18081)
            start = time.perf_counter()
            for _ in range(1000):
                writer.write(b'x')
                assert await reader.readexactly(1) == b'x'
            elapsed = (time.perf_counter()-start)  # 1000 samples, expressed in ms each.
            writer.close()
            await writer.wait_closed()
            return elapsed
        direct_latency, relay_latency = await latency(False), await latency(True)
        print(f'Local echo RTT: direct={direct_latency:.3f} ms relay={relay_latency:.3f} ms',flush=True)
        # Controller crashes must not orphan a still-running direct worker.
        reader, writer = await connect()
        relay.kill()
        await relay.wait()
        assert await asyncio.wait_for(reader.read(),2) == b''
        writer.close()
        print('PASS: permanent firewall, root-only control, remote DNS, WARP outage blocking, 20 live switches, connection and pending DNS cleanup, no-op and binary integrity',flush=True)
    finally:
        if relay.returncode is None:
            relay.terminate()
            await relay.wait()
        server.close(); await server.wait_closed()
        if warp: warp.close(); await warp.wait_closed()

asyncio.run(main())
