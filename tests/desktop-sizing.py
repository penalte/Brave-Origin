"""Verify the browser's bottom corners, not merely the encoded frame size.

Runs only in the disposable OIDC test container. A red test page paints blue
and green markers at its viewport's lower corners; both must reach the lower
corners of the streamed desktop on first connection and after every resize.
"""
import asyncio
import importlib.util
import json
import struct
import time

import av
from aiohttp import ClientSession, WSMsgType
from aiohttp.test_utils import TestServer

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Provider:
    async def authenticate(self, http, code, flow):
        return {'iss': 'https://id.example.test', 'sub': 'size-' + str(time.time_ns()),
                'name': 'Sizing test', 'exp': time.time() + 600}


async def corners(ws, expected, label):
    decoder = av.CodecContext.create('h264', 'r')
    last = None
    async with asyncio.timeout(25):
        async for message in ws:
            raw = message.data
            if message.type != WSMsgType.BINARY or len(raw) < 11 or raw[0] != 4:
                continue
            frame_id, y, width, height = struct.unpack('!4H', raw[2:10])
            await ws.send_str(f'CLIENT_FRAME_ACK {frame_id}')
            if y or (width, height) != expected:
                continue
            try:
                frames = decoder.decode(av.Packet(raw[10:]))
            except av.error.InvalidDataError:
                continue
            for frame in frames:
                picture = frame.to_image()
                last = [picture.getpixel(p) for p in
                        ((width//2, height//2), (24, height-24), (width-24, height-24))]
                red, blue, green = last
                if red[0] > 180 and red[1] < 80 and blue[2] > 180 and blue[0] < 80 and green[1] > 180 and green[0] < 80:
                    print(f'PASS: {label} {width}x{height}: page fills desktop, both lower corners visible', flush=True)
                    return
                picture.save('/tmp/desktop-sizing-failure.png')
                if frame_id % 60 == 0:
                    print(f'Waiting for {label}: center/left/right={last}', flush=True)
    raise AssertionError(f'{label} failed: {last}')


async def main():
    config = m.single.Config({'OIDC_ISSUER_URL': 'https://id.example.test',
                             'OIDC_CLIENT_ID': 'test', 'OIDC_CLIENT_SECRET': 'test',
                             'AUTO_UPDATE': 'false'})
    config.launch_timeout = 90
    config.maximum = 2
    broker = m.Broker(config, oidc=Provider())
    server = TestServer(broker.app())
    await server.start_server()
    headers = {'Host': 'web.example.test', 'Origin': 'https://web.example.test'}
    try:
        async with ClientSession() as client:
            broker.flows['sizing'] = {'cookie': 'sizing', 'origin': headers['Origin'], 'expires': time.time()+60}
            response = await client.get(server.make_url('/auth/callback'),
                params={'state': 'sizing', 'code': 'sizing'}, allow_redirects=False,
                headers={**headers, 'Cookie': m.single.FLOW_COOKIE+'=sizing'})
            assert response.status == 302, await response.text()
            auth = {**headers, 'Cookie': m.single.COOKIE+'='+response.cookies[m.single.COOKIE].value}
            desktop = next(iter(broker.sessions.values())).browser
            page = desktop.home/'Downloads/sizing.html'
            page.write_text('<html><body style="margin:0;background:#ff0000">'
                '<div style="position:fixed;left:0;bottom:0;width:96px;height:96px;background:#0000ff"></div>'
                '<div style="position:fixed;right:0;bottom:0;width:96px;height:96px;background:#00ff00"></div>'
                '</body></html>')
            # Let the nested desktop exist before its first streaming client.
            await asyncio.sleep(3)
            ws = await client.ws_connect(server.make_url('/desktop/api/websockets'), headers=auth)
            await ws.send_str('SETTINGS,'+json.dumps({'displayId': 'primary',
                'initialClientWidth': 1910, 'initialClientHeight': 912, 'framerate': 10}))
            await ws.send_str('START_VIDEO')
            await asyncio.sleep(2)
            for event in ('kd,65507', 'kd,108', 'ku,108', 'ku,65507',
                          'co,end,file://'+str(page), 'kd,65293', 'ku,65293'):
                await ws.send_str(event)
            await corners(ws, (1910, 912), 'fresh login')
            for size in ((1280, 720), (2300, 1200), (900, 1100), (1910, 912)):
                await ws.send_str(f'r,{size[0]}x{size[1]},primary')
                await corners(ws, size, 'resize')
            await ws.close()
            await asyncio.sleep(1)
            ws = await client.ws_connect(server.make_url('/desktop/api/websockets'), headers=auth)
            await ws.send_str('SETTINGS,'+json.dumps({'displayId': 'primary',
                'initialClientWidth': 1910, 'initialClientHeight': 912, 'framerate': 10}))
            await ws.send_str('START_VIDEO')
            await corners(ws, (1910, 912), 'reconnect')
            await ws.close()
    finally:
        for session in broker.sessions.values():
            directory = session.browser.directory
            if directory:
                for name in ('supervisor.log', 'labwc.log', 'selkies.log'):
                    path = directory/name
                    if path.exists():
                        print(name, path.read_text(errors='replace')[-14000:], flush=True)
        await server.close()


asyncio.run(main())
