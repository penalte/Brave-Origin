"""OIDC broker for independently supervised per-identity desktop stacks."""
import asyncio
import contextlib
import errno
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import stat
import time

from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

spec = importlib.util.spec_from_file_location('single', '/usr/local/bin/session-manager.py')
single = importlib.util.module_from_spec(spec)
spec.loader.exec_module(single)
LOG = logging.getLogger('multi-session')

# Never inherit the broker environment: it contains the OIDC client secret.
# Socket paths, authentication, sharing and command execution stay broker-owned.
SESSION_ENV = frozenset('''ENABLE_GPU ENABLE_AUDIO DRI_NODE DRINODE AUTO_GPU
LIBVA_DRIVER_NAME BRAVE_FLAGS TZ LANG LC_ALL XKB_DEFAULT_LAYOUT
XKB_DEFAULT_VARIANT XKB_DEFAULT_OPTIONS DISPLAY_WIDTH DISPLAY_HEIGHT
DISPLAY_AUTO_RESIZE BROWSER_LOCK_MAXIMIZED SELKIES_FRAMERATE
SELKIES_VIDEO_BITRATE SELKIES_VIDEO_CRF SELKIES_AUDIO_BITRATE
SELKIES_SCALING_DPI SELKIES_USE_BROWSER_CURSORS SELKIES_USE_CSS_SCALING
SELKIES_ENABLE_CLIPBOARD SELKIES_ENABLE_BINARY_CLIPBOARD
SELKIES_MICROPHONE_ENABLED'''.split())


