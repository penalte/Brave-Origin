"""My files API: the owner's files, served by the broker to the portal's Files page.

Every route answers only the session that owns the files. The broker is root and
the tree belongs to the user, so each path is walked without following links,
and anything that decodes a file (thumbnails) runs as the user, not here.
"""
import asyncio
from collections import OrderedDict
import contextlib
import errno
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import struct
import time
from urllib.parse import quote

from aiohttp import web

LOG = logging.getLogger('my-files')
picker = None  # The file-picker module: one definition of names, kinds and traversal. Set by the broker.

# Types a browser shows from a URL. Everything else is sent as a download.
MEDIA = {suffix: kind for kind, suffixes in {
    'image/jpeg': 'jpg jpeg jfif', 'image/png': 'png', 'image/apng': 'apng', 'image/gif': 'gif',
    'image/bmp': 'bmp', 'image/x-icon': 'ico', 'image/webp': 'webp', 'image/avif': 'avif',
    'image/svg+xml': 'svg', 'image/tiff': 'tif tiff',
    'video/mp4': 'mp4 m4v', 'video/webm': 'webm', 'video/ogg': 'ogv', 'video/x-matroska': 'mkv',
    'video/quicktime': 'mov', 'video/x-msvideo': 'avi', 'video/x-ms-wmv': 'wmv', 'video/x-flv': 'flv',
    'video/mpeg': 'mpg mpeg', 'video/3gpp': '3gp', 'video/mp2t': 'ts',
    'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/ogg': 'ogg oga opus', 'audio/flac': 'flac',
    'audio/mp4': 'm4a', 'audio/aac': 'aac', 'audio/webm': 'weba', 'audio/x-ms-wma': 'wma',
    'application/pdf': 'pdf'}.items() for suffix in suffixes.split()}
THUMBNAILS = ('image', 'video', 'audio', 'pdf')
THUMBNAIL_MEMORY = 32 * 1024 * 1024
STALE_UPLOAD_SECONDS = 86400
# A user's own file must never act as a page of this site.
INERT = "sandbox; default-src 'none'; style-src 'unsafe-inline'"


def content_type(name):
    suffix = Path(name).suffix.lower().lstrip('.')
    return MEDIA.get(suffix) or ('text/plain; charset=utf-8' if picker.kind(name) == 'text' else None)


def split(path):
    """A path inside My files as its names; the empty path is My files itself."""
    parts = [part for part in path.split('/') if part]
    if len(parts) > 64 or not all(picker.valid(part) for part in parts):
        raise web.HTTPBadRequest(text='That path is not in My files.')
    return parts


def byte_range(header, size):
    """(first, last) for a Range header, None when there is none, or 416."""
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not (match[1] or match[2]):
        return None
    if match[1]:
        first, last = int(match[1]), min(int(match[2]) if match[2] else size - 1, size - 1)
    else:
        first, last = max(0, size - int(match[2])), size - 1
    if first > last or first >= size:
        raise web.HTTPRequestRangeNotSatisfiable(headers={'Content-Range': f'bytes */{size}'})
    return first, last


