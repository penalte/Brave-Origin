"""Verify PCM data, WebSocket ping/pong, and reconnects against the real relay."""
import asyncio
import io
import math
import struct
import wave

from websockets.legacy.client import connect


def tone():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<hh', value, value) for value in
                       (int(4000 * math.sin(i * 2 * math.pi * 440 / 44100))
                        for i in range(44100))))
    return buffer.getvalue()


async def main():
    for _ in range(2):
        async with connect('ws://127.0.0.1:4901/audio') as socket:
            pong = await socket.ping(b'x11-audio-ping')
            await asyncio.wait_for(pong, 3)
            await asyncio.wait_for(socket.recv(), 5)  # Wait for capture to start.
            player = await asyncio.create_subprocess_exec(
                'paplay', stdin=asyncio.subprocess.PIPE)
            playback = asyncio.create_task(player.communicate(tone()))
            heard = False
            for _ in range(100):
                pcm = await asyncio.wait_for(socket.recv(), 3)
                assert isinstance(pcm, bytes) and len(pcm) % 4 == 0
                if max(abs(x[0]) for x in struct.iter_unpack('<h', pcm)) > 1000:
                    heard = True
                    break
            await playback
            assert player.returncode == 0 and heard, 'Audio tone did not reach the WebSocket'
    print('Audio PCM, ping/pong, and reconnect checks passed.')


asyncio.run(main())
