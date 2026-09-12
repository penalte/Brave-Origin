"""Check real Wayland geometry after client resize requests in a test container."""
import asyncio
import json
import struct

import av
import websockets


async def receive(ws, message_type):
    async with asyncio.timeout(20):
        async for raw in ws:
            if not isinstance(raw, str) or not raw.startswith('{'):
                continue
            message = json.loads(raw)
            if message.get('type') != message_type:
                continue
            return message
    raise AssertionError(f'Missing {message_type}')


async def receive_frame(ws, dimensions):
    # Verify encoded pixels: older Pixelflux builds do not broadcast a
    # stream_resolution message for every resize, even when it succeeds.
    async with asyncio.timeout(20):
        async for raw in ws:
            if not isinstance(raw, bytes) or len(raw) < 10 or raw[0] != 4:
                continue
            frame_id, y, width, height = struct.unpack('!4H', raw[2:10])
            await ws.send(f'CLIENT_FRAME_ACK {frame_id}')
            if raw[1] != 1 or y != 0 or (width, height) != dimensions:
                continue
            decoder = av.CodecContext.create('h264', 'r')
            frames = decoder.decode(av.Packet(raw[10:]))
            assert frames and (frames[0].width, frames[0].height) == dimensions
            print(f'Decoded desktop at {width}x{height}', flush=True)
            return
    raise AssertionError(f'Missing video frame: {dimensions}')


async def main():
    # Loopback bypasses only the HTTP login; this exercises the installed
    # streaming server and compositor, without changing a real user session.
    async with websockets.connect('ws://127.0.0.1:8082/api/websockets', max_size=32 * 1024 * 1024) as ws:
        settings = (await receive(ws, 'server_settings'))['settings']
        assert settings['manual_resolution']['value'] is False, settings
        assert settings['enable_resize']['value'] is True, settings
        await ws.send('SETTINGS,' + json.dumps({
            'displayId': 'primary', 'manual_resolution': False,
            'initialClientWidth': 1280, 'initialClientHeight': 720,
            'framerate': 10,
        }))
        await receive_frame(ws, (1280, 720))
        # Same session: grow, change aspect ratio, shrink, then reconnect.
        for dimensions in ((2560, 1440), (2560, 1300), (3440, 1440),
                           (3840, 2160), (900, 1200), (1280, 720)):
            await ws.send(f'r,{dimensions[0]}x{dimensions[1]},primary')
            await receive_frame(ws, dimensions)
        await ws.send('STOP_VIDEO')
    async with websockets.connect('ws://127.0.0.1:8082/api/websockets', max_size=32 * 1024 * 1024) as ws:
        settings = (await receive(ws, 'server_settings'))['settings']
        assert settings['manual_resolution']['value'] is False
        await ws.send('SETTINGS,' + json.dumps({
            'displayId': 'primary', 'manual_resolution': False,
            'initialClientWidth': 1920, 'initialClientHeight': 1080,
            'framerate': 10,
        }))
        await receive_frame(ws, (1920, 1080))
        await ws.send('STOP_VIDEO')
    print('Display passed: 1440p, windowed, ultrawide, 4K, portrait, shrink, reconnect.')


asyncio.run(main())
