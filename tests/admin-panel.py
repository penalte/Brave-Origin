"""Admin endpoint authorization against the real broker middleware."""
import asyncio
import importlib.util
import time
import tempfile
from pathlib import Path
import json
from unittest.mock import patch
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.admin_claim({'groups':['admin']}, 'groups', {})
assert not m.admin_claim({'groups':'admin'}, 'groups', {})
assert not m.admin_claim({'groups':['administrator']}, 'groups', {})
assert not m.admin_claim({'groups':[{'name':'admin'}]}, 'groups', {})
assert m.admin_claim({'roles':['operators']}, 'roles', {'OIDC_ADMIN_GROUP':'operators'})


async def main():
    config = m.single.Config({'OIDC_ISSUER_URL':'https://id.example.test',
        'OIDC_CLIENT_ID':'test', 'OIDC_CLIENT_SECRET':'test', 'AUTO_UPDATE':'false'})
    config.maximum = 2
    broker = m.Broker(config)
    app = broker.app()
    app.cleanup_ctx.clear()  # No desktop startup or global system mutations.
    session = m.single.Manager(config)
    session.state = 'RUNNING'
    session.owner = dict(cookie='test-cookie', csrf='test-csrf', origin='https://web.example.test',
                         name='<script>test</script>', expires=time.time()+60, admin=False)
    key = 'a'*64
    broker.sessions[key] = session
    server = TestServer(app)
    await server.start_server()
    headers = {'Host':'web.example.test','Origin':'https://web.example.test','Cookie':m.single.COOKIE+'=test-cookie'}
    temporary = tempfile.TemporaryDirectory()
    m.user_network.USERS = Path(temporary.name)/'users'
    m.user_network.FORCE = Path(temporary.name)/'force.json'
    for identity, name in ((key,'<script>test</script>'),('b'*64,'Offline user')):
        directory = m.user_network.USERS/identity
        directory.mkdir(parents=True)
        (directory/'identity.json').write_text(json.dumps({'key':identity,'uid':200001,'name':name,'warp_enabled':True}))
    class Proxy:
        key = 'a'*64
        enabled = True
        def status(self, admin=False):
            _, data = m.user_network.metadata(self.key)
            return {'mode':'warp' if self.enabled else 'direct', 'can_toggle':not m.user_network.forced() and (admin or data.get('allow_direct',False))}
        async def switch(self, enabled): self.enabled = enabled
    session.browser.network = Proxy()
    try:
        async with ClientSession() as client:
            r = await client.get(server.make_url('/admin/status'),headers=headers)
            assert r.status == 403
            session.owner['admin'] = True
            r = await client.get(server.make_url('/admin/status'),headers=headers)
            assert r.status == 200
            payload = await r.json()
            assert payload['users'][0]['name'] == '<script>test</script>'
            assert payload['users'][1]['name'] == 'Offline user' and payload['users'][1]['state'] == 'OFFLINE'
            assert 'cookie' not in str(payload) and 'csrf' not in str(payload)
            r = await client.post(server.make_url('/admin/warp'),headers=headers,json={'enabled':True})
            assert r.status == 403  # Missing CSRF.
            r = await client.post(server.make_url('/admin/warp'),headers={**headers,'Origin':'https://evil.example','X-CSRF-Token':'test-csrf'},json={'enabled':True})
            assert r.status == 403
            r = await client.post(server.make_url('/admin/warp'),headers={**headers,'X-CSRF-Token':'test-csrf'},json={'enabled':'true'})
            assert r.status == 400
            auth = {**headers,'X-CSRF-Token':'test-csrf'}
            with patch.object(m.user_network.network, 'configuration', lambda env: {}):
                # Grant permission, then act as the ordinary owner.
                r = await client.post(server.make_url('/admin/users/warp-permission'),headers=auth,json={'id':key,'allowed':True})
                assert r.status == 200, await r.text()
                session.owner['admin'] = False
                r = await client.post(server.make_url('/session/warp'),headers=auth,json={'enabled':False})
                assert r.status == 200 and not session.browser.network.enabled
                assert not m.user_network.metadata(key)[1]['warp_enabled']
                r = await client.post(server.make_url('/admin/users/warp-permission'),headers=auth,json={'id':key,'allowed':True})
                assert r.status == 403
                session.owner['admin'] = True
                r = await client.post(server.make_url('/admin/users/warp-permission'),headers=auth,json={'id':key,'allowed':False})
                assert r.status == 200 and session.browser.network.enabled
                session.owner['admin'] = False
                r = await client.post(server.make_url('/session/warp'),headers=auth,json={'enabled':False})
                assert r.status == 403
                session.owner['admin'] = True
                r = await client.post(server.make_url('/admin/warp'),headers=auth,json={'enabled':True})
                assert r.status == 200 and m.user_network.forced()
                r = await client.post(server.make_url('/session/warp'),headers=auth,json={'enabled':False})
                assert r.status == 403, 'Force WARP must also apply to admins'
                r = await client.post(server.make_url('/admin/warp'),headers=auth,json={'enabled':False})
                assert r.status == 200 and not m.user_network.forced()
                r = await client.post(server.make_url('/admin/users/warp-permission'),headers=auth,json={'id':'../escape','allowed':True})
                assert r.status == 400
            assert broker.sessions[key] is session and session.state == 'RUNNING'
            session.owner['expires'] = time.time()-1
            r = await client.get(server.make_url('/admin/status'),headers=headers)
            assert r.status == 403
    finally:
        await server.close()
        temporary.cleanup()
    print('PASS: admin-only status, CSRF/origin checks, strict input and session expiry')


asyncio.run(main())
