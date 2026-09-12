"""Broker grants and live WebSocket permission boundary, with a fake stream server."""
import asyncio
import importlib.util
import time
from types import SimpleNamespace
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


async def main():
    config = m.single.Config({'OIDC_ISSUER_URL':'https://id.test', 'OIDC_CLIENT_ID':'test',
        'OIDC_CLIENT_SECRET':'test', 'AUTO_UPDATE':'false'})
    broker = m.Broker(config)
    app = broker.app()
    app.cleanup_ctx.clear()
    server = TestServer(app)
    await server.start_server()
    owner = m.single.Manager(config)
    owner.state = 'RUNNING'
    owner.owner = dict(cookie='owner', csrf='csrf', origin='https://web.test', name='Owner', expires=time.time()+900)
    broker.sessions['owner'] = owner
    admin = m.single.Manager(config)
    admin.state = 'RUNNING'
    admin.owner = dict(cookie='admin', csrf='admin-csrf', origin='https://web.test', name='Admin', admin=True, expires=time.time()+900)
    broker.sessions['admin'] = admin
    received = []
    upstream_sockets = []
    async def websocket(request):
        assert request.query.get('token') and not request.query.get('role')
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        upstream_sockets.append(ws)
        await ws.send_str('clipboard,PRIVATE')
        await ws.send_bytes(b'\x05PRIVATE')
        await ws.send_bytes(b'\x04video')
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                received.append(msg.data)
        return ws
    upstream_app = web.Application()
    upstream_app.router.add_get('/api/websockets', websocket)
    upstream = TestServer(upstream_app)
    await upstream.start_server()
    async with ClientSession() as upstream_client, ClientSession() as client:
        class Stream:
            def ws_connect(self, url, **kwargs):
                return upstream_client.ws_connect(str(upstream.make_url('/api/websockets'))+'?'+url.split('?')[1], **kwargs)
        owner.http = Stream()
        headers = {'Host':'web.test', 'Origin':'https://web.test'}
        auth = {**headers, 'Cookie':m.single.COOKIE+'=owner', 'X-CSRF-Token':'csrf'}
        async def post(path, data, h=auth):
            return await client.post(server.make_url(path),json=data,headers=h,allow_redirects=False)
        assert (await post('/shares/create',{'minutes':60},headers)).status == 403
        assert (await post('/shares/create',{'minutes':True})).status == 400
        r = await post('/shares/create',{'minutes':60})
        assert r.status == 200
        invite = (await r.json())['url'].split('#')[1]
        r = await post('/shares/open',{'token':invite},headers)
        assert r.status == 200 and (await r.json())['view_only']
        assert m.sharing.VIEW_COOKIE in r.cookies, 'Valid invitation admits guests without OIDC'
        # Authentication has already been verified by the callback before this function.
        r = await broker.admit_viewer(invite, {'iss':'https://id.test','sub':'guest','name':'Guest','exp':time.time()+900})
        token = r.cookies[m.sharing.VIEW_COOKIE].value
        view = {**headers,'Cookie':m.sharing.VIEW_COOKIE+'='+token}
        for path in ('api/files/test', 'api/upload', 'api/tokens', '../admin/status'):
            r = await client.get(server.make_url('/watch/'+path),headers=view)
            assert r.status in (403,404)
        ws = await client.ws_connect(server.make_url('/watch/api/websockets?role=controller'),headers=view)
        frame = await ws.receive(timeout=2)
        assert frame.type == WSMsgType.BINARY and frame.data == b'\x04video'
        await ws.send_str('kd,65')
        await ws.send_str('SETTINGS,{"initialClientWidth":1}')
        await ws.send_bytes(b'\x02microphone')
        await ws.send_str('START_VIDEO')
        await asyncio.sleep(.1)
        assert received == ['START_VIDEO'], received
        assert (await post('/shares/control',{'id':token,'enabled':True},view)).status == 403
        assert (await post('/shares/control',{'id':token,'enabled':True})).status == 200
        await ws.send_str('kd,65')
        await ws.send_str('co,end,secret')
        await asyncio.sleep(.1)
        assert received == ['START_VIDEO','kd,65'], received
        assert (await post('/shares/control',{'id':token,'enabled':False})).status == 200
        await ws.send_str('kd,66')
        await asyncio.sleep(.1)
        assert received == ['START_VIDEO','kd,65']
        assert (await post('/admin/view',{'id':'owner'})).status == 403
        admin_auth = {**headers,'Cookie':m.single.COOKIE+'=admin','X-CSRF-Token':'admin-csrf'}
        r = await post('/admin/view',{'id':'owner'},admin_auth)
        admin_invite = (await r.json())['url'].split('#')[1]
        assert (await post('/shares/open',{'token':admin_invite},headers)).status == 403
        assert (await post('/shares/open',{'token':admin_invite},admin_auth)).status == 200
        assert (await post('/shares/revoke',{})).status == 200
        assert (await ws.receive(timeout=2)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
        assert not broker.shares
        await ws.close()
        r = await post('/shares/create',{'minutes':15,'control':True})
        control_invite = (await r.json())['url'].split('#')[1]
        r = await post('/shares/open',{'token':control_invite},headers)
        control_token = r.cookies[m.sharing.VIEW_COOKIE].value
        assert broker.shares[control_token]['control'], 'Owner-selected control applies on guest admission'
        owner.owner['cookie'] = 'new-generation'
        await broker.prune_shares()
        assert not broker.shares, 'Takeover generation invalidates invitations and participants'
    await upstream.close()
    await server.close()
    print('Sharing passed: Guest invitation gate, CSRF, restricted APIs, media filtering, owner-controlled input, revoke, and admin binding.')


asyncio.run(main())
