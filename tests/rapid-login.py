"""Sign-in waits for same-profile cleanup without blocking other identities."""
import asyncio
import importlib.util
import time
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

spec = importlib.util.spec_from_file_location('multi','/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

async def main():
    config=m.single.Config({'OIDC_ISSUER_URL':'https://id.test','OIDC_CLIENT_ID':'test','OIDC_CLIENT_SECRET':'test'})
    config.maximum=4
    config.launch_timeout=90
    cleaned=asyncio.Event()
    starts=[]
    class Desktop:
        master_token=None
        directory=Path('/tmp/test-private-runtime')
        async def start(self,issuer,subject):
            if subject=='alice': assert cleaned.is_set(), 'Profile reopened during cleanup'
            starts.append(subject)
    broker=m.Broker(config,desktop_factory=Desktop)
    broker.app()
    old=m.single.Manager(config,browser=Desktop())
    old.state='STOPPING'
    broker.sessions['alice']=old
    await old.lock.acquire()
    def claims(name): return {'iss':'https://id.test','sub':name,'name':name,'exp':time.time()+300}
    flow={'origin':'https://web.test'}
    first=asyncio.create_task(broker.start_session('alice',claims('alice'),flow))
    second=asyncio.create_task(broker.start_session('alice',claims('alice'),flow))
    await asyncio.sleep(.05)
    assert not first.done() and not second.done() and 'alice' not in starts
    response=await asyncio.wait_for(broker.start_session('bob',claims('bob'),flow),1)
    assert response.status==302 and starts==['bob'], 'Cleanup blocked unrelated login'
    cleaned.set()
    old.state='IDLE'
    old.lock.release()
    await asyncio.sleep(.05)  # Include the lock-release/map-removal gap in logout.
    assert 'alice' not in starts
    broker.sessions.pop('alice')
    responses=await asyncio.wait_for(asyncio.gather(first,second),2)
    assert sorted(r.headers['Location'] for r in responses)==['/','/session/resolve']
    assert starts.count('alice')==1, 'Concurrent callbacks started duplicate desktops'
    failed=m.single.Manager(config,browser=Desktop())
    failed.state='ERROR'
    broker.sessions['failed']=failed
    try:
        await broker.start_session('failed',claims('failed'),flow)
        raise AssertionError('Unsafe failed profile was reopened')
    except web.HTTPServiceUnavailable: pass
    # Preparation tickets are same-origin, expiring, one-use, and return the
    # actual owner/handoff cookies to the browser after asynchronous cleanup.
    pending=dict(identity='alice',claims=claims('alice'),flow=flow,expires=time.time()+120)
    broker.preparations['ticket']=pending
    def request(origin='https://web.test'):
        req=make_mocked_request('POST','/session/prepare',headers={
            'Cookie':'__Host-brave-prepare=ticket','Origin':origin})
        req['app_origin']='https://web.test'
        return req
    try:
        await broker.prepare_session(request('https://evil.test'))
        raise AssertionError('Cross-origin preparation accepted')
    except web.HTTPForbidden: pass
    response=await broker.prepare_session(request())
    assert response.status==200 and '/session/resolve' in response.text
    assert '__Host-brave-handoff' in response.cookies
    try:
        await broker.prepare_session(request())
        raise AssertionError('Preparation ticket replay accepted')
    except web.HTTPForbidden: pass
    for session in broker.sessions.values():
        if session.http: await session.http.close()
    print('PASS: rapid re-login waits for cleanup, concurrent callbacks share one launch, unrelated users proceed, failed cleanup stays closed')

asyncio.run(main())
