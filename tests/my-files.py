"""The My files API: listing, download with ranges, thumbnails, resumable upload and file actions."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

spec = importlib.util.spec_from_file_location('multi', '/usr/local/bin/multi-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ORIGIN = 'https://web.test'
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082')


async def main():
    base = {'OIDC_ISSUER_URL': 'https://id.test', 'OIDC_CLIENT_ID': 'test', 'OIDC_CLIENT_SECRET': 'test',
            'MAX_UPLOAD_MB': '1'}
    broker = m.Broker(m.single.Config(base))
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        files = home / 'Downloads'
        (files / 'Trip').mkdir(parents=True)
        (files / 'Trip' / 'dot.png').write_bytes(PNG)
        (files / 'notes.txt').write_bytes(b'0123456789')
        (files / 'page.html').write_bytes(b'<script>alert(1)</script>')
        (files / 'paper.pdf').write_bytes(b'%PDF-1.4')
        (files / 'tool.exe').write_bytes(b'MZ')
        (files / 'plan.docx').write_bytes(b'PK')
        (files / '.hidden').write_bytes(b'state')
        (files / 'link.txt').symlink_to('/etc/passwd')
        (files / 'away').symlink_to('/etc')

        session = m.single.Manager(broker.config, browser=AsyncMock())
        session.browser.home, session.browser.uid = home, os.getuid()
        session.state = 'RUNNING'
        session.owner = {'cookie': 'owner-cookie', 'csrf': 'csrf-token', 'origin': ORIGIN, 'expires': time.time() + 600}
        broker.sessions['alice'] = session
        # The worker normally runs as the session's user; here it is this test's user.
        broker.thumbnailer = lambda _: m.files_api.Thumbnailer(
            ['python3', '/usr/local/bin/file-picker.py', '--render', str(files)], dict(os.environ))

        application = web.Application(middlewares=[broker.app().middlewares[0]])
        application.add_routes(broker.files_routes())
        client = TestClient(TestServer(application))
        await client.start_server()
        host = {'Host': 'web.test', 'Cookie': m.single.COOKIE + '=owner-cookie'}
        change = {**host, 'Origin': ORIGIN, 'X-CSRF-Token': 'csrf-token'}

        async def get(route, expect=200, headers=None, **query):
            response = await client.get(route, params=query, headers={**host, **(headers or {})})
            assert response.status == expect, (route, query, response.status, await response.text())
            return response

        async def act(expect=200, headers=None, **body):
            response = await client.post('/files/action', json=body, headers=headers or change)
            assert response.status == expect, (body, response.status, await response.text())
            return await response.json() if expect == 200 else await response.text()

        try:
            # Listing: folders first, no dotfiles, no links, with what each entry can do.
            listing = await (await get('/files/list')).json()
            names = [entry['name'] for entry in listing['entries']]
            assert names == ['Trip', 'notes.txt', 'page.html', 'paper.pdf', 'plan.docx', 'tool.exe'], names
            by_name = {entry['name']: entry for entry in listing['entries']}
            assert by_name['Trip']['folder'] and by_name['Trip']['kind'] == 'folder' and not by_name['Trip']['opens']
            assert by_name['notes.txt'] == {'name': 'notes.txt', 'folder': False, 'size': 10, 'kind': 'text', 'opens': True, 'document': False,
                                            'modified': by_name['notes.txt']['modified']}
            # An office document is marked as one; without a document server it cannot be opened.
            assert by_name['plan.docx']['document'] and not by_name['plan.docx']['opens']
            assert not by_name['tool.exe']['opens'] and listing['upload_limit'] == 1024 * 1024 and listing['office'] is False
            assert [e['name'] for e in (await (await get('/files/list', path='Trip')).json())['entries']] == ['dot.png']
            # Only the owner, only inside the tree, never through a link.
            await get('/files/list', 403, headers={'Cookie': m.single.COOKIE + '=someone-else'})
            await get('/files/list', 403, headers={'Sec-Fetch-Site': 'cross-site'})
            await get('/files/content', 403, headers={'Sec-Fetch-Site': 'same-site'}, path='notes.txt')
            for path in ('..', 'Trip/../..', '.hidden', 'a\\b'):
                await get('/files/list', 400, path=path)
                await get('/files/content', 400, path=path)
            for path in ('away', 'missing', 'notes.txt'):
                await get('/files/list', 404, path=path)
            for path in ('link.txt', 'away/passwd', 'missing.txt', 'Trip', ''):
                await get('/files/content', 404, path=path)

            # Content: shown inline only as an inert document, with ranges for seeking.
            response = await get('/files/content', path='notes.txt')
            assert await response.read() == b'0123456789' and response.headers['Content-Type'] == 'text/plain; charset=utf-8'
            assert response.headers['Content-Security-Policy'].startswith('sandbox') and response.headers['Accept-Ranges'] == 'bytes'
            assert response.headers['X-Content-Type-Options'] == 'nosniff' and response.headers['Content-Disposition'].startswith('inline')
            etag = response.headers['ETag']
            assert (await get('/files/content', 304, headers={'If-None-Match': etag}, path='notes.txt')).headers['Cache-Control'] == 'private, no-cache'
            for header, body, span in (('bytes=2-5', b'2345', 'bytes 2-5/10'), ('bytes=7-', b'789', 'bytes 7-9/10'),
                                       ('bytes=-3', b'789', 'bytes 7-9/10'), ('bytes=4-99', b'456789', 'bytes 4-9/10')):
                response = await get('/files/content', 206, headers={'Range': header}, path='notes.txt')
                assert await response.read() == body and response.headers['Content-Range'] == span, header
            assert (await get('/files/content', 416, headers={'Range': 'bytes=10-'}, path='notes.txt')).headers['Content-Range'] == 'bytes */10'
            # A page the user downloaded is text here, never a page of this site.
            response = await get('/files/content', path='page.html')
            assert response.headers['Content-Type'] == 'text/plain; charset=utf-8' and 'sandbox' in response.headers['Content-Security-Policy']
            response = await get('/files/content', path='paper.pdf')
            assert response.headers['Content-Type'] == 'application/pdf' and 'sandbox' not in response.headers['Content-Security-Policy']
            for path, query in (('tool.exe', {}), ('notes.txt', {'download': '1'}), ('paper.pdf', {'download': '1'})):
                response = await get('/files/content', path=path, **query)
                assert response.headers['Content-Type'] == 'application/octet-stream', path
                assert response.headers['Content-Disposition'] == f"attachment; filename*=UTF-8''{path}"
                assert 'sandbox' in response.headers['Content-Security-Policy']

            # Thumbnails are drawn by the worker, kept, and revalidated cheaply.
            response = await get('/files/thumb', path='Trip/dot.png', size='128')
            picture = await response.read()
            assert picture.startswith(b'\x89PNG') and response.headers['Content-Type'] == 'image/png'
            assert response.headers['Cache-Control'] == 'private, no-cache'
            worker = broker.thumbnailers[session]
            first = worker.sequence
            assert await (await get('/files/thumb', path='Trip/dot.png', size='128')).read() == picture and worker.sequence == first
            await get('/files/thumb', 304, headers={'If-None-Match': response.headers['ETag']}, path='Trip/dot.png', size='128')
            assert (await get('/files/thumb', path='Trip/dot.png', size='128', v='1')).headers['Cache-Control'].endswith('immutable')
            await get('/files/thumb', 404, path='notes.txt')
            await get('/files/thumb', 404, path='link.txt')
            await get('/files/thumb', 400, path='Trip/dot.png', size='big')
            # A file that is not what its name says costs one missing picture, and the worker carries on.
            (files / 'broken.png').write_bytes(b'not a picture')
            await get('/files/thumb', 404, path='broken.png')
            assert (await get('/files/thumb', path='Trip/dot.png', size='64')).status == 200

            # Upload in pieces: hidden until complete, resumable, never replacing.
            async def upload(data, offset, total, expect=200, name='video.bin', folder='Trip', ident='abcd1234', headers=None):
                response = await client.post('/files/upload', params={'path': folder, 'name': name}, data=data, headers={
                    **(headers or change), 'X-Upload-Id': ident, 'X-Upload-Offset': str(offset), 'X-Upload-Total': str(total)})
                assert response.status == expect, (response.status, await response.text())
                return await response.json() if expect in (200, 409) else None
            assert await upload(b'aaaa', 0, 10) == {'done': False, 'offset': 4}
            assert not (files / 'Trip' / 'video.bin').exists()
            assert [e['name'] for e in (await (await get('/files/list', path='Trip')).json())['entries']] == ['dot.png']
            # A piece from the wrong position is answered with where to continue.
            assert await upload(b'cc', 8, 10, 409) == {'done': False, 'offset': 4}
            assert await upload(b'bbbbbb', 4, 10) == {'done': True, 'name': 'video.bin'}
            assert (files / 'Trip' / 'video.bin').read_bytes() == b'aaaabbbbbb'
            assert not list((files / 'Trip').glob('.upload-*'))
            assert await upload(b'second', 0, 6) == {'done': True, 'name': 'video (1).bin'}
            assert (files / 'Trip' / 'video.bin').read_bytes() == b'aaaabbbbbb'
            assert await upload(b'', 0, 0, name='empty.txt') == {'done': True, 'name': 'empty.txt'}
            # Restarting from zero discards the earlier attempt.
            assert await upload(b'xx', 0, 4, ident='restart99') == {'done': False, 'offset': 2}
            assert await upload(b'yyyy', 0, 4, ident='restart99', name='again.bin') == {'done': True, 'name': 'again.bin'}
            assert (files / 'Trip' / 'again.bin').read_bytes() == b'yyyy'
            # Limits and ownership.
            await upload(b'x', 0, 1024 * 1024 + 1, 413)
            await upload(b'toolong', 0, 3, 413, ident='overrun01')
            await upload(b'x', 0, 1, 400, name='.profile')
            await upload(b'x', 0, 1, 400, name='a/b')
            await upload(b'x', 0, 1, 400, ident='../../x')
            await upload(b'x', 0, 1, 404, folder='missing')
            await upload(b'x', 0, 1, 400, folder='away')
            await upload(b'x', 5, 9, 404, ident='neverbegun')
            await upload(b'x', 0, 1, 403, headers={**host, 'Origin': ORIGIN, 'X-CSRF-Token': 'wrong'})
            await upload(b'x', 0, 1, 403, headers={**host, 'Origin': 'https://evil.test', 'X-CSRF-Token': 'csrf-token'})
            await upload(b'x', 0, 1, 403, headers={**change, 'Cookie': m.single.COOKIE + '=someone-else'})
            assert not Path('/etc/x').exists() and not list(files.rglob('.upload-overrun01*'))
            # An abandoned fragment is cleared when the next upload begins there.
            stale = files / 'Trip' / '.upload-stale0001.part'
            stale.write_bytes(b'old')
            os.utime(stale, (time.time() - 2 * 86400,) * 2)
            await upload(b'z', 0, 1, name='z.bin')
            assert not stale.exists()

            # Actions: new folder, rename, move, copy, delete, open.
            await act(action='mkdir', path='Trip/Day one')
            assert (files / 'Trip' / 'Day one').is_dir()
            await act(409, action='mkdir', path='Trip/Day one')
            await act(action='rename', path='notes.txt', name='todo.txt')
            await act(409, action='rename', path='todo.txt', name='Trip')
            for name in ('../out.txt', '.hidden', '', 5):
                await act(400, action='rename', path='todo.txt', name=name)
            await act(action='move', path='todo.txt', to='Trip/Day one')
            assert (files / 'Trip' / 'Day one' / 'todo.txt').read_bytes() == b'0123456789' and not (files / 'todo.txt').exists()
            (files / 'todo.txt').write_bytes(b'other')
            await act(409, action='move', path='todo.txt', to='Trip/Day one')
            assert (files / 'todo.txt').read_bytes() == b'other'
            await act(400, action='move', path='Trip', to='Trip/Day one')
            await act(400, action='move', path='Trip', to='Trip')
            await act(404, action='move', path='missing.txt', to='Trip')
            await act(400, action='move', path='todo.txt', to='away')
            await act(400, action='move', path='link.txt', to='Trip')
            # A copy takes a free name, carries everything in a folder, and leaves links behind.
            assert (await act(action='copy', path='todo.txt', to=''))['name'] == 'todo (1).txt'
            (files / 'Trip' / 'Day one' / 'out').symlink_to('/etc')
            assert (await act(action='copy', path='Trip', to=''))['name'] == 'Trip (1)'
            copied = files / 'Trip (1)'
            assert (copied / 'Day one' / 'todo.txt').read_bytes() == b'0123456789' and (copied / 'dot.png').read_bytes() == PNG
            assert not (copied / 'Day one' / 'out').exists() and not (copied / 'Day one' / 'out').is_symlink()
            await act(400, action='copy', path='Trip', to='Trip/Day one')
            await act(action='delete', path='Trip (1)')
            await act(action='delete', path='Trip/Day one')
            assert not copied.exists() and not (files / 'Trip' / 'Day one').exists() and Path('/etc/passwd').exists()
            await act(404, action='delete', path='Trip/Day one')
            await act(400, action='delete', path='')
            await act(400, action='format', path='Trip')
            await act(400, action='move', path='todo.txt')
            await act(action='open', path='paper.pdf')
            session.browser.open_url.assert_awaited_once_with((files / 'paper.pdf').as_uri())
            await act(415, action='open', path='tool.exe')
            # Every change needs the owner, this origin and the page's token.
            for headers in ({**change, 'Cookie': m.single.COOKIE + '=someone-else'}, {**host, 'Origin': ORIGIN},
                            {**host, 'Origin': ORIGIN, 'X-CSRF-Token': 'wrong'},
                            {**host, 'Origin': 'https://evil.test', 'X-CSRF-Token': 'csrf-token'}, {**host, 'X-CSRF-Token': 'csrf-token'}):
                await act(403, headers=headers, action='delete', path='todo.txt')
            assert (files / 'todo.txt').exists()
            # A session that is not running serves nothing; ending one stops its worker.
            process = worker.process
            broker.files_closed(session)
            await asyncio.wait_for(process.wait(), 5)
            assert session not in broker.thumbnailers and not broker.thumbnails and broker.thumbnail_bytes == 0
            session.state = 'STARTING'
            await get('/files/list', 403)
            await act(403, action='delete', path='todo.txt')
        finally:
            await client.close()
    print('My files API tests passed')


asyncio.run(main())
