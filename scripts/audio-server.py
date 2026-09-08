#!/usr/bin/env python3
"""Relay PulseAudio PCM to authenticated browsers through loopback-only nginx."""
import asyncio
import contextlib
import http
import os

from websockets.legacy.server import serve

CLIENTS = set()


async def health(path, headers):
    if headers.get('Upgrade', '').lower() != 'websocket':
        return http.HTTPStatus.OK, [('Content-Type', 'text/plain')], b'Browser audio ready\n'


async def client(socket):
    CLIENTS.add(socket)
    try:
        await socket.wait_closed()
    finally:
        CLIENTS.discard(socket)


async def capture():
    while True:
        if not CLIENTS:
            await asyncio.sleep(0.2)
            continue
        proc = await asyncio.create_subprocess_exec(
            'parec', '-d', os.environ.get('PULSE_SOURCE', '@DEFAULT_MONITOR@'),
            '--rate=44100', '--channels=2', '--format=s16le', '--latency-msec=30',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            while CLIENTS:
                # Preserve complete stereo samples across partial pipe reads.
                try:
                    pcm = await proc.stdout.readexactly(4096)
                except asyncio.IncompleteReadError:
                    break
                for socket in tuple(CLIENTS):
                    # Disconnect slow clients before they accumulate stale audio.
                    if socket.transport.get_write_buffer_size() > 16384:
                        socket.fail_connection(1013)
                        CLIENTS.discard(socket)
                        continue
                    try:
                        await asyncio.wait_for(socket.send(pcm), timeout=0.1)
                    except Exception:
                        socket.fail_connection()
                        CLIENTS.discard(socket)
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
            await proc.wait()
        await asyncio.sleep(0.1)


async def main():
    async with serve(client, '127.0.0.1', 4901, process_request=health,
                     max_size=1024, max_queue=1, write_limit=16384,
                     ping_interval=20, ping_timeout=20, close_timeout=2):
        await capture()


if __name__ == '__main__':
    asyncio.run(main())