class Thumbnailer:
    """Small pictures of one user's files, drawn by a worker running as that user."""
    def __init__(self, command, env):
        self.command, self.env = command, env
        self.lock = asyncio.Lock()
        self.process = None
        self.sequence = 0

    async def draw(self, parts, shape, size):
        async with self.lock:
            try:
                if self.process is None or self.process.returncode is not None:
                    self.process = await asyncio.create_subprocess_exec(
                        *self.command, env=self.env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                self.sequence += 1
                self.process.stdin.write(json.dumps({'id': self.sequence, 'op': 'render', 'parts': parts, 'kind': shape,
                                                     'width': size, 'height': size}).encode() + b'\n')
                async with asyncio.timeout(20):
                    await self.process.stdin.drain()
                    ident, length = struct.unpack('!II', await self.process.stdout.readexactly(8))
                    if ident != self.sequence or length > 16 * 1024 * 1024:
                        raise ValueError('Unexpected thumbnail reply')
                    return await self.process.stdout.readexactly(length)
            except (TimeoutError, asyncio.IncompleteReadError, OSError, ValueError):
                # A decoder that hangs or dies costs this one picture.
                self.close()
                return b''

    def close(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)


class FileService:
    """Broker routes for My files. Expects selected(), my_files(), open_parts(), thumbnailer() and config."""
    def files_routes(self):
        self.thumbnailers, self.thumbnails, self.thumbnail_bytes = {}, OrderedDict(), 0
        return [web.get('/files/list', self.files_list), web.get('/files/content', self.files_content),
                web.get('/files/thumb', self.files_thumb), web.post('/files/upload', self.files_upload),
                web.post('/files/action', self.files_action)]

    def files_owner(self, request, change=False):
        session = self.selected(request)
        if not session or session.state != 'RUNNING':
            raise web.HTTPForbidden(text='My files needs your running session.')
        if change:
            # A change needs this site's own page and the token only that page holds.
            if request.headers.get('Origin') != request['app_origin'] or not secrets.compare_digest(
                    request.headers.get('X-CSRF-Token', ''), session.owner['csrf']):
                raise web.HTTPForbidden(text='Changing My files requires the session that owns them.')
        elif request.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
            # Another site may not embed or fetch a user's file through their cookie.
            raise web.HTTPForbidden(text='My files are not available to other sites.')
        return session

    @contextlib.contextmanager
    def files_of(self, session):
        files = self.my_files(session)
        try:
            yield files
        finally:
            os.close(files.fd)

    def files_regular(self, session, parts):
        """An open descriptor and the facts of one regular file."""
        try:
            if not parts:
                raise ValueError('Choose a file')
            with self.files_of(session) as files:
                fd = files.open(parts)
        except (OSError, ValueError):
            raise web.HTTPNotFound(text='That file is no longer in My files.') from None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise web.HTTPNotFound(text='That file is no longer in My files.')
        return fd, info

    async def files_list(self, request):
        session = self.files_owner(request)
        parts = split(request.query.get('path', ''))

        def read():
            with self.files_of(session) as files:
                return files.entries(parts)
        try:
            entries = await asyncio.get_running_loop().run_in_executor(None, read)
        except (OSError, ValueError):
            raise web.HTTPNotFound(text='That folder is no longer in My files.') from None
        office = bool(self.config.office_url)
        return web.json_response({
            'path': parts, 'office': office, 'upload_limit': self.config.upload_limit,
            'entries': [{'name': name, 'folder': folder, 'size': 0 if folder else size, 'modified': int(modified),
                         'kind': 'folder' if folder else picker.kind(name),
                         'opens': not folder and picker.openable(name, office) is None}
                        for name, folder, size, modified in entries]})

    async def files_content(self, request):
        session = self.files_owner(request)
        parts = split(request.query.get('path', ''))
        fd, info = self.files_regular(session, parts)
        try:
            name, size = parts[-1], info.st_size
            kind = content_type(name)
            download = 'download' in request.query or kind is None
            etag = f'"{info.st_mtime_ns:x}-{size:x}"'
            headers = {'Accept-Ranges': 'bytes', 'ETag': etag, 'Cache-Control': 'private, no-cache',
                       'Content-Type': 'application/octet-stream' if download else kind,
                       'Content-Disposition': ('attachment' if download else 'inline') + "; filename*=UTF-8''" + quote(name),
                       'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                       'Cross-Origin-Resource-Policy': 'same-origin',
                       # The browser's PDF viewer does not run inside a sandbox; a PDF cannot script this site.
                       'Content-Security-Policy': "frame-ancestors 'self'" if kind == 'application/pdf' and not download else INERT}
            span = byte_range(request.headers.get('Range', ''), size)
            if span is None and request.headers.get('If-None-Match') == etag:
                request['own_headers'] = {'Cache-Control': headers['Cache-Control']}
                return web.Response(status=304, headers={'ETag': etag})
            first, last = span or (0, size - 1)
            if span:
                headers['Content-Range'] = f'bytes {first}-{last}/{size}'
            headers['Content-Length'] = str(max(0, last - first + 1))
            response = web.StreamResponse(status=206 if span else 200, headers=headers)
            await response.prepare(request)
            loop = asyncio.get_running_loop()
            position = first
            while request.method != 'HEAD' and position <= last:
                chunk = await loop.run_in_executor(None, os.pread, fd, min(last - position + 1, 1 << 18), position)
                if not chunk:
                    break
                position += len(chunk)
                await response.write(chunk)
            return response
        finally:
            os.close(fd)

    async def files_thumb(self, request):
        session = self.files_owner(request)
        parts = split(request.query.get('path', ''))
        try:
            size = min(512, max(64, int(request.query.get('size', '256'))))
        except ValueError:
            raise web.HTTPBadRequest(text='Invalid thumbnail size') from None
        fd, info = self.files_regular(session, parts)
        os.close(fd)
        shape = picker.kind(parts[-1])
        if shape not in THUMBNAILS or shape in ('image', 'pdf') and info.st_size > 64 * 1024 * 1024:
            raise web.HTTPNotFound(text='No thumbnail for this file.')
        etag = f'"{info.st_mtime_ns:x}-{info.st_size:x}-{size}"'
        # A page that names the file's version in the address may keep the picture.
        request['own_headers'] = {'Cache-Control': 'private, max-age=604800, immutable' if 'v' in request.query
                                  else 'private, no-cache'}
        if request.headers.get('If-None-Match') == etag:
            return web.Response(status=304, headers={'ETag': etag})
        key = (id(session), tuple(parts), info.st_mtime_ns, info.st_size, size)
        picture = self.thumbnails.get(key)
        if picture is None:
            worker = self.thumbnailers.get(session)
            if worker is None:
                worker = self.thumbnailers[session] = self.thumbnailer(session)
            picture = await worker.draw(parts, shape, size)
            # A file with no picture is remembered too, so it is not decoded again and again.
            self.thumbnails[key] = picture
            self.thumbnail_bytes += len(picture) + 256
            while self.thumbnail_bytes > THUMBNAIL_MEMORY and len(self.thumbnails) > 1:
                self.thumbnail_bytes -= len(self.thumbnails.popitem(last=False)[1]) + 256
        else:
            self.thumbnails.move_to_end(key)
        if not picture:
            raise web.HTTPNotFound(text='No thumbnail for this file.')
        return web.Response(body=picture, content_type='image/png',
                            headers={'ETag': etag, 'Cross-Origin-Resource-Policy': 'same-origin'})

    def files_closed(self, session):
        """Forget a session that ended: its worker and its pictures."""
        worker = self.thumbnailers.pop(session, None)
        if worker:
            worker.close()
        for key in [key for key in self.thumbnails if key[0] == id(session)]:
            self.thumbnail_bytes -= len(self.thumbnails.pop(key)) + 256

    async def files_upload(self, request):
        """Receive one piece of a file. Pieces arrive in order; the last one completes it.

        The pieces collect in a hidden fragment beside the destination, so a
        half-sent file never shows in My files and an interrupted upload resumes.
        """
        session = self.files_owner(request, change=True)
        parts = split(request.query.get('path', ''))
        name = request.query.get('name', '')
        ident = request.headers.get('X-Upload-Id', '')
        if not picker.valid(name) or not re.fullmatch(r'[A-Za-z0-9]{8,40}', ident):
            raise web.HTTPBadRequest(text='Use a plain file name without / or a leading dot.')
        try:
            offset, total = int(request.headers.get('X-Upload-Offset', '0')), int(request.headers['X-Upload-Total'])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest(text='Invalid upload size') from None
        limit = self.config.upload_limit
        if total < 0 or offset < 0 or offset > total:
            raise web.HTTPBadRequest(text='Invalid upload size')
        if total > limit or offset + (request.content_length or 0) > total:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=total)
        owner, loop, fragment = session.browser.uid, asyncio.get_running_loop(), f'.upload-{ident}.part'

        def begin():
            with self.files_of(session) as files:
                folder = files.open(parts, directory=True)
            try:
                if offset == 0:
                    now = time.time()
                    for entry in os.listdir(folder):
                        if re.fullmatch(r'\.upload-[A-Za-z0-9]{8,40}\.part', entry):
                            with contextlib.suppress(OSError):
                                if entry == fragment or now - os.stat(entry, dir_fd=folder, follow_symlinks=False).st_mtime > STALE_UPLOAD_SECONDS:
                                    os.unlink(entry, dir_fd=folder)
                    fd = os.open(fragment, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
                    os.fchown(fd, owner, owner)
                else:
                    fd = os.open(fragment, os.O_WRONLY | os.O_NOFOLLOW | os.O_APPEND, dir_fd=folder)
                return folder, fd
            except BaseException:
                os.close(folder)
                raise

        def write(fd, data):
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]

        def finish(folder):
            final = picker.Files.free_name(folder, name)
            # A link fails if the name appeared meanwhile; a rename would replace it.
            os.link(fragment, final, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
            os.unlink(fragment, dir_fd=folder)
            return final

        try:
            folder, fd = await loop.run_in_executor(None, begin)
        except FileNotFoundError:
            raise web.HTTPNotFound(text='That upload or its folder is no longer in My files.') from None
        except (OSError, ValueError):
            raise web.HTTPBadRequest(text='That folder is not in My files.') from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != offset:
                # The page continues from what has actually been stored.
                return web.json_response({'done': False, 'offset': info.st_size if stat.S_ISREG(info.st_mode) else 0}, status=409)
            stored = offset
            try:
                async for chunk in request.content.iter_chunked(1 << 18):
                    stored += len(chunk)
                    if stored > total:
                        raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=stored)
                    await loop.run_in_executor(None, write, fd, chunk)
            except OSError as error:
                if error.errno in (errno.ENOSPC, errno.EDQUOT):
                    with contextlib.suppress(OSError):
                        os.unlink(fragment, dir_fd=folder)
                    raise web.HTTPInsufficientStorage(text='There is not enough storage left for this file.') from None
                raise
            if stored < total:
                return web.json_response({'done': False, 'offset': stored})
            return web.json_response({'done': True, 'name': await loop.run_in_executor(None, finish, folder)})
        finally:
            os.close(fd)
            os.close(folder)

    async def files_action(self, request):
        """Create a folder, rename, move, copy or delete an entry, or open a file in the remote browser."""
        session = self.files_owner(request, change=True)
        try:
            body = await request.json()
            action, parts = body['action'], split(body['path'])
            target = split(body['to']) if action in ('move', 'copy') else None
            name = body['name'] if action == 'rename' else None
            if not isinstance(action, str) or name is not None and not isinstance(name, str):
                raise ValueError('Invalid request')
        except web.HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise web.HTTPBadRequest(text='Invalid My files request') from None
        if action == 'open':
            kind = await self.open_parts(session, parts)
            return web.json_response({'opened': True, 'viewer': 'office' if kind else 'browser'})
        if action not in ('mkdir', 'rename', 'move', 'copy', 'delete'):
            raise web.HTTPBadRequest(text='Unknown My files action')
        owner = session.browser.uid

        def change():
            with self.files_of(session) as files:
                if action == 'mkdir':
                    # Created by this root process; hand it to the account that owns the tree.
                    return files.mkdir(parts, owner=owner)
                if action == 'rename':
                    return files.rename(parts, name)
                if action == 'move':
                    return files.move(parts, target)
                if action == 'copy':
                    return files.copy(parts, target, owner=owner)
                return files.remove(parts)
        try:
            created = await asyncio.get_running_loop().run_in_executor(None, change)
        except FileExistsError:
            raise web.HTTPConflict(text='Something with that name already exists there.') from None
        except FileNotFoundError:
            raise web.HTTPNotFound(text='That item is no longer in My files.') from None
        except OSError as error:
            if error.errno in (errno.ENOSPC, errno.EDQUOT):
                raise web.HTTPInsufficientStorage(text='There is not enough storage left for this.') from None
            raise web.HTTPBadRequest(text='Use a plain name without / or a leading dot, inside My files.') from None
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error) or 'That is not possible in My files.') from None
        return web.json_response({'changed': True, 'name': created})
