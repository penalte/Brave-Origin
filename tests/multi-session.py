"""Disposable-container integration: two real desktops and private transfers."""
import asyncio
import importlib.util
import json
import os
import time
import struct
import av
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Provider:
    run = str(time.time_ns())
    async def discovery(self, http):
        return {'authorization_endpoint': 'https://id.example.test/authorize'}

    async def authenticate(self, http, code, flow):
        return {'iss': 'https://id.example.test', 'sub': code + self.run, 'name': code, 'exp': time.time()+900}


async def main():
    os.environ['SELKIES_FRAMERATE'] = '30'
    os.environ['SELKIES_USE_BROWSER_CURSORS'] = 'true'
    os.environ['XKB_DEFAULT_LAYOUT'] = 'pt'
    # Accept media permission prompts only; use the real forwarded devices.
    os.environ['BRAVE_FLAGS'] = '--use-fake-ui-for-media-stream'
    os.environ['OIDC_CLIENT_SECRET'] = 'must-not-reach-desktop'
    config = m.single.Config({'OIDC_ISSUER_URL': 'https://id.example.test', 'OIDC_CLIENT_ID': 'test',
                              'OIDC_CLIENT_SECRET': 'test', 'AUTO_UPDATE': 'false', 'MAX_UPLOAD_MB': '1'})
    config.maximum, config.launch_timeout = 2, 90
    broker = m.Broker(config, oidc=Provider())
    server = TestServer(broker.app())
    await server.start_server()
    headers = {'Host': 'web.example.test', 'Origin': 'https://web.example.test'}
    try:
        async with ClientSession() as client:
            async def login(user):
                broker.flows[user] = {'cookie':user, 'origin':'https://web.example.test', 'expires':time.time()+60}
                response = await client.get(server.make_url('/auth/callback'), params={'state':user,'code':user},
                    headers={**headers, 'Cookie':m.single.FLOW_COOKIE+'='+user}, allow_redirects=False)
                assert response.status == 302, (response.status, await response.text())
                return {**headers, 'Cookie':m.single.COOKIE+'='+response.cookies[m.single.COOKIE].value}
            a, b = await asyncio.gather(login('alice'), login('bob'))
            assert len(broker.sessions) == 2
            desktops = {s.owner['name']:s for s in broker.sessions.values()}
            assert desktops['alice'].browser.uid != desktops['bob'].browser.uid
            assert desktops['alice'].browser.directory != desktops['bob'].browser.directory
            for session in desktops.values():
                process_env = (m.Path('/proc') / str(session.browser.process.pid) / 'environ').read_bytes()
                assert b'SELKIES_FRAMERATE=30\0' in process_env
                assert b'XKB_DEFAULT_LAYOUT=pt\0' in process_env
                assert b'OIDC_CLIENT_SECRET=' not in process_env
            response = await client.get(server.make_url('/auth/login'), headers=headers, allow_redirects=False)
            assert response.status == 302, 'Authentication must remain available for takeover at capacity'
            broker.flows['charlie'] = {'cookie':'charlie', 'origin':headers['Origin'], 'expires':time.time()+60}
            response = await client.get(server.make_url('/auth/callback'), params={'state':'charlie','code':'charlie'},
                headers={**headers,'Cookie':m.single.FLOW_COOKIE+'=charlie'}, allow_redirects=False)
            assert response.status == 409, 'Capacity must reject a new authenticated identity'
            assert len(broker.sessions) == 2
            broker.flows['alice-again'] = {'cookie':'again', 'origin':headers['Origin'], 'expires':time.time()+60}
            response = await client.get(server.make_url('/auth/callback'), params={'state':'alice-again','code':'alice'},
                headers={**headers,'Cookie':m.single.FLOW_COOKIE+'=again'}, allow_redirects=False)
            assert response.status == 302 and response.headers['Location'] == '/session/resolve'
            token = response.cookies['__Host-brave-handoff'].value
            choice_headers = {**headers,'Cookie':'__Host-brave-handoff='+token}
            response = await client.get(server.make_url('/session/resolve'),headers=choice_headers)
            assert response.status == 200 and 'Take over existing desktop' in await response.text()
            response = await client.post(server.make_url('/session/resolve'),headers=choice_headers,
                data={'action':'disconnect','csrf':'wrong'},allow_redirects=False)
            assert response.status == 403 and len(broker.sessions) == 2
            csrf = broker.handoffs[token]['csrf']
            response = await client.post(server.make_url('/session/resolve'),headers=choice_headers,
                data={'action':'cancel','csrf':csrf},allow_redirects=False)
            assert response.status == 302 and len(broker.sessions) == 2
            response = await client.post(server.make_url('/session/resolve'),headers=choice_headers,
                data={'action':'disconnect','csrf':csrf},allow_redirects=False)
            assert response.status == 403, 'Consumed handoff must not be replayable'
            for user, auth in (('alice',a), ('bob',b)):
                response = await client.post(server.make_url('/desktop/api/upload'), headers={**auth,'X-Upload-Path':'same.txt'}, data=user.encode())
                assert response.status == 200, (response.status,await response.text())
                response = await client.get(server.make_url('/api/files/same.txt'), headers=auth)
                assert response.status == 200 and await response.text() == user
                color = '#ff0000' if user == 'alice' else '#0000ff'
                page = ('<html><body style="margin:0;background:'+color+'">'+user+
                        '<input type="file" style="position:absolute;left:20px;top:20px;width:250px;height:40px" '
                        'onchange="this.files[0].text().then(t=>document.body.style.background=t===\''+user+'\'?\'#00ff00\':\'#ffffff\')">'
                        '<div style="position:absolute;left:350px;top:250px;width:150px;height:150px;cursor:text"></div>'
                        '<div style="position:absolute;left:550px;top:250px;width:150px;height:150px;cursor:pointer"></div>'
                        '<script>let a=document.createElement("a");a.href="data:text/plain,'+user+
                        '";a.download="from-brave.txt";a.click();</script></body></html>')
                response = await client.post(server.make_url('/api/upload'), headers={**auth,'X-Upload-Path':'page.html'},data=page.encode())
                assert response.status == 200
                desktop = desktops[user].browser
                await asyncio.sleep(3)
                socket = await client.ws_connect(server.make_url('/desktop/api/websockets'),headers=auth)
                await socket.send_str('SETTINGS,'+json.dumps({'displayId':'primary','initialClientWidth':800,'initialClientHeight':600,'framerate':10}))
                await socket.send_str('START_VIDEO')
                async with asyncio.timeout(40):
                    decoder = av.CodecContext.create('h264','r')
                    async for message in socket:
                        raw = message.data
                        if message.type != m.single.WSMsgType.BINARY or len(raw)<11 or raw[0]!=4:
                            continue
                        frame_id, y, width, height = struct.unpack('!4H',raw[2:10])
                        await socket.send_str(f'CLIENT_FRAME_ACK {frame_id}')
                        if y != 0:
                            continue
                        for frame in decoder.decode(av.Packet(raw[10:])):
                            frame.to_image().save('/tmp/'+user+'-latest.png')
                            plane = frame.reformat(format='rgb24').planes[0]
                            offset = (height//2)*plane.line_size + (width//2)*3
                            r,g,blue = bytes(plane)[offset:offset+3]
                            if not hasattr(desktops[user], 'frame_saved'):
                                frame.to_image().save('/tmp/'+user+'-frame.png')
                                desktops[user].frame_saved = True
                                for event in ('kd,65507', 'kd,108', 'ku,108', 'ku,65507',
                                              'co,end,file://'+str(desktop.home/'Downloads/page.html'),
                                              'kd,65293', 'ku,65293'):
                                    await socket.send_str(event)

                                print('First frame',user,width,height,r,g,blue,flush=True)
                            if user == 'alice' and r>180 and blue<80 or user == 'bob' and blue>180 and r<80:
                                break
                        else:
                            continue
                        break
                    else:
                        raise AssertionError('No video frames')
                desktops[user].test_socket = socket
                desktops[user].test_decoder = decoder
                for _ in range(50):
                    if (desktop.home/'Downloads/from-brave.txt').exists():
                        break
                    await asyncio.sleep(0.1)
                assert (desktop.home/'Downloads/from-brave.txt').read_text() == user, 'Browser download policy did not resolve private HOME'
                if os.environ.get('TEST_PRIVATE_PICKER') == 'true':
                    # Chromium displays an infobar for the test-only media
                    # permission flag. Dismiss it before coordinate-based UI tests.
                    next_click = 0
                    await socket.send_str('REQUEST_KEYFRAME')
                    async with asyncio.timeout(15):
                        while True:
                            if time.monotonic() >= next_click:
                                for event in ('m,770,142,0,0','m,770,142,1,0','m,770,142,0,0'):
                                    await socket.send_str(event)
                                next_click = time.monotonic()+1
                            raw = (await socket.receive()).data
                            if not isinstance(raw,bytes) or len(raw)<11 or raw[0]!=4: continue
                            fid,y,_,_=struct.unpack('!4H',raw[2:10])
                            await socket.send_str(f'CLIENT_FRAME_ACK {fid}')
                            if y: continue
                            frames=decoder.decode(av.Packet(raw[10:]))
                            channel=0 if user=='alice' else 2
                            if any((lambda p:p[channel]>180 and all(p[i]<80 for i in range(3) if i!=channel))(f.to_image().getpixel((400,155))) for f in frames): break
                    from picker_browser import exercise_picker
                    await exercise_picker(socket, desktop, decoder, user)
                    from cursor_browser import exercise_cursor
                    await exercise_cursor(socket, decoder, user)
            # Exercise real admin route changes while both desktop streams remain open.
            saved_owner = dict(desktops['alice'].owner)
            desktops['alice'].owner['admin'] = True
            previous_tos = os.environ.get('WARP_ACCEPT_TOS')
            os.environ['WARP_ACCEPT_TOS'] = 'true'
            identities = {name: (s.browser.process.pid, s.owner['cookie']) for name, s in desktops.items()}
            requests = {}
            async def network_page(request):
                if request.path in requests:
                    requests[request.path].set()
                return web.Response(text='<html><body>Routing works</body></html>', content_type='text/html')
            probe_app = web.Application()
            probe_app.router.add_get('/{probe}', network_page)
            probe_server = TestServer(probe_app)
            await probe_server.start_server()
            async def browse(step):
                async def drain(ws, name):
                    frames = desktops[name].test_decoder
                    async for message in ws:
                        raw = message.data
                        if isinstance(raw,bytes) and len(raw)>10 and raw[0] == 4:
                            await ws.send_str(f'CLIENT_FRAME_ACK {struct.unpack("!H",raw[2:4])[0]}')
                            if raw[4:6] == b'\x00\x00':
                                try:
                                    for frame in frames.decode(av.Packet(raw[10:])):
                                        frame.to_image().save('/tmp/'+name+'-routing.png')
                                except av.error.InvalidDataError:
                                    pass
                drains = [asyncio.create_task(drain(s.test_socket,name)) for name,s in desktops.items()]
                try:
                    for name, session in desktops.items():
                        path = '/'+name+'-'+step
                        requests[path] = asyncio.Event()
                        from gamepad_session import open_browser_page
                        await open_browser_page(session.browser, str(probe_server.make_url(path)))
                        await asyncio.wait_for(requests[path].wait(),20)
                finally:
                    for task in drains:
                        task.cancel()
                    await asyncio.gather(*drains,return_exceptions=True)
            try:
                await browse('before')
                bob_route = desktops['bob'].browser.network.manager.mode
                for enabled in (True, False):
                    response = await client.post(server.make_url('/session/warp'),
                        headers={**a, 'X-CSRF-Token':desktops['alice'].owner['csrf']},
                        json={'enabled':enabled})
                    assert response.status == 200, await response.text()
                    assert (await response.json())['network']['mode'] == ('warp' if enabled else 'direct')
                    for name, session in desktops.items():
                        assert (session.browser.process.pid, session.owner['cookie']) == identities[name]
                        assert session.browser.process.returncode is None
                        assert not session.test_socket.closed
                    response = await client.get(server.make_url('/session/status'),headers=b)
                    assert response.status == 200
                    assert desktops['bob'].browser.network.manager.mode == bob_route
                await browse('after')
                print('PASS: live admin WARP toggles preserve both desktop processes, cookies, streams and HTTP browsing', flush=True)
            finally:
                await probe_server.close()
                desktops['alice'].owner.update(saved_owner)
                if previous_tos is None:
                    os.environ.pop('WARP_ACCEPT_TOS', None)
                else:
                    os.environ['WARP_ACCEPT_TOS'] = previous_tos
            from gamepad_session import exercise_gamepads
            await exercise_gamepads(desktops)
            from media_session import exercise_media, exercise_microphones
            await exercise_media(desktops)
            await exercise_microphones(desktops)
            # A real Selkies viewer joins without replacing either owner desktop.
            invite = await broker.new_grant(desktops['alice'], 15)
            admitted = await broker.admit_viewer(invite, {'iss':'https://id.example.test','sub':'viewer','name':'Viewer','exp':time.time()+600})
            view_token = admitted.cookies[m.sharing.VIEW_COOKIE].value
            viewer_headers = {**headers,'Cookie':m.sharing.VIEW_COOKIE+'='+view_token}
            await asyncio.sleep(1)  # Selkies debounces rapid connections on the Unix peer.
            viewer = await client.ws_connect(server.make_url('/watch/api/websockets'),headers=viewer_headers)
            await viewer.send_str('START_VIDEO')
            async with asyncio.timeout(30):
                async for message in viewer:
                    if message.type == m.single.WSMsgType.BINARY and message.data and message.data[0] in (3,4):
                        break
                    if message.type in (m.single.WSMsgType.CLOSE,m.single.WSMsgType.CLOSED,m.single.WSMsgType.ERROR):
                        raise AssertionError('Viewer closed before receiving video')
                else:
                    raise AssertionError('No viewer video')
            assert len(desktops['alice'].connections) == 1, 'Viewer must not replace owner or count toward owner lifetime'
            state_response = await client.get(server.make_url('/session/status'),headers=a)
            owner_csrf = (await state_response.json())['csrf']
            for enabled in (True, False):
                response = await client.post(server.make_url('/shares/control'),headers={**a,'X-CSRF-Token':owner_csrf},json={'id':view_token,'enabled':enabled})
                assert response.status == 200, await response.text()
                async with asyncio.timeout(10):
                    async for message in viewer:
                        if message.type == m.single.WSMsgType.TEXT and message.data == 'MK_ACCESS,'+str(int(enabled)):
                            break
                    else:
                        raise AssertionError('Control update was not delivered')
            from guest_gamepad import exercise_guest_gamepad
            await exercise_guest_gamepad(client,server,desktops,viewer,view_token,{**a,'X-CSRF-Token':owner_csrf})
            response = await client.post(server.make_url('/shares/revoke'),headers={**a,'X-CSRF-Token':owner_csrf},json={})
            assert response.status == 200
            await viewer.close()
            assert len(broker.sessions) == 2
            print('Live sharing passed: video, owner preserved, control grant/revoke and stop sharing.',flush=True)
            # UID boundaries protect another session's stream and files.
            for endpoint in ('stream.sock', 'wayland-0', 'pulse/native'):
                target = desktops['bob'].browser.directory / endpoint
                assert target.is_socket()
                process = await asyncio.create_subprocess_exec('/usr/sbin/runuser','-u',
                    __import__('pwd').getpwuid(desktops['alice'].browser.uid).pw_name,'--','python3','-c',
                    'import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])',str(target),
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                assert await process.wait() != 0
            response = await client.post(server.make_url('/api/upload'),headers={**a,'Origin':'https://attacker.test','X-Upload-Path':'evil'},data=b'x')
            assert response.status == 403
            response = await client.post(server.make_url('/api/upload'),headers={**a,'X-Upload-Path':'big','X-Upload-Total':str(2*1024*1024)},data=b'x')
            assert response.status == 413
            response = await client.post(server.make_url('/api/upload'),headers={**a,
                'X-Upload-Path':'big','X-Upload-Id':'test','X-Upload-Offset':str(1024*1024)},data=b'x')
            assert response.status == 413, 'Chunk offsets must count toward the file size limit'
            alice_uid = desktops['alice'].browser.uid
            delete_headers = {**a, 'X-My-Files-Action':'delete'}
            response = await client.delete(server.make_url('/api/files/same.txt'), headers={**delete_headers,'Origin':'https://attacker.test'})
            assert response.status == 403
            response = await client.delete(server.make_url('/api/files/same.txt'), headers=a)
            assert response.status == 403
            response = await client.delete(server.make_url('/api/files/same.txt'), headers=delete_headers)
            assert response.status == 204, (response.status, await response.text())
            response = await client.get(server.make_url('/api/files/same.txt'), headers=b)
            assert await response.text() == 'bob', 'Delete crossed identities'
            home = desktops['alice'].browser.home
            (home/'Downloads/link').symlink_to(home/'profile')
            response = await client.delete(server.make_url('/api/files/link/Local%20State'), headers=delete_headers)
            assert response.status == 403
            response = await client.delete(server.make_url('/api/files/'), headers=delete_headers)
            assert response.status == 403
            assert (home/'profile/Local State').exists()
            print('PASS: private file deletion, origin/header enforcement, symlink and root rejection', flush=True)
            response = await client.post(server.make_url('/auth/logout'),headers={**a,'X-CSRF-Token':desktops['alice'].owner['csrf']})
            assert response.status == 200
            assert not m.single.Browser.pids(alice_uid)
            assert desktops['bob'].browser.running()
            response = await client.get(server.make_url('/api/files/same.txt'),headers=a)
            assert response.status == 403
            response = await client.get(server.make_url('/api/files/same.txt'),headers=b)
            assert await response.text() == 'bob'
            response = await client.post(server.make_url('/auth/logout'),headers={**b,'X-CSRF-Token':desktops['bob'].owner['csrf']})
            assert response.status == 200
            assert not broker.sessions
            if os.environ.get('TEST_PRIVATE_PICKER') == 'true':
                await login('failure')
                failed = next(iter(broker.sessions.values()))
                uid = failed.browser.uid
                from pathlib import Path
                import signal
                pids = [pid for pid in m.single.Browser.pids(uid)
                        if b'/usr/local/bin/file-picker.py' in Path(f'/proc/{pid}/cmdline').read_bytes()]
                assert len(pids) == 1
                os.kill(pids[0], signal.SIGTERM)
                for _ in range(300):
                    if not broker.sessions:
                        break
                    await asyncio.sleep(.1)
                assert not broker.sessions, 'Picker failure must close its session'
                assert not m.single.Browser.pids(uid), 'Picker failure left browser processes alive'
                print('PASS: picker failure closes its desktop and browser', flush=True)
    finally:
        for session in broker.sessions.values():
            if session.browser.directory and (session.browser.directory/'browser.log').exists():
                print((session.browser.directory/'browser.log').read_text(), flush=True)
        await server.close()
    print('PASS: two real private desktops, video, upload/download isolation, cross-UID socket rejection and independent logout')


asyncio.run(main())
