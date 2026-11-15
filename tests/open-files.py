"""Opening My files in the remote browser, and office documents through a document server."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit
from aiohttp import streams, web
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

        def opening(owner, path, origin=ORIGIN, cookie='owner-cookie', token='csrf', action='open'):
            body = streams.StreamReader(Mock(_reading_paused=False), 2 ** 16, loop=asyncio.get_running_loop())
            body.feed_data(json.dumps({'action': action, 'path': path}).encode())
            body.feed_eof()
            request = make_mocked_request('POST', '/files/action', payload=body, headers={
                'Cookie': m.single.COOKIE + '=' + cookie, 'Origin': origin, 'X-CSRF-Token': token,
                'Content-Type': 'application/json'})
            request['app_origin'] = ORIGIN
            return owner.files_action(request)

        def get(path, origin=ORIGIN):
            request = make_mocked_request('GET', path)
            request['app_origin'] = origin
            return request

        session = session_for(broker)
        # Files Brave renders itself open straight from the user's folder.
        response = await opening(broker, 'Trip/beach day.JPG')
        assert json.loads(response.text) == {'opened': True, 'viewer': 'browser'}
        session.browser.open_url.assert_awaited_once_with((files / 'Trip' / 'beach day.JPG').as_uri())
        # Only the owner, from the application itself, and only real files inside My files.
        await refused(opening(broker, 'budget.xlsx', cookie='someone-else'), 403)
        await refused(opening(broker, 'budget.xlsx', origin='https://evil.test'), 403)
        await refused(opening(broker, 'budget.xlsx', token='guessed'), 403)
        for path in ('linked.docx', 'missing.pdf', 'Trip', ''):
            await refused(opening(broker, path), 404)
        for path in ('../../etc/passwd', 'Trip/../budget.xlsx', '.profile'):
            await refused(opening(broker, path), 400)
        assert session.browser.open_url.await_count == 1
        # A type the browser cannot display is refused: opening it would only
        # make the browser download a duplicate into My files.
        for name in ('archive.zip', 'setup.EXE', 'clip.avi', 'photo.heic', 'no-extension'):
            (files / name).write_bytes(b'data')
            assert 'Download it' in await refused(opening(broker, name), 415)
        for name in ('song.MP3', 'film.mkv', 'paper.pdf', 'notes.md'):
            (files / name).write_bytes(b'data')
            await opening(broker, name)
        assert session.browser.open_url.await_count == 5

        # The picker's Open in Brave leaves a note in the session's private
        # folder; the gateway takes it once and applies the same rules.
        runtime = home / 'runtime'
        runtime.mkdir()
        session.browser.directory = runtime
        async def note(content, link=None):
            before = session.browser.open_url.await_count
            if link:
                (runtime / 'open-request').symlink_to(link)
            else:
                (runtime / 'open-request').write_text(content)
            broker.picker_request(session)
            await asyncio.sleep(0.05)
            assert not (runtime / 'open-request').exists() and not (runtime / 'open-request').is_symlink(), 'Note was not consumed'
            return session.browser.open_url.await_count - before
        assert await note(json.dumps({'parts': ['Trip', 'beach day.JPG']})) == 1
        assert session.browser.open_url.await_args.args[0] == (files / 'Trip' / 'beach day.JPG').as_uri()
        for content in (json.dumps({'parts': ['archive.zip']}), json.dumps({'parts': ['..', '..', 'etc', 'passwd']}),
                        json.dumps({'parts': ['linked.docx']}), json.dumps({'parts': 'Trip'}), json.dumps({'parts': []}),
                        'not json', '', json.dumps({'parts': ['x' * 9000]})):
            assert await note(content) == 0, content[:40]
        secret = home / 'secret.json'
        secret.write_text(json.dumps({'parts': ['paper.pdf']}))
        assert await note(None, link=secret) == 0, 'A linked note was followed'
        broker.picker_request(session)  # No note: nothing happens.
        # Without a document server an office file says so instead of opening a download loop.
        unconfigured = session_for(plain)
        assert 'OFFICE_SERVER_URL' in await refused(opening(plain, 'budget.xlsx'), 409)
        await refused(opening(plain, 'budget.xlsx', action='link'), 409)
        unconfigured.browser.open_url.assert_not_awaited()

        # With one, the browser is sent to a viewer page behind a private link.
        response = await opening(broker, 'Report <1>.DOCX')
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
        # The Files page shows the same document through its own link, without opening a tab.
        tabs = session.browser.open_url.await_count
        shown = json.loads((await opening(broker, 'Report <1>.DOCX', action='link')).text)['url']
        assert shown.startswith(ORIGIN + '/office/view?t=') and shown != session.browser.open_url.await_args.args[0]
        assert session.browser.open_url.await_count == tabs
        await refused(opening(broker, 'paper.pdf', action='link'), 415)
        await refused(opening(broker, 'Report <1>.DOCX', action='link', token='guessed'), 403)
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
