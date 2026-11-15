"""OIDC broker for independently supervised per-identity desktop stacks."""
import asyncio
import contextlib
import errno
import hashlib
import importlib.util
import json
import logging
import html
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import time
from urllib.parse import unquote

from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

spec = importlib.util.spec_from_file_location('single', '/usr/local/bin/session-manager.py')
single = importlib.util.module_from_spec(spec)
spec.loader.exec_module(single)
sharing_spec = importlib.util.spec_from_file_location('view_sharing', '/usr/local/bin/view-sharing.py')
sharing = importlib.util.module_from_spec(sharing_spec)
sharing_spec.loader.exec_module(sharing)
picker_spec = importlib.util.spec_from_file_location('file_picker', '/usr/local/bin/file-picker.py')
picker = importlib.util.module_from_spec(picker_spec)
picker_spec.loader.exec_module(picker)
files_spec = importlib.util.spec_from_file_location('my_files', '/usr/local/bin/my-files.py')
files_api = importlib.util.module_from_spec(files_spec)
files_spec.loader.exec_module(files_api)
files_api.picker = picker
LOG = logging.getLogger('multi-session')
network_spec = importlib.util.spec_from_file_location('user_network', '/usr/local/bin/user-network.py')
user_network = importlib.util.module_from_spec(network_spec)
network_spec.loader.exec_module(user_network)


# How long the take-over choice page stays valid.
HANDOFF_SECONDS = 600
# How long a verified sign-in may wait to launch, or retry launching, its desktop.
PREPARE_SECONDS = 600


# How long a document opened from My files stays readable by the document server.
OFFICE_SECONDS = 3600
# One definition, shared with the picker, of what the browser and the document server can show.
OFFICE_TYPES, BROWSER_TYPES = picker.OFFICE_TYPES, picker.BROWSER_TYPES


class TicketExpired(web.HTTPForbidden):
    """The sign-in behind a preparation request is gone or timed out."""


class ProfileTooNew(RuntimeError):
    """The installed browser is older than the one that last opened this profile."""


def admin_claim(claims, group_claim, env=os.environ):
    groups = claims.get(group_claim, [])
    return (isinstance(groups, list) and env.get('OIDC_ADMIN_GROUP', 'admin') in
            [group for group in groups if isinstance(group, str)])

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
        self.network = None
        self.display_name = 'Private browser'
        self.startup = None
        self.admin = False

    def report(self, phase=None, **steps):
        """Update private sign-in progress. Display only: nothing here gates a launch."""
        if self.startup is None:
            return
        if phase:
            self.startup['phase'] = phase
        for task in self.startup['tasks']:
            task.update(steps.get(task.get('id'), {}))

    async def clear_shared(self):
        # All clipboard, audio and compositor state belongs to this UID.
        pass

    async def start_locked(self, issuer, subject):
        self.report('profile', profile={'state': 'running'})
        name = await self.prepare(issuer, subject)
        # Open and check the profile before any connection work, so a profile
        # this browser must not open fails first and says why.
        await self.clear_profile_locks(name)
        installed = await self.command('dpkg-query', '-W', '-f=${Version}', 'brave-origin')
        saved = json.loads(self.metadata.read_text())
        if saved.get('version'):
            process = await asyncio.create_subprocess_exec('dpkg', '--compare-versions', installed, 'lt', saved['version'])
            if await process.wait() == 0:
                raise ProfileTooNew('Installed browser is older than this profile')
        saved['version'] = installed
        self.metadata.write_text(json.dumps(saved) + '\n')
        self.report('connection', profile={'state': 'ready'}, network={'state': 'running'})
        key = json.loads(self.metadata.read_text())['key']
        user_network.register(key, self.display_name)
        self.network = user_network.UserProxy(key, self.uid, self.admin)
        await self.network.start()
        self.report(network={'label': 'WARP' if self.network.manager.mode == 'warp' else 'Direct connection'})
        await self.network.wait_ready()
        self.report('desktop', network={'state': 'ready'}, desktop={'state': 'running'})
        await self.launch_desktop(name)

    async def launch_desktop(self, name):
        self.directory = Path('/run/brave-origin/sessions') / secrets.token_hex(12)
        self.directory.mkdir(mode=0o700)
        os.chown(self.directory, self.uid, self.uid)
        for device in self.gpu_devices():
            gid = device.stat().st_gid
            if gid:
                await self.command('usermod', '-aG', str(gid), name)
        env = session_environment(self.home, name, self.directory)
        env['BRAVE_PROXY_PORT'] = str(self.network.port)
        env['BRAVE_OFFICE_VIEWER'] = 'true' if self.config.office_url else 'false'
        self.master_token = secrets.token_urlsafe(32)
        self.owner_token = secrets.token_urlsafe(32)
        env['SELKIES_MASTER_TOKEN'] = self.master_token
        with (self.directory / 'supervisor.log').open('ab') as log:
            self.process = await asyncio.create_subprocess_exec('/usr/sbin/runuser', '-u', name,
                '--', 'dbus-run-session', '--', '/usr/local/bin/user-desktop.sh',
                env=env, stdout=log, stderr=log, start_new_session=True)
        for _ in range(self.config.launch_timeout * 10):
            # This account owns the session folder, so these marks only drive
            # the page; readiness below remains the broker's own check.
            displays = (self.directory / 'stream.sock').is_socket() and (self.directory / 'wayland-0').is_socket()
            picker = (self.directory / 'picker-ready').exists()
            self.report(desktop={'state': 'ready' if displays else 'running'},
                        files={'state': 'ready' if picker else 'running' if displays else 'pending'})
            if (self.directory / 'ready').exists() and (self.directory / 'stream.sock').is_socket() and self.running():
                self.report(desktop={'state': 'ready'}, files={'state': 'ready'})
                return
            if self.process.returncode is not None:
                break
            await asyncio.sleep(0.1)
        raise RuntimeError('Private desktop did not become ready')

    async def open_url(self, url):
        """Open an address as a new tab in this desktop's running browser.

        A second launch on the same profile hands its address to the running
        browser and exits; it never becomes a second browser.
        """
        name = pwd.getpwuid(self.uid).pw_name
        env = session_environment(self.home, name, self.directory)
        env['WAYLAND_DISPLAY'] = 'wayland-0'
        await self.command('/usr/sbin/runuser', '-u', name, '--', '/opt/brave.com/brave-origin/brave',
                           '--ozone-platform=wayland', f'--user-data-dir={self.home}/profile', url,
                           timeout=20, env=env)

    async def stop(self):
        await super().stop()
        if self.network is not None:
            await self.network.stop()
            self.network = None
        if self.directory:
            if self.failed:
                try:
                    retain_failure_logs(self.directory)
                except Exception:
                    LOG.exception('Could not retain private desktop failure logs')
            # Exact broker-generated path, only after the UID has no processes.
            shutil.rmtree(self.directory)
            self.directory = None


