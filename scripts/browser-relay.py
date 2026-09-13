"""Local SOCKS5 CONNECT relay. Root-only control switches all connections together.

No TLS inspection, disk buffering, UDP forwarding, or direct fallback from WARP.
DNS names are passed verbatim to WARP; only direct mode resolves locally.
"""
import asyncio
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import sys

STATE = Path('/run/brave-network')
PORT = 40001
WARP_PORT = 40000
USER_PORTS = range(61000, 61128)
REPLY = b'\x05\x00\x00\x01' + b'\x00' * 6


def private_proxy(address, port):
    ip = ipaddress.ip_address(address.split('%')[0])
    if not (ip.is_loopback or getattr(ip, 'ipv4_mapped', None) and ip.ipv4_mapped.is_loopback):
        return False
    # Reserve the entire range, including free ports: checking only active
    # listeners would allow a bind/connect race while another desktop starts.
    return port in USER_PORTS or port in (PORT, WARP_PORT, 40001)


async def address(reader, kind):
    if kind == 1:
        raw = await reader.readexactly(4)
        host = socket.inet_ntop(socket.AF_INET, raw)
    elif kind == 4:
        raw = await reader.readexactly(16)
        host = socket.inet_ntop(socket.AF_INET6, raw)
    elif kind == 3:
        size = await reader.readexactly(1)
        if not size[0]:
            raise ValueError('Empty destination')
        name = await reader.readexactly(size[0])
        host = name.decode('ascii')
        if any(ord(c) <= 32 for c in host):
            raise ValueError('Invalid destination')
        raw = size + name
    else:
        raise ValueError('Unsupported address family')
    port = await reader.readexactly(2)
    return host, int.from_bytes(port, 'big'), bytes([kind]) + raw + port


async def copy(reader, writer):
    while data := await reader.read(262144):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


class Relay:
    def __init__(self, mode):
        self.mode = mode
        self.connections = set()

    async def client(self, reader, writer):
        task = asyncio.current_task()
        upstream = None
        connected = False
        if len(self.connections) >= 1024:
            writer.close()
            return
        self.connections.add(task)
        try:
            async with asyncio.timeout(15):
                version, count = await reader.readexactly(2)
                methods = await reader.readexactly(count)
                if version != 5 or 0 not in methods:
                    writer.write(b'\x05\xff')
                    await writer.drain()
                    return
                writer.write(b'\x05\x00')
                await writer.drain()
                version, command, reserved, kind = await reader.readexactly(4)
                if (version, command, reserved) != (5, 1, 0):
                    writer.write(b'\x05\x07' + REPLY[2:])
                    await writer.drain()
                    return
                host, port, destination = await address(reader, kind)
                if self.mode == 'warp':
                    remote, upstream = await asyncio.open_connection('127.0.0.1', WARP_PORT)
                    upstream.write(b'\x05\x01\x00')
                    await upstream.drain()
                    if await remote.readexactly(2) != b'\x05\x00':
                        raise ValueError('WARP authentication failed')
                    upstream.write(b'\x05\x01\x00' + destination)
                    await upstream.drain()
                    version, result, reserved, kind = await remote.readexactly(4)
                    if (version, result, reserved) != (5, 0, 0):
                        raise ValueError('WARP connection failed')
                    await address(remote, kind)
                else:
                    # Reject recursion into our own listening port after resolution.
                    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
                    if any(private_proxy(info[4][0], port) for info in infos):
                        raise ValueError('Private proxy chaining is not allowed')
                    # Use the resolved address to avoid a second, inconsistent lookup.
                    for family, _, _, _, endpoint in infos:
                        try:
                            remote, upstream = await asyncio.open_connection(endpoint[0], port, family=family)
                            break
                        except OSError:
                            continue
                    if upstream is None:
                        raise OSError('Destination unavailable')
                writer.write(REPLY)
                await writer.drain()
                connected = True
            async with asyncio.TaskGroup() as group:
                group.create_task(copy(reader, upstream))
                group.create_task(copy(remote, writer))
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, ExceptionGroup):
            if not connected:
                writer.write(b'\x05\x01' + REPLY[2:])
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    # Abort also discards buffered bytes from the previous route.
                    stream.transport.abort()
            self.connections.discard(task)


class Controller:
    def __init__(self, listener, mode, state=STATE):
        self.listener = listener
        self.state = state
        self.mode = mode
        self.worker = None
        self.lock = asyncio.Lock()

    async def stop_worker(self):
        if self.worker is not None and self.worker.returncode is None:
            self.worker.terminate()
            try:
                await asyncio.wait_for(self.worker.wait(), 1)
            except TimeoutError:
                # asyncio's DNS resolver uses threads that cannot be cancelled.
                # Reap the entire worker before acknowledging a new route.
                self.worker.kill()
                await self.worker.wait()

    async def start_worker(self):
        self.worker = await asyncio.create_subprocess_exec(sys.executable, __file__,
            '--worker', self.mode, str(self.listener.fileno()), str(os.getpid()),
            pass_fds=(self.listener.fileno(),), stdout=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(5):
                if await self.worker.stdout.readline() != b'READY\n':
                    raise OSError('Relay worker failed to start')
        except BaseException:
            await self.stop_worker()
            raise

    async def switch(self, mode):
        if mode not in ('direct', 'warp'):
            raise ValueError('Unknown route')
        async with self.lock:
            if mode == self.mode and self.worker is not None and self.worker.returncode is None:
                return
            await self.stop_worker()
            config = self.state / 'config.json'
            temporary = self.state / 'config.tmp'
            temporary.write_text(json.dumps({'mode': mode}) + '\n')
            temporary.replace(config)
            self.mode = mode
            await self.start_worker()

    async def control(self, reader, writer):
        try:
            async with asyncio.timeout(10):
                mode = (await reader.readline()).decode().strip()
                await self.switch(mode)
                writer.write(b'OK\n')
                await writer.drain()
        except (OSError, ValueError, TimeoutError):
            writer.write(b'ERROR\n')
        finally:
            writer.close()


def stop_event():
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    return stopped


async def worker(mode, descriptor, parent):
    # A crashed controller must never leave an orphan using its old route.
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        raise RuntimeError('Could not attach relay worker to its controller')
    global PORT
    with socket.socket(fileno=os.dup(descriptor)) as duplicate:
        PORT = duplicate.getsockname()[1]
    relay = Relay(mode)
    stopped = stop_event()
    listener = socket.socket(fileno=descriptor)
    server = await asyncio.start_server(relay.client, sock=listener)
    print('READY', flush=True)
    async with server:
        await stopped.wait()
    for task in list(relay.connections):
        task.cancel()
    await asyncio.gather(*list(relay.connections), return_exceptions=True)


async def main():
    stopped = stop_event()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', PORT))
    listener.listen(128)
    listener.setblocking(False)
    manager = Controller(listener, json.loads((STATE / 'config.json').read_text())['mode'])
    control = STATE / 'control.sock'
    control.unlink(missing_ok=True)
    # Bind with a restrictive umask so desktop users never get a permission window.
    previous = os.umask(0o177)
    try:
        commands = await asyncio.start_unix_server(manager.control, str(control), limit=64)
    finally:
        os.umask(previous)
    try:
        async with commands:
            async with manager.lock:
                await manager.start_worker()
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), 1)
                except TimeoutError:
                    async with manager.lock:
                        if manager.worker.returncode is not None:
                            await manager.start_worker()
    finally:
        await manager.stop_worker()
        listener.close()
        control.unlink(missing_ok=True)


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--worker':
        asyncio.run(worker(sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))
    else:
        asyncio.run(main())