def session_environment(home, name, runtime, source=None):
    source = os.environ if source is None else source
    env = {'HOME': str(home), 'USER': name, 'LOGNAME': name,
           'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'XDG_RUNTIME_DIR': str(runtime)}
    env.update((key, source[key]) for key in SESSION_ENV if key in source)
    return env


def retain_failure_logs(directory, root=Path('/config/session-failures')):
    """Keep at most five archives, each with five 64-KiB log tails."""
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != 0:
        raise RuntimeError('Failure archive must be a root-owned directory')
    root.chmod(0o700)
    archive = root / (str(time.time_ns()) + '-' + secrets.token_hex(4))
    archive.mkdir(mode=0o700)
    for name in ('supervisor.log', 'browser.log', 'selkies.log', 'labwc.log', 'picker.log'):
        try:
            fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        except OSError as error:
            if error.errno != errno.ELOOP:
                raise
            LOG.warning('Refusing symlink diagnostic log: %s', name)
            continue
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                continue
            os.lseek(fd, max(0, os.fstat(fd).st_size - 65536), os.SEEK_SET)
            with (archive / name).open('xb') as output:
                os.chmod(output.name, 0o600)
                output.write(os.read(fd, 65536))
        finally:
            os.close(fd)
    archives = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    for old in archives[:-5]:
        shutil.rmtree(old)
    LOG.error('Private desktop failure logs retained in %s', archive)


class Desktop(single.Browser):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.directory = None
        self.failed = False

    async def clear_shared(self):
        # All clipboard, audio and compositor state belongs to this UID.
        pass

    async def start_locked(self, issuer, subject):
        name = await self.prepare(issuer, subject)
        await self.clear_profile_locks(name)
        installed = await self.command('dpkg-query', '-W', '-f=${Version}', 'brave-origin')
        saved = json.loads(self.metadata.read_text())
        if saved.get('version'):
            process = await asyncio.create_subprocess_exec('dpkg', '--compare-versions', installed, 'lt', saved['version'])
            if await process.wait() == 0:
                raise RuntimeError('Installed browser is older than this profile')
        saved['version'] = installed
        self.metadata.write_text(json.dumps(saved) + '\n')
        self.directory = Path('/run/brave-origin/sessions') / secrets.token_hex(12)
        self.directory.mkdir(mode=0o700)
        os.chown(self.directory, self.uid, self.uid)
        for device in self.gpu_devices():
            gid = device.stat().st_gid
            if gid:
                await self.command('usermod', '-aG', str(gid), name)
        env = session_environment(self.home, name, self.directory)
        with (self.directory / 'supervisor.log').open('ab') as log:
            self.process = await asyncio.create_subprocess_exec('/usr/sbin/runuser', '-u', name,
                '--', 'dbus-run-session', '--', '/usr/local/bin/user-desktop.sh',
                env=env, stdout=log, stderr=log, start_new_session=True)
        for _ in range(self.config.launch_timeout * 10):
            if (self.directory / 'ready').exists() and (self.directory / 'stream.sock').is_socket() and self.running():
                return
            if self.process.returncode is not None:
                break
            await asyncio.sleep(0.1)
        raise RuntimeError('Private desktop did not become ready')

    async def stop(self):
        await super().stop()
        if self.directory:
            if self.failed:
                try:
                    retain_failure_logs(self.directory)
                except Exception:
                    LOG.exception('Could not retain private desktop failure logs')
            # Exact broker-generated path, only after the UID has no processes.
            shutil.rmtree(self.directory)
            self.directory = None


class Broker(single.Manager):
    def __init__(self, config, desktop_factory=None, oidc=None):
        super().__init__(config, browser=Desktop(config), oidc=oidc)
        self.sessions = {}
        self.factory = desktop_factory or (lambda: Desktop(config))

    def selected(self, request):
        for session in self.sessions.values():
            if session.owns(request):
                return session
        return None

    def owns(self, request):
        return self.selected(request) is not None

    async def health(self, request):
        failed = sum(s.state == 'ERROR' or
                     (s.state == 'RUNNING' and not s.browser.running())
                     for s in self.sessions.values())
        return web.json_response({'state': self.state, 'sessions': len(self.sessions),
                                  'failed_sessions': failed},
                                 status=503 if self.state == 'ERROR' or failed else 200)

    async def status(self, request):
        session = self.selected(request)
        if session:
            return await session.status(request)
        available = self.state == 'IDLE' and len(self.sessions) < self.config.maximum
        return web.json_response({'state': 'IDLE' if available else 'BUSY', 'owner': False,
                                  'name': '', 'csrf': ''})

    async def login(self, request):
        if not self.owns(request) and len(self.sessions) >= self.config.maximum:
            raise web.HTTPConflict(text='All desktop slots are occupied')
        return await super().login(request)

    async def callback(self, request):
        flow = self.flows.pop(request.query.get('state', ''), None)
        if (not flow or flow['expires'] < time.time() or flow['origin'] != request['app_origin']
                or not secrets.compare_digest(request.cookies.get(single.FLOW_COOKIE, ''), flow['cookie'])
                or not request.query.get('code') or request.query.get('error')):
            raise web.HTTPBadRequest(text='Invalid or expired login')
        try:
            claims = await self.oidc.authenticate(self.http, request.query['code'], flow)
        except Exception:
            raise web.HTTPForbidden(text='Sign-in could not be verified') from None
        identity = hashlib.sha256(json.dumps([claims['iss'], claims['sub']], separators=(',', ':')).encode()).hexdigest()
        async with self.lock:
            if identity in self.sessions:
                raise web.HTTPConflict(text='Your desktop is already open. Reconnect from its original tab or end that session first.')
            if self.state != 'IDLE' or len(self.sessions) >= self.config.maximum:
                raise web.HTTPConflict(text='No desktop slot is currently available')
            session = single.Manager(self.config, browser=self.factory())
            session.state = 'STARTING'
            session.owner = {'cookie': secrets.token_urlsafe(32), 'csrf': secrets.token_urlsafe(32),
                'origin': flow['origin'], 'name': str(claims.get('name') or 'Private browser')[:120],
                'expires': min(time.time() + self.config.ttl, float(claims['exp']))}
            self.sessions[identity] = session
        async with session.lock:
            try:
                await session.browser.start(claims['iss'], claims['sub'])
                session.http = ClientSession(connector=UnixConnector(path=str(session.browser.directory / 'stream.sock')),
                                            timeout=ClientTimeout(total=None, connect=10, sock_read=30))
                session.state = 'RUNNING'
                session.last_disconnect = time.monotonic()
            except Exception:
                session.browser.failed = True
                LOG.exception('Private desktop launch failed for %s', identity[:12])
                await self.end(identity, session)
                raise web.HTTPServiceUnavailable(text='Your desktop could not start') from None
            response = web.HTTPFound('/')
            response.set_cookie(single.COOKIE, session.owner['cookie'], secure=True, httponly=True,
                                samesite='Lax', path='/', max_age=max(1, int(session.owner['expires'] - time.time())))
            response.del_cookie(single.FLOW_COOKIE, path='/', secure=True, httponly=True, samesite='Lax')
            return response

    async def end(self, identity, session):
        await session.stop_locked()
        if session.http:
            await session.http.close()
        if session.state == 'IDLE':
            self.sessions.pop(identity, None)

    async def logout(self, request):
        session = self.selected(request)
        if not session:
            raise web.HTTPForbidden()
        response = await session.logout(request)
        if session.state == 'IDLE':
            for key, candidate in list(self.sessions.items()):
                if candidate is session:
                    self.sessions.pop(key)
            await session.http.close()
        return response

    async def proxy(self, request):
        session = self.selected(request)
        if not session:
            raise web.HTTPForbidden(text='No active desktop')
        return await session.proxy(request)

    async def monitor(self):
        while True:
            await asyncio.sleep(1)
            for identity, session in list(self.sessions.items()):
                if session.lock.locked():
                    continue
                async with session.lock:
                    if session.state == 'RUNNING':
                        if not session.browser.running():
                            session.browser.failed = True
                        grace = self.config.grace if session.connected_once else self.config.start_timeout
                        if (time.time() >= session.owner['expires'] or not session.browser.running()
                                or not session.connections and time.monotonic() - session.last_disconnect >= grace):
                            await self.end(identity, session)
                    elif session.state == 'ERROR' and time.monotonic() - session.last_recovery >= 30:
                        await self.end(identity, session)
            update = False
            async with self.lock:
                if not self.sessions:
                    if self.state == 'ERROR' and time.monotonic() - self.last_recovery >= 30:
                        await self.retry_locked()
                    elif self.state == 'IDLE' and self.config.auto_update and time.monotonic() - self.last_update >= self.config.update_interval:
                        self.state = 'UPDATING'
                        update = True
            if update:
                await self.update()

    async def lifecycle(self, app):
        root = Path('/run/brave-origin/sessions')
        root.mkdir(mode=0o711, exist_ok=True)
        root.chmod(0o711)
        await self.browser.recover()
        for directory in root.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory)
        # Chromium policy expands ${user_home} separately in each process.
        Path('/etc/brave/policies/managed/policies.json').write_text(
            json.dumps(single.policies('${user_home}/Downloads')))
        self.http = ClientSession(timeout=ClientTimeout(total=None, connect=10, sock_read=30))
        task = asyncio.create_task(self.monitor())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            for key, session in list(self.sessions.items()):
                async with session.lock:
                    await self.end(key, session)
            await self.http.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    config = single.Config()
    config.maximum = single.integer(os.environ, 'MAX_CONCURRENT_SESSIONS', 2, 1, 32)
    config.launch_timeout = single.integer(os.environ, 'SESSION_START_TIMEOUT', 90, 10, 300)
    web.run_app(Broker(config).app(), host='127.0.0.1', port=8084, access_log=None, shutdown_timeout=60)