class Broker(files_api.FileService, sharing.ViewSharing, single.Manager):
    def __init__(self, config, desktop_factory=None, oidc=None):
        super().__init__(config, browser=Desktop(config), oidc=oidc)
        self.sessions = {}
        self.handoffs = {}
        self.documents = {}
        self.choices = set()
        self.preparations = {}
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
            response = await session.status(request)
            payload = json.loads(response.body)
            if session.browser.network:
                payload['network'] = session.browser.network.status(session.owner.get('admin',False))
            return web.json_response(payload)
        available = self.state == 'IDLE'  # Allow authentication at capacity to reclaim an existing desktop.
        return web.json_response({'state': 'IDLE' if available else 'BUSY', 'owner': False,
                                  'name': '', 'csrf': ''})

    def admin_routes(self):
        return [web.get('/admin/status', self.admin_status), web.post('/admin/warp', self.admin_warp),
                web.post('/admin/users/warp-permission', self.warp_permission),
                web.post('/session/warp', self.session_warp),
                web.post('/session/connection', self.connection_choice),
                web.get('/session/preparation-status', self.preparation_status),
                web.get('/session/prepare', self.prepare_page), web.post('/session/prepare', self.prepare_session),
                web.get('/session/resolve', self.resolve_page), web.post('/session/resolve', self.resolve_session),
                web.get('/office/view', self.office_view), web.get('/office/file', self.office_file), *self.files_routes(), *self.sharing_routes()]

    def require_admin(self, request, mutate=False):
        session = self.selected(request)
        if not session or session.state != 'RUNNING' or not session.owner.get('admin'):
            raise web.HTTPForbidden(text='Administrator session required')
        if mutate and (request.headers.get('Origin') != request['app_origin'] or
                       not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.owner['csrf'])):
            raise web.HTTPForbidden(text='Invalid admin request')
        return session

    async def admin_status(self, request):
        self.require_admin(request)
        users = []
        for path in sorted(user_network.USERS.glob('*/identity.json')):
            _, data = user_network.metadata(path.parent.name)
            key = data['key']
            session = self.sessions.get(key)
            users.append({'id':key, 'name':data.get('name') or 'User '+key[:8],
                'state':session.state if session and session.owner else 'OFFLINE',
                'allow_direct':data.get('allow_direct',False), 'warp_enabled':data.get('warp_enabled',os.environ.get('WARP_ENABLED') == 'true'),
                'last_login':data.get('last_login'),
                'admin':bool(session and session.owner and session.owner.get('admin'))})
        return web.json_response({'force_warp':user_network.forced(),
            'warp_available':shutil.which('warp-svc') is not None,
            'tos_accepted':os.environ.get('WARP_ACCEPT_TOS') == 'true', 'users':users})

    async def session_warp(self, request):
        session = self.selected(request)
        if not session:
            raise web.HTTPForbidden(text='Active session required')
        session.require_owner(request)
        if (request.headers.get('Origin') != request['app_origin'] or
            not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.owner['csrf'])):
            raise web.HTTPForbidden(text='Invalid request')
        data = await request.json()
        if not isinstance(data,dict) or type(data.get('enabled')) is not bool:
            raise web.HTTPBadRequest(text='enabled must be a boolean')
        async with self.lock, session.lock:
            session.require_owner(request)
            proxy = session.browser.network
            if not proxy.status(session.owner.get('admin',False))['can_toggle']:
                raise web.HTTPForbidden(text='Your administrator has not allowed changing WARP')
            try:
                await proxy.switch(data['enabled'])
                path, saved = user_network.metadata(proxy.key)
                saved['warp_enabled'] = data['enabled']
                user_network.save(path,saved)
            except (ValueError, RuntimeError, OSError) as error:
                raise web.HTTPServiceUnavailable(text=str(error)) from None
        return web.json_response({'network':proxy.status(session.owner.get('admin',False))})

    async def warp_permission(self, request):
        self.require_admin(request,mutate=True)
        data = await request.json()
        if not isinstance(data,dict) or type(data.get('allowed')) is not bool:
            raise web.HTTPBadRequest(text='allowed must be a boolean')
        async with self.lock:
            self.require_admin(request,mutate=True)
            try:
                path, saved = user_network.metadata(data.get('id'))
                session = self.sessions.get(data['id'])
                if not data['allowed']:
                    # Revocation also puts the affected active desktop back on WARP.
                    user_network.network.configuration(dict(os.environ,WARP_ENABLED='true'))
                    if session:
                        async with session.lock:
                            if session.browser.network:
                                await session.browser.network.switch(True)
                    path, saved = user_network.metadata(data['id'])
                    saved['warp_enabled'] = True
                saved['allow_direct'] = data['allowed']
                user_network.save(path,saved)
            except (ValueError,FileNotFoundError):
                raise web.HTTPBadRequest(text='Unknown user') from None
            except (RuntimeError,OSError) as error:
                raise web.HTTPServiceUnavailable(text=str(error)) from None
        return web.json_response({'changed':True})

    async def admin_warp(self, request):
        self.require_admin(request, mutate=True)
        data = await request.json()
        if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
            raise web.HTTPBadRequest(text='enabled must be a boolean')
        async with self.lock:
            self.require_admin(request, mutate=True)
            try:
                if data['enabled']:
                    user_network.network.configuration(dict(os.environ,WARP_ENABLED='true'))
                user_network.save(user_network.FORCE, {'force_warp':data['enabled']})
                failures = []
                for session in list(self.sessions.values()):
                    async with session.lock:
                        if session.browser.network:
                            proxy = session.browser.network
                            try:
                                _, saved = user_network.metadata(proxy.key)
                                await proxy.switch(data['enabled'] or saved.get('warp_enabled',os.environ.get('WARP_ENABLED') == 'true'))
                            except (ValueError,RuntimeError,OSError) as error:
                                # Apply enforcement to the remaining users too.
                                # Keep this failed route closed until recovery.
                                await proxy.manager.stop_worker()
                                failures.append(str(error))
                if failures:
                    raise RuntimeError('Some routes are unavailable: '+failures[0])
            except (ValueError,RuntimeError,OSError) as error:
                raise web.HTTPServiceUnavailable(text=str(error)) from None
        return web.json_response({'changed':True})

    async def login(self, request):
        return await super().login(request)

    async def callback(self, request):
        flow = self.take_flow(request)
        claims = await self.verify(request, flow)
        identity = hashlib.sha256(json.dumps([claims['iss'], claims['sub']], separators=(',', ':')).encode()).hexdigest()
        previous = self.sessions.get(identity)
        if previous is None or previous.state in ('STARTING', 'STOPPING', 'IDLE'):
            now = time.time()
            self.preparations = {k: v for k, v in self.preparations.items() if v['expires'] > now or self.preparing(v)}
            while len(self.preparations) >= 128:
                del self.preparations[next(iter(self.preparations))]
            token = secrets.token_urlsafe(32)
            self.preparations[token] = dict(identity=identity, claims=claims, flow=flow,
                expires=min(now + PREPARE_SECONDS, float(claims['exp'])))
            response = web.HTTPFound('/session/prepare')
            response.set_cookie('__Host-brave-prepare', token, secure=True, httponly=True,
                                samesite='Strict', path='/', max_age=PREPARE_SECONDS)
            single.forget_flow(response, flow)
            return response
        return await self.start_session(identity, claims, flow)

    @staticmethod
    def preparing(pending):
        return 'outcome' in pending and not pending['outcome'].done()

    def pending_preparation(self, request):
        token = request.cookies.get('__Host-brave-prepare', '')
        pending = self.preparations.get(token)
        # A launch in progress outlives its ticket; only a new attempt needs a valid one.
        if not pending or pending['expires'] <= time.time() and not self.preparing(pending):
            self.preparations.pop(token, None)
            raise TicketExpired(text='This sign-in expired. Please sign in again.')
        if pending['flow']['origin'] != request['app_origin']:
            raise web.HTTPForbidden(text='Invalid sign-in origin')
        return token, pending

    async def prepare_page(self, request):
        try:
            self.pending_preparation(request)
        except TicketExpired:
            # Reached by going back, a restored tab or the address bar. The
            # portal continues an open desktop or offers the sign-in button.
            LOG.info('Preparation page opened without a current sign-in; returning to the portal')
            response = web.HTTPFound('/?notice=signin_expired')
            response.del_cookie('__Host-brave-prepare', path='/', secure=True, httponly=True, samesite='Strict')
            return response
        return web.FileResponse('/usr/local/share/brave-origin/portal.html')

    async def preparation_status(self, request):
        _, pending = self.pending_preparation(request)
        progress = pending.get('progress', {'phase': 'waiting', 'tasks': []})
        return web.json_response(dict(progress, remaining=max(0, progress.get('deadline', 0)-time.time())),
                                 headers={'Cache-Control': 'no-store'})

    async def prepare_session(self, request):
        try:
            _, pending = self.pending_preparation(request)
        except TicketExpired as error:
            LOG.info('Desktop preparation refused: the sign-in is no longer current')
            return web.json_response({'error': error.text, 'location': '/?notice=signin_expired'}, status=403)
        if request.headers.get('Origin') != pending['flow']['origin']:
            raise web.HTTPForbidden(text='Invalid sign-in request')
        # One launch per sign-in. A page that reloads while it runs joins it
        # and receives its result: the browser keeps only the newest response,
        # so refusing the reload would discard the session cookie and leave a
        # desktop nobody can reach. A failed launch may be tried again.
        outcome = pending.get('outcome')
        if outcome is None or outcome.done() and (outcome.cancelled() or 'error' in outcome.result()):
            pending['progress'] = {'phase': 'profile', 'tasks': [
                {'id': 'profile', 'label': 'Your profile', 'state': 'running'},
                {'id': 'network', 'label': 'Connection', 'state': 'pending'},
                {'id': 'desktop', 'label': 'Desktop', 'state': 'pending'},
                {'id': 'files', 'label': 'My files', 'state': 'pending'}]}
            outcome = pending['outcome'] = asyncio.ensure_future(self.launch_prepared(pending))
            self.choices.add(outcome)
            outcome.add_done_callback(self.choices.discard)
        result = await asyncio.shield(outcome)
        if 'error' in result:
            return web.json_response({'error': result['error']}, status=result['status'])
        response = web.json_response({'location': result['location']})
        response.cookies.update(result['cookies'])
        response.del_cookie('__Host-brave-prepare', path='/', secure=True, httponly=True, samesite='Strict')
        return response

    async def launch_prepared(self, pending):
        try:
            destination = await self.start_session(pending['identity'], pending['claims'], pending['flow'], pending['progress'])
            if all(task['state'] == 'ready' for task in pending['progress']['tasks']):
                # Count down only once every step is complete. The page connects
                # the stream when this request returns, so it never starts early.
                pending['progress'].update(phase='countdown', deadline=time.time() + 3)
                await asyncio.sleep(3)
            # Long enough to answer a page that reloaded, then the ticket is gone.
            pending['expires'] = time.time() + 30
            return {'location': destination.headers['Location'], 'cookies': destination.cookies}
        except Exception as error:
            if not isinstance(error, web.HTTPException):
                LOG.exception('Desktop preparation failed for %s', pending['identity'][:12])
            pending['progress']['phase'] = 'error'
            for task in pending['progress']['tasks']:
                if task['state'] == 'running':
                    task['state'] = 'error'
            if isinstance(error, web.HTTPException):
                return {'error': error.text, 'status': error.status}
            return {'error': 'Your desktop could not start. Please retry.', 'status': 503}

    async def start_session(self, identity, claims, flow, progress=None):
        deadline = time.monotonic() + self.config.launch_timeout + 30
        while True:
            if float(claims['exp']) <= time.time():
                raise web.HTTPForbidden(text='Sign-in expired while waiting. Please sign in again.')
            async with self.lock:
                previous = self.sessions.get(identity)
                if previous is not None and previous.state == 'RUNNING':
                    return self.offer_handoff(identity, claims, flow)
                if previous is not None and previous.state not in ('STARTING', 'STOPPING', 'IDLE'):
                    raise web.HTTPServiceUnavailable(text='Your previous desktop could not close safely. Please try again shortly.')
                if previous is None:
                    if self.state != 'IDLE' or len(self.sessions) >= self.config.maximum:
                        raise web.HTTPConflict(text='No desktop slot is currently available')
                    session = single.Manager(self.config, browser=self.factory())
                    session.browser.startup = progress
                    session.browser.display_name = str(claims.get('name') or 'Private browser')[:120]
                    session.state = 'STARTING'
                    session.owner = {'cookie': secrets.token_urlsafe(32), 'csrf': secrets.token_urlsafe(32),
                        'picture': single.profile_picture(claims),
                        'origin': flow['origin'], 'name': str(claims.get('name') or 'Private browser')[:120],
                        # The ID token proves the sign-in; it does not time the
                        # desktop. SESSION_MAX_SECONDS is the only lifetime.
                        'expires': time.time() + self.config.ttl}
                    session.owner['admin'] = admin_claim(claims, self.config.group_claim)
                    session.browser.admin = session.owner['admin']
                    session.owner['started_at'] = time.time()
                    self.sessions[identity] = session
                    break
            # Do not hold the broker lock while another instance finishes using
            # this profile. Recheck the identity map after cleanup removes it.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise web.HTTPServiceUnavailable(text='Your desktop is taking longer to finish. Please try again shortly.')
            try:
                async with asyncio.timeout(remaining):
                    async with previous.lock:
                        pass
            except TimeoutError:
                raise web.HTTPServiceUnavailable(text='Your desktop is taking longer to finish. Please try again shortly.') from None
            await asyncio.sleep(.05)
        async with session.lock:
            try:
                await session.browser.start(claims['iss'], claims['sub'])
                session.http = ClientSession(connector=UnixConnector(path=str(session.browser.directory / 'stream.sock')),
                                            timeout=ClientTimeout(total=None, connect=10, sock_read=30))
                await self.provision_view_tokens(session)
                session.state = 'RUNNING'
                session.last_disconnect = time.monotonic()
            except Exception as error:
                session.browser.failed = True
                LOG.exception('Private desktop launch failed for %s', identity[:12])
                await self.end(identity, session)
                phase = progress['phase'] if progress else None
                message = ("This server's Brave is older than the one that last opened your profile. Ask your administrator to update Brave."
                           if isinstance(error, ProfileTooNew) else
                           'Your profile could not be opened. Please retry.' if phase == 'profile' else
                           'Your connection could not become ready. Please retry shortly.' if phase == 'connection' else
                           'Your desktop could not start. Please retry.')
                raise web.HTTPServiceUnavailable(text=message) from None
            response = web.HTTPFound('/')
            response.set_cookie(single.COOKIE, session.owner['cookie'], secure=True, httponly=True,
                                samesite='Lax', path='/', max_age=max(1, int(session.owner['expires'] - time.time())))
            single.forget_flow(response, flow)
            return response

    def offer_handoff(self, identity, claims, flow):
        session = self.sessions[identity]
        if session.state != 'RUNNING' or not session.owner:
            raise web.HTTPConflict(text='Your desktop is changing. Please try signing in again shortly.')
        now = time.time()
        self.handoffs = {k: v for k, v in self.handoffs.items() if v['expires'] > now}
        while len(self.handoffs) >= 128:
            del self.handoffs[next(iter(self.handoffs))]
        token = secrets.token_urlsafe(32)
        self.handoffs[token] = dict(identity=identity, claims=claims, origin=flow['origin'],
            session=self.sessions[identity], owner_cookie=self.sessions[identity].owner['cookie'],
            csrf=secrets.token_urlsafe(32), expires=min(now + HANDOFF_SECONDS, float(claims['exp'])))
        response = web.HTTPFound('/session/resolve')
        response.set_cookie('__Host-brave-handoff', token, secure=True, httponly=True,
                            samesite='Strict', path='/', max_age=HANDOFF_SECONDS)
        single.forget_flow(response, flow)
        return response

    def pending_handoff(self, request):
        token = request.cookies.get('__Host-brave-handoff', '')
        pending = self.handoffs.get(token)
        if pending and pending['origin'] != request['app_origin']:
            raise web.HTTPForbidden(text='Invalid session origin')
        if not pending or pending['expires'] <= time.time():
            # A dead end here strands whoever reloads, goes back or waits too
            # long. The portal offers a fresh choice to a signed-in owner and
            # the sign-in button to anyone else.
            LOG.info('Session choice unavailable: %s', 'ticket expired' if pending
                     else 'unknown or already finished ticket' if token else 'no ticket')
            self.handoffs.pop(token, None)
            response = web.HTTPFound('/?notice=choice_expired')
            response.del_cookie('__Host-brave-handoff', path='/', secure=True, httponly=True, samesite='Strict')
            raise response
        return token, pending

    async def resolve_page(self, request):
        _, pending = self.pending_handoff(request)
        template = Path('/usr/local/share/brave-origin/session-choice.html').read_text()
        return web.Response(content_type='text/html', text=template.replace('__CSRF__', pending['csrf']))

    async def connection_choice(self, request):
        session = self.selected(request)
        if (not session or request.headers.get('Origin') != request['app_origin'] or
                not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.owner['csrf'])):
            raise web.HTTPForbidden()
        # Let a refresh release its previous socket before offering takeover.
        for _ in range(30):
            if not session.connections:
                return web.json_response({'location': None})
            await asyncio.sleep(.1)
        if session.state != 'RUNNING' or not session.owns(request):
            raise web.HTTPConflict(text='The desktop changed. Please reload.')
        identity = next(k for k, v in self.sessions.items() if v is session)
        claims = {'exp':session.owner['expires']}
        redirect = self.offer_handoff(identity, claims, {'origin':request['app_origin']})
        ticket = self.handoffs[redirect.cookies['__Host-brave-handoff'].value]
        ticket['same_owner'] = True
        response = web.json_response({'location':'/session/resolve'})
        response.cookies.update(redirect.cookies)
        return response

    async def resolve_session(self, request):
        token, pending = self.pending_handoff(request)
        data = await request.post()
        if (request.headers.get('Origin') != pending['origin'] or
                not secrets.compare_digest(str(data.get('csrf', '')), pending['csrf'])):
            raise web.HTTPForbidden(text='Invalid session confirmation')
        action = data.get('action')
        if action not in ('takeover', 'disconnect', 'cancel'):
            raise web.HTTPBadRequest(text='Unknown session choice')
        # The choice is applied once. A repeated submission (a second click
        # while the takeover settles) waits for that one result and receives
        # it too: the browser keeps only its last response, so refusing the
        # repeat would discard the new session cookie and sign the user out.
        if 'outcome' not in pending:
            pending['expires'] = time.time() + 120
            pending['outcome'] = asyncio.ensure_future(self.settle_choice(pending, action))
            self.choices.add(pending['outcome'])
            pending['outcome'].add_done_callback(self.choices.discard)
        # Shielded: a browser that gives up must not stop a takeover halfway.
        result = await asyncio.shield(pending['outcome'])
        if 'status' in result:
            return web.Response(status=result['status'], text=result['text'])
        response = web.HTTPFound(result['location'])
        if result.get('cookie'):
            response.set_cookie(single.COOKIE, result['cookie'], secure=True, httponly=True, samesite='Lax',
                                path='/', max_age=max(1, int(result['expires'] - time.time())))
        response.del_cookie('__Host-brave-handoff', path='/', secure=True, httponly=True, samesite='Strict')
        return response

    async def settle_choice(self, pending, action):
        try:
            return await self.apply_choice(pending, action)
        except web.HTTPException as error:
            LOG.info('Session choice %s refused for %s: %s', action, pending['identity'][:12], error.text)
            return {'status': error.status, 'text': error.text}
        finally:
            # Long enough to answer a repeated click, then the ticket is gone.
            pending['expires'] = time.time() + 30

    async def apply_choice(self, pending, action):
        async with self.lock:
            session = self.sessions.get(pending['identity'])
            if action == 'cancel':
                return {'location': '/?session_cancelled=1'}
            if self.state != 'IDLE' or session is not pending['session']:
                raise web.HTTPConflict(text='The desktop changed. Please sign in again.')
            async with session.lock:
                if (session.state != 'RUNNING' or not session.owner or
                        session.owner['cookie'] != pending['owner_cookie']):
                    raise web.HTTPConflict(text='The desktop changed. Please sign in again.')
                if action == 'disconnect':
                    LOG.info('Ending desktop %s: closed from a new sign-in', pending['identity'][:12])
                    await self.end(pending['identity'], session)
                    if session.state != 'IDLE':
                        raise web.HTTPServiceUnavailable(text='Desktop cleanup failed')
                    return {'location': '/'}
                await self.revoke_session_views(session)
                session.state = 'STARTING'
                try:
                    session.owner = {**session.owner, 'cookie': secrets.token_urlsafe(32),
                        'csrf': secrets.token_urlsafe(32),
                        'admin': session.owner['admin'] if pending.get('same_owner') else admin_claim(pending['claims'], self.config.group_claim),
                        # A fresh sign-in starts a new lifetime; reopening the
                        # same session in another tab keeps the one it had.
                        'expires': session.owner['expires'] if pending.get('same_owner') else time.time() + self.config.ttl}
                    proxy = getattr(session.browser, 'network', None)
                    if proxy:
                        proxy.admin = session.owner['admin']
                    # Not 4001: the stream client reads that as a bad token. A
                    # "superseded" reason makes it say the session moved and
                    # stops it reconnecting against the new holder.
                    await asyncio.gather(*(ws.close(code=4003, message=b'Session superseded by a new sign-in')
                        for ws in list(session.connections)), return_exceptions=True)
                    session.connections.clear()
                    tasks = list(session.transfers)
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    if session.connected_once:
                        await asyncio.sleep(4)  # Let the previous Selkies peer teardown settle.
                finally:
                    # Never leave the desktop in STARTING: nothing would reap it
                    # and its owner could neither return nor start another.
                    session.connected_once = False
                    session.last_disconnect = time.monotonic()
                    session.state = 'RUNNING'
                LOG.info('Desktop %s taken over (%s)', pending['identity'][:12],
                         'same sign-in, another tab' if pending.get('same_owner') else 'new sign-in')
                return {'location': '/', 'cookie': session.owner['cookie'], 'expires': session.owner['expires']}

    async def end(self, identity, session):
        self.files_closed(session)
        await self.revoke_session_views(session)
        await session.stop_locked()
        if session.http:
            await session.http.close()
        if session.state == 'IDLE':
            self.sessions.pop(identity, None)

    async def logout(self, request):
        session = self.selected(request)
        if not session:
            raise web.HTTPForbidden()
        # Authorization is checked by logout before any session mutation.
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.owner['csrf']):
            raise web.HTTPForbidden()
        await self.revoke_session_views(session)
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
        path = request.path[len('/desktop'):] if request.path.startswith('/desktop/') else request.path
        action = request.headers.get('X-My-Files-Action')
        if request.method == 'POST' and path.startswith('/api/files/') and action:
            relative = path[len('/api/files/'):]
            if action == 'open':
                return await self.open_file(request, session, relative)
            if action in ('mkdir', 'rename', 'remove'):
                return self.change_file(request, session, relative, action)
            raise web.HTTPBadRequest(text='Unknown My files action')
        return await session.proxy(request)

    def change_file(self, request, session, relative, action):
        """Create a folder, rename an entry or delete one in the owner's My files."""
        if session.state != 'RUNNING' or request.headers.get('Origin') != request['app_origin']:
            raise web.HTTPForbidden(text='Changing files requires the session that owns them')
        parts = relative.rstrip('/').split('/')
        try:
            files = self.my_files(session)
            try:
                if action == 'mkdir':
                    # Created by this root process; hand it to the account that owns the tree.
                    files.mkdir(parts, owner=session.browser.uid)
                elif action == 'rename':
                    files.rename(parts, unquote(request.headers.get('X-My-Files-Name', '')))
                else:
                    files.remove(parts)
            finally:
                os.close(files.fd)
        except FileExistsError:
            raise web.HTTPConflict(text='Something with that name already exists here.') from None
        except FileNotFoundError:
            raise web.HTTPNotFound(text='That item is no longer in My files.') from None
        except (OSError, ValueError):
            raise web.HTTPBadRequest(text='Use a plain name without / or a leading dot, inside My files.') from None
        return web.json_response({'changed': True})

    @staticmethod
    def my_files(session):
        # No-symlink traversal: this process is root and the tree is the user's.
        return picker.Files(session.browser.home / 'Downloads')

    def thumbnailer(self, session):
        """The worker that draws this session's thumbnails, as the session's own user."""
        browser = session.browser
        name = pwd.getpwuid(browser.uid).pw_name
        return files_api.Thumbnailer(
            ['/usr/sbin/runuser', '-u', name, '--', 'python3', '/usr/local/bin/file-picker.py', '--render',
             str(browser.home / 'Downloads')], session_environment(browser.home, name, browser.directory))

    async def open_file(self, request, session, relative):
        """Open one of the owner's files as a tab in their remote browser."""
        if session.state != 'RUNNING' or request.headers.get('Origin') != request['app_origin']:
            raise web.HTTPForbidden(text='Opening a file requires the session that owns it')
        kind = await self.open_parts(session, relative.split('/'))
        return web.json_response({'opened': True, 'viewer': 'office' if kind else 'browser'})

    async def open_parts(self, session, parts):
        try:
            files = self.my_files(session)
            try:
                address = files.uri(parts)
            finally:
                os.close(files.fd)
        except (OSError, ValueError):
            raise web.HTTPNotFound(text='That file is not available in My files.') from None
        reason = picker.openable(parts[-1], bool(self.config.office_url))
        if reason:
            raise (web.HTTPConflict if Path(parts[-1]).suffix.lower().lstrip('.') in OFFICE_TYPES
                   else web.HTTPUnsupportedMediaType)(text=reason)
        kind = OFFICE_TYPES.get(Path(parts[-1]).suffix.lower().lstrip('.'))
        if kind:
            now = time.time()
            self.documents = {k: v for k, v in self.documents.items() if v['expires'] > now}
            while len(self.documents) >= 256:
                del self.documents[next(iter(self.documents))]
            token = secrets.token_urlsafe(32)
            self.documents[token] = dict(session=session, parts=parts, kind=kind, origin=session.owner['origin'],
                                         expires=min(now + OFFICE_SECONDS, session.owner['expires']))
            # The query string keeps the token out of the access log.
            address = session.owner['origin'] + '/office/view?t=' + token
        try:
            await session.browser.open_url(address)
        except Exception:
            LOG.exception('Could not open a file in the remote browser')
            raise web.HTTPServiceUnavailable(text='The browser did not open the file. Please try again.') from None
        return kind

    def picker_request(self, session):
        """Take the file the user's picker asked to open in Brave, if any.

        The picker has no session cookie, so it leaves a note in its private
        runtime folder. That folder belongs to the user: the note is read
        without following links and names a path that is checked like any other.
        """
        directory = getattr(session.browser, 'directory', None)
        if not directory:
            return
        note = Path(directory) / 'open-request'
        try:
            fd = os.open(note, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        except OSError:
            # A link or something unreadable in the note's place: discard it.
            with contextlib.suppress(OSError):
                os.unlink(note)
            return
        try:
            info = os.fstat(fd)
            data = os.read(fd, 8192) if stat.S_ISREG(info.st_mode) and info.st_size <= 8192 else b''
        finally:
            os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(note)
        try:
            parts = json.loads(data)['parts']
            if not isinstance(parts, list) or not parts or not all(isinstance(part, str) for part in parts):
                raise ValueError('Invalid request')
        except (ValueError, KeyError, TypeError):
            return

        async def deliver():
            try:
                await self.open_parts(session, parts)
            except web.HTTPException as error:
                LOG.info('Picker request to open a file was refused: %s', error.text)
        task = asyncio.ensure_future(deliver())
        self.choices.add(task)
        task.add_done_callback(self.choices.discard)

    def document(self, request):
        """The file behind a document link. The link itself is the credential:
        the document server and the remote browser hold no session cookie."""
        entry = self.documents.get(request.query.get('t', ''))
        if (not entry or entry['expires'] <= time.time() or entry['origin'] != request['app_origin']
                or entry['session'].state != 'RUNNING' or entry['session'] not in self.sessions.values()):
            raise web.HTTPNotFound(text='This document link is no longer valid. Open the file again from My files.')
        try:
            files = self.my_files(entry['session'])
            try:
                fd = files.open(entry['parts'])
            finally:
                os.close(files.fd)
        except (OSError, ValueError):
            raise web.HTTPNotFound(text='This file is no longer in My files.') from None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise web.HTTPNotFound(text='This file is no longer in My files.')
        return entry, fd, info

    async def office_view(self, request):
        entry, fd, info = self.document(request)
        os.close(fd)
        name = entry['parts'][-1]
        # A new key for every version of the file, so the server never shows a stale copy.
        key = hashlib.sha256(json.dumps([id(entry['session']), entry['parts'], info.st_mtime_ns, info.st_size]).encode()).hexdigest()[:40]
        config = {'document': {'fileType': Path(name).suffix.lower().lstrip('.'), 'key': key, 'title': name,
                               'url': entry['origin'] + '/office/file?t=' + request.query['t'],
                               'permissions': {'edit': False, 'review': False, 'comment': False,
                                               'fillForms': False, 'download': True, 'print': True}},
                  'documentType': entry['kind'], 'type': 'desktop', 'width': '100%', 'height': '100%',
                  'editorConfig': {'mode': 'view', 'customization': {'uiTheme': 'theme-dark', 'compactHeader': True}}}
        if self.config.office_secret:
            config['token'] = single.jwt.encode(config, self.config.office_secret, algorithm='HS256')
        script = json.dumps(config).replace('<', '\\u003c')
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + html.escape(name) + '</title>'
                '<style>html,body,#viewer{height:100%;margin:0;background:#11151d;color:#edf1fa;font:16px system-ui}'
                '#viewer{display:grid;place-items:center}</style></head><body><div id="viewer">Opening document…</div>'
                '<script src="' + html.escape(self.config.office_url) + '/web-apps/apps/api/documents/api.js"></script>'
                '<script>if(window.DocsAPI)new DocsAPI.DocEditor("viewer",' + script + ');'
                'else document.getElementById("viewer").textContent="The document server could not be reached.";</script>'
                '</body></html>')
        return web.Response(text=page, content_type='text/html')

    async def office_file(self, request):
        _, fd, info = self.document(request)
        try:
            response = web.StreamResponse(headers={'Content-Type': 'application/octet-stream',
                                                   'Content-Length': str(info.st_size)})
            await response.prepare(request)
            loop = asyncio.get_running_loop()
            remaining = info.st_size
            while remaining:
                chunk = await loop.run_in_executor(None, os.read, fd, min(remaining, 1 << 18))
                if not chunk:
                    break
                remaining -= len(chunk)
                await response.write(chunk)
            return response
        finally:
            os.close(fd)

    async def monitor(self):
        while True:
            await asyncio.sleep(1)
            await self.prune_shares()
            for identity, session in list(self.sessions.items()):
                if session.lock.locked():
                    continue
                async with session.lock:
                    if session.state == 'RUNNING':
                        if not session.browser.running():
                            session.browser.failed = True
                        if getattr(session.browser, 'network', None):
                            try:
                                await session.browser.network.recover()
                            except (OSError,RuntimeError,ValueError):
                                LOG.exception('Private network recovery failed')
                        self.picker_request(session)
                        reason = session.end_reason()
                        if reason:
                            LOG.info('Ending desktop %s (%s): %s', identity[:12], session.owner['name'], reason)
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
        await user_network.recover()
        for directory in root.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory)
        # Chromium policy expands ${user_home} separately in each process.
        Path('/etc/brave/policies/managed/policies.json').write_text(
            json.dumps(single.policies('${user_home}/Downloads', files=str(self.browser.users))))
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
