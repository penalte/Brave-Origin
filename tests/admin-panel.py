"""Admin endpoint authorization against the real broker middleware."""
import asyncio
import importlib.util
import time
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
    broker.sessions['test'] = session
    server = TestServer(app)
    await server.start_server()
    headers = {'Host':'web.example.test','Origin':'https://web.example.test','Cookie':m.single.COOKIE+'=test-cookie'}
    try:
        async with ClientSession() as client:
            r = await client.get(server.make_url('/admin/status'),headers=headers)
            assert r.status == 403
            session.owner['admin'] = True
            r = await client.get(server.make_url('/admin/status'),headers=headers)
            assert r.status == 200
            payload = await r.json()
            assert payload['users'][0]['name'] == '<script>test</script>'
            assert 'cookie' not in str(payload) and 'csrf' not in str(payload)
            r = await client.post(server.make_url('/admin/warp'),headers=headers,json={'enabled':True})
            assert r.status == 403  # Missing CSRF.
            r = await client.post(server.make_url('/admin/warp'),headers={**headers,'Origin':'https://evil.example','X-CSRF-Token':'test-csrf'},json={'enabled':True})
            assert r.status == 403
            r = await client.post(server.make_url('/admin/warp'),headers={**headers,'X-CSRF-Token':'test-csrf'},json={'enabled':'true'})
            assert r.status == 400
            calls = []
            class Process:
                returncode = 0
                async def communicate(self): return b'', b''
                async def wait(self): return 0
            async def launch(*args, **kwargs):
                calls.append((args[-1], kwargs['env']['WARP_ENABLED']))
                return Process()
            async def end(key, desktop):
                calls.append(('end', key))
                broker.sessions.pop(key)
            with patch.object(m.asyncio, 'create_subprocess_exec', launch), patch.object(broker, 'end', end):
                r = await client.post(server.make_url('/admin/warp'),headers={**headers,'X-CSRF-Token':'test-csrf'},json={'enabled':True})
                assert r.status == 200, await r.text()
            assert calls == [('validate','true'),('end','test'),('setup','true')], calls
            assert broker.state == 'IDLE' and not broker.sessions
            broker.sessions['test'] = session
            session.owner['expires'] = time.time()-1
            r = await client.get(server.make_url('/admin/status'),headers=headers)
            assert r.status == 403
    finally:
        await server.close()
    print('PASS: admin-only status, CSRF/origin checks, strict input and session expiry')


asyncio.run(main())
