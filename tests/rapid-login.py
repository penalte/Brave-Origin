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
    from html.parser import HTMLParser
    class Inputs(HTMLParser):
        value = None
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('name') == 'csrf': self.value = attrs.get('value')
    # The choice page must never strand its user: a second click receives the
    # first click's result, and a missing or timed-out ticket leads back to the
    # portal instead of a dead-end error.
    def choice(token, csrf, action='takeover'):
        class Submission(dict):
            cookies={'__Host-brave-handoff':token}
            headers={'Origin':'https://web.test'}
            async def post(self): return {'csrf':csrf,'action':action}
        return Submission(app_origin='https://web.test')
    alice=broker.sessions['alice']
    alice.connected_once=True  # A stream was open, so the takeover has to settle.
    before=alice.owner['cookie']
    offered=broker.offer_handoff('alice',{**claims('alice'),'exp':time.time()+3600},flow)
    token=offered.cookies['__Host-brave-handoff'].value
    assert 590 < broker.handoffs[token]['expires']-time.time() <= 600, 'Choice page must stay valid for ten minutes'
    csrf=broker.handoffs[token]['csrf']
    clicks=[asyncio.create_task(broker.resolve_session(choice(token,csrf))) for _ in range(2)]
    await asyncio.sleep(.2)
    abandoned=asyncio.create_task(broker.resolve_session(choice(token,csrf)))
    await asyncio.sleep(.2)
    abandoned.cancel()  # A browser that gives up must not stop the takeover.
    assert alice.state=='STARTING' and not any(click.done() for click in clicks)
    responses=await asyncio.wait_for(asyncio.gather(*clicks),10)
    owner=alice.owner['cookie']
    assert owner!=before and alice.state=='RUNNING', 'Takeover did not finish'
    assert all(r.status==302 and r.cookies[m.single.COOKIE].value==owner for r in responses), 'A repeated click lost the new session'
    late=await broker.resolve_session(choice(token,csrf,'disconnect'))
    assert late.cookies[m.single.COOKIE].value==owner and broker.sessions.get('alice') is alice, 'A repeated choice acted twice'
    offered=broker.offer_handoff('alice',claims('alice'),flow)
    token=offered.cookies['__Host-brave-handoff'].value
    csrf=broker.handoffs[token]['csrf']
    broker.handoffs[token]['expires']=time.time()-1
    for attempt in (lambda: broker.resolve_session(choice(token,csrf)),
                    lambda: broker.resolve_page(choice('','')), lambda: broker.resolve_page(choice('unknown',''))):
        try:
            await attempt()
            raise AssertionError('A missing or timed-out choice was accepted')
        except web.HTTPFound as redirect:
            assert redirect.headers['Location']=='/?choice_expired=1'
    assert alice.owner['cookie']==owner and alice.state=='RUNNING'
    # A takeover interrupted midway must leave the desktop usable.
    offered=broker.offer_handoff('alice',claims('alice'),flow)
    token=offered.cookies['__Host-brave-handoff'].value
    alice.connected_once=True
    interrupted=asyncio.create_task(broker.apply_choice(broker.handoffs.pop(token),'takeover'))
    await asyncio.sleep(.2)
    interrupted.cancel()
    await asyncio.gather(interrupted,return_exceptions=True)
    assert alice.state=='RUNNING', 'An interrupted takeover left the desktop stuck'
    for action in ('cancel', 'takeover', 'disconnect'):
        offered=broker.offer_handoff('alice',claims('alice'),flow)
        token=offered.cookies['__Host-brave-handoff'].value
        req=make_mocked_request('GET','/session/resolve',headers={'Cookie':'__Host-brave-handoff='+token})
        req['app_origin']='https://web.test'
        rendered=await broker.resolve_page(req)
        parser=Inputs();parser.feed(rendered.text)
        assert parser.value==broker.handoffs[token]['csrf'], 'Broken form token'
        class Submission(dict):
            cookies={'__Host-brave-handoff':token}
            headers={'Origin':'https://web.test'}
            async def post(self): return {'csrf':parser.value,'action':action}
        if action=='disconnect':
            async def stop(): broker.sessions['alice'].state='IDLE'
            broker.sessions['alice'].stop_locked=stop
        response=await broker.resolve_session(Submission(app_origin='https://web.test'))
        assert response.status==302
        if action=='cancel': assert response.headers['Location']=='/?session_cancelled=1'
        if action=='takeover': assert m.single.COOKIE in response.cookies
    assert 'alice' not in broker.sessions
    # Native Chromium form POSTs use Origin:null under no-referrer. The
    # handoff document needs same-origin, while foreign/null origins stay denied.
    security = broker.app().middlewares[0]
    async def document(request): return web.Response(text='form')
    for path, policy in (('/session/resolve', 'same-origin'), ('/auth/callback', 'no-referrer')):
        req = make_mocked_request('GET', path, headers={'Host':'web.test'})
        assert (await security(req, document)).headers['Referrer-Policy'] == policy
    for origin in ('null', 'https://evil.test'):
        req = make_mocked_request('POST', '/session/resolve', headers={'Host':'web.test', 'Origin':origin})
        try:
            await security(req, document)
            raise AssertionError('Invalid form origin accepted')
        except web.HTTPForbidden:
            pass
    req = make_mocked_request('POST', '/session/resolve', headers={'Host':'web.test', 'Origin':'https://web.test'})
    assert (await security(req, document)).status == 200
    for session in broker.sessions.values():
        if session.http: await session.http.close()
    print('PASS: rapid re-login waits for cleanup, concurrent callbacks share one launch, unrelated users proceed, failed cleanup stays closed')

asyncio.run(main())
