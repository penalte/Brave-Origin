"""Readiness is private, retryable, and gates desktop launch on the server."""
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


async def main():
    config = m.single.Config({'OIDC_ISSUER_URL': 'https://id.test', 'OIDC_CLIENT_ID': 'test', 'OIDC_CLIENT_SECRET': 'test'})
    broker = m.Broker(config)
    pending = dict(identity='test', claims={}, flow={'origin': 'https://web.test'}, expires=time.time()+120)
    broker.preparations['ticket'] = pending

    def request(token='ticket', origin='https://web.test'):
        req = make_mocked_request('POST', '/session/prepare', headers={
            'Cookie': '__Host-brave-prepare='+token, 'Origin': origin})
        req['app_origin'] = origin
        return req

    entered, release = asyncio.Event(), asyncio.Event()
    async def launch(*args):
        entered.set()
        await release.wait()
        raise web.HTTPServiceUnavailable(text='Connection unavailable')
    broker.start_session = launch
    task = asyncio.create_task(broker.prepare_session(request()))
    await entered.wait()
    response = await broker.preparation_status(request())
    reported = json.loads(response.text)['tasks']
    assert [t['id'] for t in reported] == ['profile', 'network', 'desktop', 'files'] and reported[0]['state'] == 'running'
    for req in (request('wrong'), request(origin='https://other.test')):
        try:
            await broker.preparation_status(req)
            raise AssertionError('Readiness leaked to another sign-in')
        except web.HTTPForbidden:
            pass
    try:
        await broker.prepare_session(request())
        raise AssertionError('Duplicate launch accepted')
    except web.HTTPConflict:
        pass
    release.set()
    assert (await task).status == 503
    assert pending['progress']['tasks'][0]['state'] == 'error'
    broker.start_session = AsyncMock(return_value=web.HTTPFound('/'))
    assert (await broker.prepare_session(request())).status == 200
    assert 'ticket' not in broker.preparations

    # The countdown runs only once every step is ready, just before the stream
    # may connect; a sign-in that ends at the take-over choice skips it.
    async def launched(identity, claims, flow, progress):
        for step in progress['tasks']:
            step['state'] = 'ready'
        return web.HTTPFound('/')
    broker.preparations['ticket'] = dict(pending, running=False)
    broker.start_session = launched
    before = time.monotonic()
    task = asyncio.create_task(broker.prepare_session(request()))
    await asyncio.sleep(.5)
    progress = json.loads((await broker.preparation_status(request())).text)
    assert progress['phase'] == 'countdown' and 0 < progress['remaining'] <= 3, progress
    assert all(step['state'] == 'ready' for step in progress['tasks']), progress
    assert (await task).status == 200 and time.monotonic()-before >= 3
    broker.preparations['ticket'] = dict(pending, running=False)
    broker.start_session = AsyncMock(return_value=web.HTTPFound('/session/resolve'))
    before = time.monotonic()
    assert (await broker.prepare_session(request())).status == 200
    assert time.monotonic()-before < 1, 'A take-over choice must not count down'

    # Exercise the real launch ordering, stopping just before native desktop setup.
    def steps():
        return {'phase': 'profile', 'tasks': [{'id': step, 'state': 'pending'}
                                              for step in ('profile', 'network', 'desktop', 'files')]}
    with TemporaryDirectory() as directory:
        desktop = m.Desktop(config)
        desktop.uid = 1000
        desktop.metadata = Path(directory)/'metadata.json'
        desktop.metadata.write_text('{"key":"test"}')
        desktop.prepare = AsyncMock(return_value='test')
        desktop.clear_profile_locks = AsyncMock()
        desktop.command = AsyncMock(return_value='1.0')
        desktop.launch_desktop = AsyncMock(side_effect=RuntimeError('desktop boundary'))
        proxy = SimpleNamespace(start=AsyncMock(), wait_ready=AsyncMock(), manager=SimpleNamespace(mode='warp'))
        desktop.startup = steps()
        before = time.monotonic()
        with patch.object(m.user_network, 'register'), patch.object(m.user_network, 'UserProxy', return_value=proxy):
            try:
                await desktop.start_locked('issuer', 'subject')
            except RuntimeError as error:
                assert str(error) == 'desktop boundary'
        assert time.monotonic()-before < 3, 'The countdown must wait until the desktop is prepared'
        desktop.clear_profile_locks.assert_awaited_once()
        proxy.wait_ready.assert_awaited_once()
        state = {task['id']: task['state'] for task in desktop.startup['tasks']}
        assert desktop.startup['phase'] == 'desktop'
        assert state == {'profile': 'ready', 'network': 'ready', 'desktop': 'running', 'files': 'pending'}, state
        # A failed connection never reaches the desktop.
        proxy.wait_ready.side_effect = RuntimeError('WARP unavailable')
        desktop.launch_desktop.reset_mock()
        desktop.startup = steps()
        with patch.object(m.user_network, 'register'), patch.object(m.user_network, 'UserProxy', return_value=proxy):
            try:
                await desktop.start_locked('issuer', 'subject')
            except RuntimeError:
                pass
        desktop.launch_desktop.assert_not_awaited()
        # A profile the installed browser must not open fails before any connection work.
        desktop.metadata.write_text('{"key":"test","version":"2.0"}')
        proxy.start.reset_mock()
        desktop.startup = steps()
        with patch.object(m.user_network, 'register'), patch.object(m.user_network, 'UserProxy', return_value=proxy):
            try:
                await desktop.start_locked('issuer', 'subject')
                raise AssertionError('A profile from a newer browser was opened')
            except m.ProfileTooNew:
                pass
        proxy.start.assert_not_awaited()
        assert desktop.startup['phase'] == 'profile'

    # That refusal reaches the sign-in page as its own message.
    class Refused:
        master_token = directory = None
        async def start(self, issuer, subject):
            raise m.ProfileTooNew('Installed browser is older than this profile')
        async def stop(self):
            pass
    config.launch_timeout, config.maximum = 90, 2
    refusing = m.Broker(config, desktop_factory=Refused)
    refusing.app()  # creates the sharing state that ending a session revokes
    try:
        await refusing.start_session('refused', {'iss': 'https://id.test', 'sub': 'refused', 'exp': time.time()+60}, {'origin': 'https://web.test'},
                                     {'phase': 'profile', 'tasks': [{'id': 'profile', 'state': 'running'}]})
        raise AssertionError('A profile from a newer browser was opened')
    except web.HTTPServiceUnavailable as error:
        assert 'older than the one that last opened your profile' in error.text, error.text
    print('PASS: private progress, duplicate protection, retry, countdown after readiness, profile checked before connection, failed WARP blocks desktop, profile error message')


asyncio.run(main())
