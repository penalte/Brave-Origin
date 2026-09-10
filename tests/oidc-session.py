"""Run in a disposable running OIDC image. Real Brave lifecycle, fake OIDC exchange."""
import asyncio
import importlib.util
import re
import time
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

spec = importlib.util.spec_from_file_location('manager', '/usr/local/bin/session-manager.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Provider:
    async def discovery(self, http):
        return {'authorization_endpoint': 'https://id.example.test/authorize'}
    async def authenticate(self, http, code, flow):
        return {'iss': 'https://id.example.test', 'sub': code, 'exp': time.time()+600, 'name': code}

async def main():
    config = m.Config({'OIDC_ISSUER_URL':'https://id.example.test',
                       'OIDC_CLIENT_ID':'test', 'OIDC_CLIENT_SECRET':'test', 'AUTO_UPDATE':'false'})
    manager = m.Manager(config, oidc=Provider())
    server = TestServer(manager.app())
    await server.start_server()
    headers = {'Host':'web.example.test'}
    async with ClientSession() as client:
        # Derive the HTTPS origin, preserving public ports and IPv6.
        for host in ('web.example.test', 'web.example.test:9443', '[::1]:8443'):
            async with client.get(server.make_url('/auth/login'), headers={'Host':host,
                    'X-Forwarded-Host':'attacker.test', 'X-Forwarded-Proto':'http'}, allow_redirects=False) as response:
                assert response.status == 302, await response.text()
                query = parse_qs(urlsplit(response.headers['Location']).query)
                assert query['redirect_uri'] == ['https://' + host + '/auth/callback']
                state = query['state'][0]
                flow_cookie = response.cookies[m.FLOW_COOKIE].value
            async with client.get(server.make_url('/auth/callback'), params={'state':state,'code':'alice'},
                    headers={'Host':'other.example.test','Cookie':m.FLOW_COOKIE+'='+flow_cookie}, allow_redirects=False) as response:
                assert response.status == 400, 'Login flow crossed hosts'
        for host in ('user@web.example.test', 'web.example.test:99999'):
            async with client.get(server.make_url('/auth/login'),headers={'Host':host},allow_redirects=False) as response:
                assert response.status == 400
        async with client.get(server.make_url('/session/status'),headers={**headers,'Origin':'https://attacker.test'}) as response:
            assert response.status == 403
        async def callback(user, state):
            manager.flows[state] = {'cookie':state, 'expires':time.time()+60, 'origin':'https://web.example.test'}
            return await client.get(server.make_url('/auth/callback'), params={'state':state,'code':user},
                    headers={**headers, 'Cookie':m.FLOW_COOKIE+'='+state}, allow_redirects=False)
        first, second = await asyncio.gather(callback('alice','a'), callback('bob','b'))
        assert sorted([first.status,second.status]) == [302,409], (first.status,second.status,await first.text(),await second.text())
        winner = first if first.status == 302 else second
        cookie = winner.cookies[m.COOKIE].value
        owner_headers = {**headers, 'Cookie':m.COOKIE+'='+cookie}
        async with client.get(server.make_url('/desktop/'),headers={**owner_headers,'Host':'other.example.test'}) as response:
            assert response.status == 403, 'Session cookie crossed hosts'
        assert manager.browser.running()
        await asyncio.sleep(1)
        assert manager.browser.running(), 'Browser exited before becoming usable'
        old_uid, old_home = manager.browser.uid, manager.browser.home
        # The browser follows the window manager's socket, wherever the desktop
        # put it, so the kiosk window rules always apply. Socket names depend on
        # startup order, so take the published one rather than assuming a number.
        published = Path('/tmp/brave-desktop-ready').read_text().strip()
        assert re.fullmatch(r'wayland-[0-9]+', published), published
        assert m.Browser.app_display() == published
        assert (Path('/tmp/runtime-braveuser') / published).is_socket()
        try:
            await manager.browser.clear_profile_locks(__import__('pwd').getpwuid(old_uid).pw_name)
            raise AssertionError('Unlocked a running profile')
        except RuntimeError as error:
            assert 'Profile processes still running' in str(error)
        (old_home/'private.txt').write_text('private user data')
        import os
        os.chown(old_home/'private.txt', old_uid, old_uid)
        async with client.get(server.make_url('/desktop/'),headers=headers) as response:
            assert response.status == 403
        async with client.get(server.make_url('/desktop/'),headers=owner_headers) as response:
            assert response.status == 200, (response.status,await response.text())
        async with client.get(server.make_url('/api/files/'),headers=owner_headers) as response:
            assert response.status == 403
        # Send the request line verbatim: a client library resolves dot segments
        # before they go out, which is exactly what hid this from the allowlist.
        async def verbatim(target):
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write((f'GET {target} HTTP/1.1\r\nHost: web.example.test\r\n'
                          f'Cookie: {m.COOKIE}={cookie}\r\nConnection: close\r\n\r\n').encode())
            await writer.drain()
            head = (await reader.read()).split(b'\r\n', 1)[0].decode()
            writer.close()
            return int(head.split()[1])
        for escape in ('/desktop/assets/../api/status', '/desktop/assets/%2e%2e/api/status',
                       '/desktop/assets/..%2fapi/status', '/assets/%2e%2e/api/files/',
                       '/desktop/./api/status'):
            assert await verbatim(escape) == 403, f'Allowlist escaped by {escape}'
        assert await verbatim('/desktop/') == 200, 'Allowlist rejected the desktop itself'
        ws = await client.ws_connect(server.make_url('/desktop/api/websockets'), headers=owner_headers)
        try:
            await client.ws_connect(server.make_url('/desktop/api/websockets'), headers=owner_headers)
            raise AssertionError('Second tab took over')
        except __import__('aiohttp').WSServerHandshakeError as error:
            assert error.status == 409
        await ws.send_str('SETTINGS,' + json.dumps({'displayId':'primary', 'initialClientWidth':1280,
                                                  'initialClientHeight':720, 'framerate':10}))
        async with asyncio.timeout(30):
            async for message in ws:
                if message.type == m.WSMsgType.BINARY and len(message.data) > 100:
                    break
            else:
                raise AssertionError('No video data')
        async with client.post(server.make_url('/auth/logout'),headers=owner_headers) as response:
            assert response.status == 403
        async with client.post(server.make_url('/auth/logout'),headers={**owner_headers,'X-CSRF-Token':manager.owner['csrf']}) as response:
            assert response.status == 200, await response.text()
        assert manager.state == 'IDLE' and not m.Browser.pids(old_uid)
        # Drain buffered frames until the transport proves it closed.
        async with asyncio.timeout(5):
            async for _ in ws:
                pass
        assert ws.closed
        other = await callback('charlie','c')
        assert other.status == 302, await other.text()
        assert manager.browser.uid != old_uid
        audio = await asyncio.create_subprocess_exec('runuser','-u',__import__('pwd').getpwuid(manager.browser.uid).pw_name,
                '--','env','PULSE_SERVER=unix:/tmp/runtime-braveuser/pulse/oidc','pactl','info',
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        assert await audio.wait() == 0, 'Profile cannot access audio'
        proc = await asyncio.create_subprocess_exec('runuser','-u',__import__('pwd').getpwuid(manager.browser.uid).pw_name,
                                                   '--','cat',str(old_home/'private.txt'),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        assert await proc.wait() != 0, 'Cross-profile file read allowed'
        async with client.get(server.make_url('/desktop/'),headers=owner_headers) as response:
            assert response.status == 403, 'Old owner retained access'
        manager.owner['expires'] = time.time() - 1
        for _ in range(80):
            if manager.state == 'IDLE': break
            await asyncio.sleep(0.1)
        assert manager.state == 'IDLE'
        # Reproduce a persisted Chromium lock naming a previous container host.
        # The socket/cookie targets must survive; only singleton links are removed.
        singleton_target = old_home / 'singleton-target.txt'
        singleton_target.write_text('must survive recovery')
        for filename, target in (('SingletonLock', 'old-container-host-2197'),
                                 ('SingletonCookie', str(singleton_target)),
                                 ('SingletonSocket', str(singleton_target))):
            path = old_home / 'profile' / filename
            path.unlink(missing_ok=True)
            path.symlink_to(target)
        again = await callback('alice','d')
        assert again.status == 302, await again.text()
        assert singleton_target.read_text() == 'must survive recovery'
        assert (old_home / 'private.txt').read_text() == 'private user data'
        manager.connected_once = True
        manager.last_disconnect = time.monotonic() - config.grace - 1
        for _ in range(100):
            if manager.state == 'IDLE': break
            await asyncio.sleep(0.1)
        assert manager.state == 'IDLE', 'Disconnected browser stayed running'
    await server.close()
    print('PASS: concurrent admission, real Brave start/stop, video stream, second-tab rejection, live socket logout, idle state, CSRF, private profiles, stale session rejection')

asyncio.run(main())
