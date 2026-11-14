"""Opening My files in the remote browser, and office documents through a document server."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ORIGIN = 'https://web.test'
SECRET = 'office-secret-for-tests-0123456789'


async def refused(call, status):
    try:
        await call
    except web.HTTPException as error:
        assert error.status == status, (error.status, error.text)
        return error.text
    raise AssertionError(f'Expected {status}')


async def main():
    base = {'OIDC_ISSUER_URL': 'https://id.test', 'OIDC_CLIENT_ID': 'test', 'OIDC_CLIENT_SECRET': 'test'}
    try:
        m.single.Config({**base, 'OFFICE_SERVER_URL': 'http://office.test'})
        raise AssertionError('A document server without HTTPS was accepted')
    except ValueError:
        pass
    plain = m.Broker(m.single.Config(base))
    broker = m.Broker(m.single.Config({**base, 'OFFICE_SERVER_URL': 'https://office.test/', 'OFFICE_JWT_SECRET': SECRET}))
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        files = home / 'Downloads'
        (files / 'Trip').mkdir(parents=True)
        (files / 'Trip' / 'beach day.JPG').write_bytes(b'picture')
        (files / 'Report <1>.DOCX').write_bytes(b'document-bytes')
        (files / 'budget.xlsx').write_bytes(b'sheet')
        (files / 'linked.docx').symlink_to('/etc/passwd')

        def session_for(owner):
            session = m.single.Manager(owner.config, browser=AsyncMock())
            session.browser.home = home
            session.state = 'RUNNING'
            session.owner = {'cookie': 'owner-cookie', 'csrf': 'csrf', 'origin': ORIGIN, 'expires': time.time() + 600}
            owner.sessions['alice'] = session
            return session

        def post(path, origin=ORIGIN, cookie='owner-cookie', action='open'):
            request = make_mocked_request('POST', path, headers={
                'Cookie': m.single.COOKIE + '=' + cookie, 'Origin': origin, 'X-My-Files-Action': action})
            request['app_origin'] = ORIGIN
            return request

        def get(path, origin=ORIGIN):
            request = make_mocked_request('GET', path)
            request['app_origin'] = origin
            return request

        session = session_for(broker)
        # Files Brave renders itself open straight from the user's folder.
        response = await broker.proxy(post('/desktop/api/files/Trip/beach%20day.JPG'))
        assert json.loads(response.text) == {'opened': True, 'viewer': 'browser'}
        session.browser.open_url.assert_awaited_once_with((files / 'Trip' / 'beach day.JPG').as_uri())
        # Only the owner, from the application itself, and only real files inside My files.
        await refused(broker.proxy(post('/api/files/budget.xlsx', cookie='someone-else')), 403)
        await refused(broker.proxy(post('/api/files/budget.xlsx', origin='https://evil.test')), 403)
        for path in ('/api/files/linked.docx', '/api/files/missing.pdf', '/api/files/Trip', '/api/files/../../etc/passwd',
                     '/api/files/Trip/../budget.xlsx', '/api/files/'):
            await refused(broker.proxy(post(path)), 404)
        assert session.browser.open_url.await_count == 1
        # A type the browser cannot display is refused: opening it would only
        # make the browser download a duplicate into My files.
        for name in ('archive.zip', 'setup.EXE', 'clip.avi', 'photo.heic', 'no-extension'):
            (files / name).write_bytes(b'data')
            assert 'Download it' in await refused(broker.proxy(post('/api/files/' + name)), 415)
        for name in ('song.MP3', 'film.mkv', 'paper.pdf', 'notes.md'):
            (files / name).write_bytes(b'data')
            await broker.proxy(post('/api/files/' + name))
        assert session.browser.open_url.await_count == 5

        # Without a document server an office file says so instead of opening a download loop.
        unconfigured = session_for(plain)
        assert 'OFFICE_SERVER_URL' in await refused(plain.proxy(post('/api/files/budget.xlsx')), 409)
        unconfigured.browser.open_url.assert_not_awaited()

        # With one, the browser is sent to a viewer page behind a private link.
        response = await broker.proxy(post('/api/files/Report%20%3C1%3E.DOCX'))
        assert json.loads(response.text)['viewer'] == 'office'
        address = urlsplit(session.browser.open_url.await_args.args[0])
        token = parse_qs(address.query)['t'][0]
        assert (address.scheme + '://' + address.netloc, address.path) == (ORIGIN, '/office/view') and len(token) >= 40
        page = (await broker.office_view(get('/office/view?t=' + token))).text
        assert '<script src="https://office.test/web-apps/apps/api/documents/api.js">' in page
        assert '<1>' not in page, 'File name reached the page unescaped'
        config = json.loads(page.split('new DocsAPI.DocEditor("viewer",', 1)[1].split(');else', 1)[0])
        signed = m.single.jwt.decode(config.pop('token'), SECRET, algorithms=['HS256'])
        assert signed == config, 'The signed configuration differs from the one the page uses'
        document = config['document']
        assert config['documentType'] == 'word' and document['fileType'] == 'docx' and document['title'] == 'Report <1>.DOCX'
        assert config['editorConfig']['mode'] == 'view' and document['permissions']['edit'] is False
        assert document['url'] == ORIGIN + '/office/file?t=' + token
        # The document server reads exactly that file through the link.
        _, fd, info = broker.document(get('/office/file?t=' + token))
        assert os.read(fd, 100) == b'document-bytes' and info.st_size == 14
        os.close(fd)
        # A changed file is a new document to the server, never a stale cached one.
        (files / 'Report <1>.DOCX').write_bytes(b'a newer version')
        page = (await broker.office_view(get('/office/view?t=' + token))).text
        assert json.loads(page.split('"viewer",', 1)[1].split(');else', 1)[0])['document']['key'] != document['key']

        # The link dies with a wrong token, another address, a swapped file, time, and the session.
        for request in (get('/office/file?t=wrong'), get('/office/file'), get('/office/view?t=' + token, 'https://other.test')):
            await refused(broker.office_file(request), 404)
        (files / 'Report <1>.DOCX').unlink()
        (files / 'Report <1>.DOCX').symlink_to('/etc/passwd')
        await refused(broker.office_file(get('/office/file?t=' + token)), 404)
        (files / 'Report <1>.DOCX').unlink()
        (files / 'Report <1>.DOCX').write_bytes(b'back')
        broker.document(get('/office/file?t=' + token))
        broker.documents[token]['expires'] = time.time() - 1
        await refused(broker.office_view(get('/office/view?t=' + token)), 404)
        broker.documents[token]['expires'] = time.time() + 60
        session.state = 'STOPPING'
        await refused(broker.office_file(get('/office/file?t=' + token)), 404)
        session.state = 'RUNNING'
        broker.sessions.clear()
        await refused(broker.office_file(get('/office/file?t=' + token)), 404)
    # Local files are reachable only inside the user's own storage.
    policy = m.single.policies('${user_home}/Downloads', {}, files='/config/users')
    assert policy['URLBlocklist'] == ['file://*'] and policy['URLAllowlist'] == ['file:///config/users/']
    assert m.single.policies('/config/downloads', {})['URLAllowlist'] == ['file:///config/downloads/']
    print('PASS: files open in the remote browser; office documents use a signed, view-only, expiring link')

asyncio.run(main())
