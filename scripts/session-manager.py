#!/usr/bin/env python3
"""OIDC admission gateway and browser lifecycle. Runs as root, browser never does."""
import asyncio
import base64
import contextlib
import grp
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import time
from urllib.parse import urlencode, urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from yarl import URL
import jwt

LOG = logging.getLogger('browser-session')
COOKIE = '__Host-brave-session'
FLOW_COOKIE = '__Host-brave-login'
HOP = {'connection', 'upgrade', 'keep-alive', 'transfer-encoding', 'te', 'trailer',
       'proxy-authorization', 'proxy-authenticate', 'set-cookie', 'content-length'}
# Helpers that drop privileges must not inherit the supervisor's environment: it
# carries OIDC_CLIENT_SECRET, which the unprivileged account could read back out
# of /proc/<pid>/environ for as long as the helper runs.
HELPER_ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}


def https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('OIDC and application URLs must be absolute HTTPS URLs')
    return value.rstrip('/')


def policies(download_directory, env=os.environ):
    """Managed Chromium policy: a locked baseline plus operator additions.

    Each blocked feature opens a browsing context outside the managed profile,
    where this appliance's per-identity separation does not reach. The download
    directory is not negotiable: it is what keeps one identity's files out of
    another's home, so operator additions cannot move it.
    """
    policy = {'BookmarkBarEnabled': True, 'BackgroundModeEnabled': False,
              'IncognitoModeAvailability': 1, 'TorDisabled': True,
              'BrowserGuestModeEnabled': False, 'BrowserAddPersonEnabled': False,
              # Casting discovers and reaches devices on the host's network from
              # inside a session, which no remote browser user should inherit.
              'EnableMediaRouter': False, 'ShowCastIconInToolbar': False}
    extra = env.get('BROWSER_POLICY', '').strip()
    if extra:
        try:
            added = json.loads(extra)
        except ValueError as error:
            raise ValueError(f'BROWSER_POLICY must be valid JSON: {error}') from None
        if not isinstance(added, dict):
            raise ValueError('BROWSER_POLICY must be a JSON object of policy names')
        policy.update(added)
    policy['DownloadDirectory'] = download_directory
    return policy


def integer(env, name, default, low, high):
    value = int(env.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return value


class Config:
    def __init__(self, env=os.environ):
        self.issuer = https_url(env.get('OIDC_ISSUER_URL', ''))
        self.client_id = env.get('OIDC_CLIENT_ID', '')
        secret_file = env.get('OIDC_CLIENT_SECRET_FILE', '')
        self.secret = Path(secret_file).read_text().strip() if secret_file else env.get('OIDC_CLIENT_SECRET', '')
        if not self.client_id or not self.secret:
            raise ValueError('OIDC_CLIENT_ID and OIDC_CLIENT_SECRET[_FILE] are required')
        if env is os.environ:
            # No child inherits the secret from here on. Children that drop to an
            # unprivileged account would otherwise publish it through their own
            # /proc/<pid>/environ. This supervisor's exec-time snapshot still
            # holds it, but that one is readable only by root.
            os.environ.pop('OIDC_CLIENT_SECRET', None)
        self.scopes = env.get('OIDC_SCOPES', 'openid profile email groups')
        if 'openid' not in self.scopes.split():
            raise ValueError('OIDC_SCOPES must include openid')
        self.groups = set(filter(None, (s.strip() for s in env.get('OIDC_ALLOWED_GROUPS', '').split(','))))
        self.group_claim = env.get('OIDC_GROUPS_CLAIM', 'groups')
        self.ttl = integer(env, 'SESSION_MAX_SECONDS', 3600, 60, 86400)
        self.grace = integer(env, 'DISCONNECT_GRACE_SECONDS', 30, 0, 300)
        self.start_timeout = integer(env, 'SESSION_CONNECT_TIMEOUT', 90, 10, 300)
        self.update_interval = integer(env, 'UPDATE_INTERVAL', 21600, 60, 604800)
        self.auto_update = env.get('AUTO_UPDATE', 'true') == 'true'
        # Reject a malformed policy at startup rather than at the first login.
        policies('/nonexistent', env)
        self.upload_limit = integer(env, 'MAX_UPLOAD_MB', 1024, 1, 10240) * 1024 * 1024


class OIDC:
    def __init__(self, config):
        self.config = config
        self.metadata = None

    async def discovery(self, http):
        if self.metadata is None:
            async with http.get(self.config.issuer + '/.well-known/openid-configuration', allow_redirects=False) as response:
                response.raise_for_status()
                data = await response.json()
            if data.get('issuer') != self.config.issuer:
                raise ValueError('Discovery issuer mismatch')
            for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
                https_url(data[key])
            self.metadata = data
        return self.metadata

    async def authenticate(self, http, code, flow):
        metadata = await self.discovery(http)
        async with http.post(metadata['token_endpoint'], data={
            'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': flow['origin'] + '/auth/callback', 'client_id': self.config.client_id,
            'client_secret': self.config.secret, 'code_verifier': flow['verifier'],
        }, allow_redirects=False) as response:
            response.raise_for_status()
            token = (await response.json())['id_token']
        # Refresh JWKS for every login, including rotation. Never trust token-supplied URLs.
        async with http.get(metadata['jwks_uri'], allow_redirects=False) as response:
            response.raise_for_status()
            keys = (await response.json())['keys']
        header = jwt.get_unverified_header(token)
        algorithm = header.get('alg')
        if algorithm not in ('RS256', 'ES256', 'EdDSA'):
            raise ValueError('Unsupported signing algorithm')
        candidates = [key for key in keys if key.get('kid') == header.get('kid') and key.get('use', 'sig') == 'sig'
                      and key.get('alg', algorithm) == algorithm]
        if len(candidates) != 1:
            raise ValueError('Ambiguous signing key')
        claims = jwt.decode(token, jwt.PyJWK.from_dict(candidates[0], algorithm=algorithm).key,
                            algorithms=[algorithm], issuer=self.config.issuer,
                            audience=self.config.client_id,
                            options={'require': ['exp', 'iat', 'iss', 'sub', 'aud', 'nonce']})
        if not isinstance(claims['sub'], str) or not claims['sub']:
            raise ValueError('Missing subject')
        if not secrets.compare_digest(str(claims['nonce']), flow['nonce']):
            raise ValueError('Nonce mismatch')
        audiences = claims['aud'] if isinstance(claims['aud'], list) else [claims['aud']]
        if (len(audiences) > 1 or 'azp' in claims) and claims.get('azp') != self.config.client_id:
            raise ValueError('Authorized party mismatch')
        if self.config.groups:
            groups = claims.get(self.config.group_claim, [])
            if not isinstance(groups, list) or not self.config.groups.intersection(g for g in groups if isinstance(g, str)):
                raise ValueError('User is not in an allowed group')
        return claims


class Browser:
    def __init__(self):
        self.process = None
        self.uid = None
        self.home = None
        self.metadata = None
        self.users = Path('/config/users')
        self.runtime = Path('/run/brave-origin/session')

    @staticmethod
    async def command(*args, timeout=40, **kwargs):
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, **kwargs)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f'{args[0]} failed')
        return stdout.decode().strip()

    @staticmethod
    def app_display():
        """The window manager's socket, published by the desktop once it is ready.

        Browsers belong on the nested compositor, exactly where the legacy
        session launches them: the kiosk window rules and suppressed shortcuts
        live there, and the capture compositor beneath enforces none of them.
        Which socket that is depends on compositor startup order, so follow what
        the desktop reports instead of assuming a number.
        """
        name = ''
        with contextlib.suppress(OSError):
            name = Path('/tmp/brave-desktop-ready').read_text().strip()
        if not re.fullmatch(r'wayland-[0-9]+', name):
            LOG.warning('Desktop published no usable display name; falling back')
            name = 'wayland-1'
        socket = Path('/tmp/runtime-braveuser') / name
        if not socket.is_socket():
            raise RuntimeError('Desktop compositor socket is missing')
        return name

    @staticmethod
    def gpu_devices():
        """Render/NVIDIA character devices a browser needs to reach the GPU."""
        devices = sorted(Path('/dev/dri').glob('renderD*')) + sorted(Path('/dev').glob('nvidia*'))
        node = os.environ.get('DRI_NODE')
        if node:
            devices.append(Path(node))
        return [device for device in devices if device.is_char_device()]

    @staticmethod
    def pids(uid):
        result = []
        for directory in Path('/proc').glob('[0-9]*'):
            try:
                status = (directory / 'status').read_text()
                fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
                if int(fields['Uid'].split()[0]) == uid and not fields['State'].strip().startswith('Z'):
                    result.append(int(directory.name))
            except (OSError, ValueError, KeyError):
                continue
        return result

    def running(self):
        if self.uid is None or self.process is None or self.process.returncode is not None:
            return False
        for pid in self.pids(self.uid):
            with contextlib.suppress(OSError):
                if Path(f'/proc/{pid}/comm').read_text().strip() == 'brave':
                    return True
        return False

    @staticmethod
    def send_signal(uid, sig):
        for pid in Browser.pids(uid):
            with contextlib.suppress(ProcessLookupError, FileNotFoundError):
                fd = os.pidfd_open(pid)
                try:
                    # pidfd prevents signaling a different process after PID reuse.
                    if pid in Browser.pids(uid):
                        signal.pidfd_send_signal(fd, sig)
                finally:
                    os.close(fd)

    async def recover(self):
        self.users.mkdir(mode=0o711, exist_ok=True)
        if self.users.is_symlink() or self.users.stat().st_uid != 0:
            raise RuntimeError('Unsafe users directory')
        # mkdir's mode is reduced by the container UMASK. Profile users need
        # traversal through these root-owned parents, but never directory listing.
        self.users.chmod(0o711)
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Container recreations lose /etc/passwd. Metadata remains root-owned.
        for metadata in self.users.glob('*/identity.json'):
            if metadata.is_symlink() or metadata.stat().st_uid != 0:
                raise RuntimeError('Unsafe profile metadata')
            uid = json.loads(metadata.read_text())['uid']
            if not isinstance(uid, int) or not 200000 <= uid < 1000200000:
                raise RuntimeError('Invalid saved profile UID')
            self.send_signal(uid, signal.SIGKILL)
            for _ in range(30):
                if not self.pids(uid):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError('Unable to reap abandoned browser')
        await self.clear_shared()

    async def prepare(self, issuer, subject):
        key = hashlib.sha256(json.dumps([issuer, subject], separators=(',', ':')).encode()).hexdigest()
        directory = self.users / key
        directory.mkdir(mode=0o711, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != 0:
            raise RuntimeError('Unsafe identity directory')
        directory.chmod(0o711)
        metadata = directory / 'identity.json'
        name = 'brv_' + key[:20]
        uid = 200000 + int(key[:8], 16) % 1000000000
        if metadata.exists():
            if metadata.is_symlink() or metadata.stat().st_uid != 0:
                raise RuntimeError('Unsafe identity metadata')
            uid = json.loads(metadata.read_text())['uid']
            if not isinstance(uid, int) or not 200000 <= uid < 1000200000:
                raise RuntimeError('Invalid saved profile UID')
        while True:
            try:
                account = pwd.getpwuid(uid)
                if account.pw_name == name:
                    break
                if metadata.exists():
                    raise RuntimeError('Saved profile UID conflicts with another account')
                uid += 1
            except KeyError:
                break
        self.uid, self.home, self.metadata = uid, directory / 'home', metadata
        try:
            account = pwd.getpwnam(name)
            if account.pw_uid != uid:
                raise RuntimeError('Profile account identity mismatch')
        except KeyError:
            with contextlib.suppress(KeyError):
                if grp.getgrgid(uid).gr_name != name:
                    raise RuntimeError('Profile group collision')
            try:
                grp.getgrnam(name)
            except KeyError:
                await self.command('groupadd', '-g', str(uid), name)
            await self.command('useradd', '-M', '-u', str(uid), '-g', name, '-G', 'brave-display',
                               '-d', str(self.home), '-s', '/usr/sbin/nologin', name)
        if not metadata.exists():
            metadata.write_text(json.dumps({'uid': uid, 'key': key}) + '\n')
            metadata.chmod(0o600)
        self.home.mkdir(mode=0o700, exist_ok=True)
        if self.home.is_symlink():
            raise RuntimeError('Unsafe home directory')
        os.chown(self.home, uid, uid)
        self.home.chmod(0o700)
        return name

    async def start(self, issuer, subject):
        with open('/run/lock/brave-origin-launch.lock', 'w') as lock:
            # Poll nonblocking so cancellation never leaves a worker holding a lock.
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
            await self.start_locked(issuer, subject)

    async def start_locked(self, issuer, subject):
        name = await self.prepare(issuer, subject)
        await self.clear_profile_locks(name)
        installed = await self.command('dpkg-query', '-W', '-f=${Version}', 'brave-origin')
        saved = json.loads(self.metadata.read_text())
        if saved.get('version'):
            check = await asyncio.create_subprocess_exec('dpkg', '--compare-versions', installed, 'lt', saved['version'])
            if await check.wait() == 0:
                raise RuntimeError('Installed browser is older than this profile')
        saved['version'] = installed
        self.metadata.write_text(json.dumps(saved) + '\n')
        # Shared desktop sockets are accessible only to explicit display members.
        display_gid = grp.getgrnam('brave-display').gr_gid
        runtime = Path('/tmp/runtime-braveuser')
        os.chown(runtime, pwd.getpwnam('braveuser').pw_uid, display_gid)
        runtime.chmod(0o710)
        for socket in runtime.glob('wayland-*'):
            if socket.is_socket():
                os.chown(socket, pwd.getpwnam('braveuser').pw_uid, display_gid)
                socket.chmod(0o660)
        pulse = runtime / 'pulse'
        if pulse.is_dir():
            os.chown(pulse, pwd.getpwnam('braveuser').pw_uid, display_gid)
            pulse.chmod(0o710)
            if (pulse / 'oidc').is_socket():
                os.chown(pulse / 'oidc', pwd.getpwnam('braveuser').pw_uid, display_gid)
                (pulse / 'oidc').chmod(0o660)
        private_runtime = self.runtime / str(self.uid)
        private_runtime.mkdir(mode=0o700, exist_ok=True)
        os.chown(private_runtime, self.uid, self.uid)
        # Parent permits traversal but no listing; each child is private.
        self.runtime.chmod(0o711)
        # Every render node and NVIDIA character device the browser may need, not
        # just DRI_NODE: an NVIDIA container exposes /dev/nvidia* and its render
        # node under groups this account is not born into.
        groups = set()
        for device in self.gpu_devices():
            with contextlib.suppress(OSError):
                info = device.stat()
                if info.st_gid and info.st_mode & 0o060:
                    groups.add(info.st_gid)
        for gid in sorted(groups):
            # Losing one device group costs acceleration, never the session.
            try:
                await self.command('usermod', '-aG', str(gid), name)
            except RuntimeError:
                LOG.warning('Could not grant group %s to a browser profile', gid)
        # User's download path is private too; no shared Selkies file server in OIDC mode.
        policy = Path('/etc/brave/policies/managed/policies.json')
        policy.write_text(json.dumps(policies(str(self.home / 'Downloads'))))
        env = {'HOME': str(self.home), 'USER': name, 'LOGNAME': name, 'PATH': '/usr/local/bin:/usr/bin:/bin',
               'LANG': 'C.UTF-8', 'XDG_RUNTIME_DIR': str(private_runtime),
               'WAYLAND_DISPLAY': str(runtime / self.app_display()),
               'PULSE_SERVER': f'unix:{runtime}/pulse/oidc'}
        for key in ('ENABLE_GPU', 'DRI_NODE', 'BRAVE_FLAGS', 'TZ'):
            if key in os.environ:
                env[key] = os.environ[key]
        log = open(self.runtime / 'browser.log', 'ab')
        os.chmod(self.runtime / 'browser.log', 0o600)
        try:
            self.process = await asyncio.create_subprocess_exec('/usr/sbin/runuser', '-u', name, '--',
                '/usr/local/bin/browser-session.sh', env=env, stdout=log, stderr=log, start_new_session=True)
        finally:
            log.close()
        for _ in range(100):
            if self.running():
                return
            if self.process.returncode is not None:
                break
            await asyncio.sleep(0.2)
        raise RuntimeError('Browser failed to start')

    async def clear_profile_locks(self, name):
        # The container owns /config's instance lock and start() holds the launch
        # lock. Never override an active profile, even if its lock looks stale.
        if self.pids(self.uid):
            raise RuntimeError('Profile processes still running; refusing to unlock')
        # Run as the profile account and unlink only these three directory entries.
        # Never follow a profile-directory symlink or a singleton symlink target.
        await self.command('/usr/sbin/runuser', '-u', name, '--', 'python3', '-c', '''
import os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
except FileNotFoundError:
    sys.exit(0)
try:
    for name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass
finally:
    os.close(fd)
''', str(self.home / 'profile'), env=HELPER_ENV)

    async def clear_shared(self):
        # Clear both compositors' clipboard ownership without stopping either.
        # Best effort: a clipboard that will not clear must not turn a routine
        # logout into a locked appliance.
        for display in ('wayland-0', 'wayland-1'):
            with contextlib.suppress(RuntimeError, OSError):
                await self.command('/usr/sbin/runuser', '-u', 'braveuser', '--', 'env',
                    'XDG_RUNTIME_DIR=/tmp/runtime-braveuser', f'WAYLAND_DISPLAY={display}', 'wl-copy', '--clear',
                    env=HELPER_ENV)

    async def stop(self):
        if self.uid is not None:
            # Keep D-Bus alive until Brave has flushed its profile.
            for pid in self.pids(self.uid):
                with contextlib.suppress(OSError):
                    if Path(f'/proc/{pid}/comm').read_text().strip() == 'brave':
                        os.kill(pid, signal.SIGTERM)
            for _ in range(150):
                if not self.running():
                    break
                await asyncio.sleep(0.1)
            self.send_signal(self.uid, signal.SIGTERM)
            await asyncio.sleep(0.2)
            if self.pids(self.uid):
                self.send_signal(self.uid, signal.SIGKILL)
            if self.process is not None:
                await asyncio.wait_for(self.process.wait(), 10)
            # The supervisor can exit before its audio/compositor descendants.
            # Give signalled processes time to leave the scheduler before deciding
            # cleanup failed. Keep the identity reserved throughout this wait.
            for _ in range(100):
                if not self.pids(self.uid):
                    break
                self.send_signal(self.uid, signal.SIGKILL)
                await asyncio.sleep(0.1)
            if self.pids(self.uid):
                raise RuntimeError('Browser processes remain; admission stays locked')
        await self.clear_shared()
        self.uid = self.process = self.home = None


class Manager:
    def __init__(self, config, browser=None, oidc=None):
        self.config, self.browser = config, browser or Browser()
        self.oidc = oidc or OIDC(config)
        self.state = 'IDLE'
        self.owner = None
        self.flows = {}
        self.lock = asyncio.Lock()
        self.connections = set()
        self.transfers = set()
        self.last_disconnect = time.monotonic()
        self.connected_once = False
        self.http = None
        self.last_update = time.monotonic()
        self.last_recovery = time.monotonic()

    def owns(self, request):
        cookie = request.cookies.get(COOKIE, '')
        return bool(self.owner and cookie and self.owner['origin'] == request['app_origin']
                    and secrets.compare_digest(cookie, self.owner['cookie'])
                    and time.time() < self.owner['expires'])

    def require_owner(self, request):
        if not self.owns(request) or self.state != 'RUNNING':
            raise web.HTTPForbidden(text='No active browser session')

    async def status(self, request):
        own = self.owns(request)
        return web.json_response({'state': self.state, 'owner': own,
            'name': self.owner['name'] if own else '', 'csrf': self.owner['csrf'] if own else ''})

    async def login(self, request):
        if self.owns(request):
            raise web.HTTPFound('/')
        if self.state != 'IDLE':
            raise web.HTTPConflict(text='Browser currently in use')
        now = time.time()
        self.flows = {k: v for k, v in self.flows.items() if v['expires'] > now}
        # Bound the table by dropping the oldest pending flow rather than refusing
        # the request: refusing let anyone deny every login for the flow lifetime
        # with 128 unauthenticated requests. A displaced flow only has to sign in
        # again, and a flow is worthless to anyone without its paired cookie.
        while len(self.flows) >= 128:
            del self.flows[min(self.flows, key=lambda key: self.flows[key]['expires'])]
        metadata = await self.oidc.discovery(self.http)
        state, nonce, verifier, cookie = (secrets.token_urlsafe(32) for _ in range(4))
        self.flows[state] = {'nonce': nonce, 'verifier': verifier, 'cookie': cookie, 'expires': now + 600,
                             'origin': request['app_origin']}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        response = web.HTTPFound(metadata['authorization_endpoint'] + '?' + urlencode({
            'response_type': 'code', 'client_id': self.config.client_id,
            'redirect_uri': request['app_origin'] + '/auth/callback',
            'scope': self.config.scopes, 'state': state, 'nonce': nonce,
            'code_challenge': challenge, 'code_challenge_method': 'S256'}))
        response.set_cookie(FLOW_COOKIE, cookie, secure=True, httponly=True, samesite='Lax', path='/', max_age=600)
        return response

    async def callback(self, request):
        flow = self.flows.pop(request.query.get('state', ''), None)
        if (not flow or flow['expires'] < time.time() or flow['origin'] != request['app_origin'] or
                not secrets.compare_digest(request.cookies.get(FLOW_COOKIE, ''), flow['cookie']) or
                not request.query.get('code') or request.query.get('error')):
            raise web.HTTPBadRequest(text='Invalid or expired login; please sign in again')
        try:
            claims = await self.oidc.authenticate(self.http, request.query['code'], flow)
        except Exception:
            LOG.warning('OIDC verification rejected a login')
            raise web.HTTPForbidden(text='Sign-in could not be verified or access is not allowed') from None
        async with self.lock:
            if self.state != 'IDLE':
                raise web.HTTPConflict(text='Browser currently in use')
            self.state = 'STARTING'
            self.owner = {'cookie': secrets.token_urlsafe(32), 'csrf': secrets.token_urlsafe(32),
                          'origin': flow['origin'],
                          'name': str(claims.get('name') or claims.get('preferred_username') or 'Private browser')[:120],
                          'expires': min(time.time() + self.config.ttl, float(claims['exp']))}
            try:
                await self.browser.start(claims['iss'], claims['sub'])
                self.state = 'RUNNING'
                self.connected_once = False
                self.last_disconnect = time.monotonic()
            except Exception:
                LOG.exception('Browser launch failed')
                await self.stop_locked()
                raise web.HTTPServiceUnavailable(text='Browser could not start; contact the administrator') from None
            response = web.HTTPFound('/')
            response.set_cookie(COOKIE, self.owner['cookie'], secure=True, httponly=True, samesite='Lax', path='/',
                                max_age=max(1, int(self.owner['expires'] - time.time())))
            response.del_cookie(FLOW_COOKIE, path='/', secure=True, httponly=True, samesite='Lax')
            return response

    async def stop_locked(self):
        self.state = 'STOPPING'
        self.owner = None  # Immediately reject all new traffic, including during cleanup.
        # Disconnecting clients is best effort. A socket registered but not yet
        # prepared raises from close(), and losing the browser teardown over that
        # would leave the previous user's profile running behind a closed door.
        results = await asyncio.gather(
            *(ws.close(code=4001, message=b'Session ended') for ws in list(self.connections)),
            return_exceptions=True)
        for error in (result for result in results if isinstance(result, BaseException)):
            LOG.warning('Closing a streaming connection failed: %r', error)
        self.connections.clear()
        tasks = [task for task in self.transfers if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            # The pinned Selkies backend defers display teardown for 3 seconds.
            # Keep admission closed until that and bounded clipboard tasks settle.
            if self.connected_once:
                await asyncio.sleep(4)
            await self.browser.stop()
            self.state = 'IDLE'
        except Exception:
            self.state = 'ERROR'
            self.last_recovery = time.monotonic()
            LOG.exception('Browser cleanup failed; admission remains closed')

    async def logout(self, request):
        if not self.owns(request) or not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), self.owner['csrf']):
            raise web.HTTPForbidden()
        async with self.lock:
            # Recheck after waiting so an old request cannot stop a newer session.
            if not self.owns(request):
                raise web.HTTPForbidden()
            await self.stop_locked()
        response = web.json_response({'ended': self.state == 'IDLE'}, status=200 if self.state == 'IDLE' else 503)
        response.del_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='Lax')
        return response

    async def proxy(self, request):
        self.require_owner(request)
        # No token/control APIs, file server, recording, or secondary streaming modes.
        path = request.path
        # request.path decodes %2e and keeps dot segments, while the outgoing URL
        # resolves them: '/assets/%2e%2e/api/status' would pass this allowlist and
        # then fetch '/api/status'. Reject the segments so both agree on one path.
        if any(segment in ('.', '..') for segment in path.split('/')):
            raise web.HTTPForbidden(text='Endpoint unavailable')
        if path.startswith('/desktop/'):
            path = path[len('/desktop'):]
        allowed = path == '/' or path.startswith(('/assets/', '/src/', '/nginx/')) or path in (
            '/api/websockets', '/api/websockets/', '/favicon.ico', '/icon.png', '/icon-512.png', '/manifest.json')
        private = getattr(self.browser, 'directory', None) is not None
        upload = private and path == '/api/upload' and request.method == 'POST'
        files = private and path.startswith('/api/files/') and request.method == 'GET'
        if not (upload or files or allowed and request.method == 'GET'):
            raise web.HTTPForbidden(text='Endpoint unavailable')
        if upload:
            if request.headers.get('Origin') != request['app_origin']:
                raise web.HTTPForbidden(text='Upload requires same-origin request')
            try:
                upload_offset = int(request.headers.get('X-Upload-Offset', '0'))
            except ValueError:
                raise web.HTTPBadRequest(text='Invalid upload offset') from None
            if upload_offset < 0:
                raise web.HTTPBadRequest(text='Invalid upload offset')
            size = upload_offset + (request.content_length or 0)
            if size > self.config.upload_limit:
                raise web.HTTPRequestEntityTooLarge(max_size=self.config.upload_limit, actual_size=size)
            for value in (request.content_length, request.headers.get('X-Upload-Total')):
                if value is not None:
                    try:
                        size = int(value)
                    except ValueError:
                        raise web.HTTPBadRequest(text='Invalid upload size') from None
                    if size < 0 or size > self.config.upload_limit:
                        raise web.HTTPRequestEntityTooLarge(max_size=self.config.upload_limit, actual_size=size)
        # Build the target from the checked path so no later re-parse can move it.
        target = URL.build(scheme='http', host='127.0.0.1', port=8082, path=path,
                           query_string=request.query_string)
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            # One primary WebSocket per application session; never evict its owner.
            if self.connections:
                raise web.HTTPConflict(text='This session is already open in another tab')
            session = self.owner
            client = web.WebSocketResponse(heartbeat=20, max_msg_size=32 * 1024 * 1024)
            self.connections.add(client)  # Reserve before the first network await.
            try:
                async with self.http.ws_connect(target, heartbeat=20, max_msg_size=32 * 1024 * 1024,
                                                headers={'Origin': session['origin'], 'Host': urlsplit(session['origin']).netloc}) as upstream:
                    if self.owner is not session or self.state != 'RUNNING':
                        raise web.HTTPForbidden()
                    await client.prepare(request)
                    self.connected_once = True
                    async def relay(source, destination):
                        async for message in source:
                            if message.type == WSMsgType.BINARY:
                                await destination.send_bytes(message.data)
                            elif message.type == WSMsgType.TEXT:
                                await destination.send_str(message.data)
                            else:
                                break
                    tasks = [asyncio.create_task(relay(client, upstream)), asyncio.create_task(relay(upstream, client))]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                self.connections.discard(client)
                if self.owner is session:
                    self.last_disconnect = time.monotonic()
                await client.close()
            return client
        task = asyncio.current_task()
        self.transfers.add(task)
        try:
            async def body():
                total = upload_offset
                async for chunk in request.content.iter_chunked(65536):
                    self.require_owner(request)
                    total += len(chunk)
                    if total > self.config.upload_limit:
                        raise web.HTTPRequestEntityTooLarge(max_size=self.config.upload_limit, actual_size=total)
                    yield chunk
            headers = {key: value for key, value in request.headers.items()
                       if key.lower().startswith('x-upload-') or key.lower() == 'content-type'} if upload else {}
            async with self.http.request(request.method, target, data=body() if upload else None,
                                         headers=headers, allow_redirects=False) as upstream:
                response = web.StreamResponse(status=upstream.status,
                    headers={k:v for k,v in upstream.headers.items() if k.lower() not in HOP and k.lower() not in ('content-encoding', 'cache-control')})
                response.headers['Cache-Control'] = 'no-store'
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    # Headers are already sent, so a revoked session ends the body
                    # here. Raising instead would splice an error page into it.
                    if not self.owns(request) or self.state != 'RUNNING':
                        break
                    await response.write(chunk)
                return response
        finally:
            self.transfers.discard(task)

    async def monitor(self):
        while True:
            await asyncio.sleep(1)
            update = False
            async with self.lock:
                if self.state == 'RUNNING':
                    grace = self.config.grace if self.connected_once else self.config.start_timeout
                    expired = time.time() >= self.owner['expires']
                    disconnected = not self.connections and time.monotonic() - self.last_disconnect >= grace
                    if expired or disconnected or not self.browser.running():
                        await self.stop_locked()
                elif self.state == 'ERROR' and time.monotonic() - self.last_recovery >= 30:
                    await self.retry_locked()
                elif self.state == 'IDLE' and self.config.auto_update and time.monotonic() - self.last_update >= self.config.update_interval:
                    # Claim the slot, then release the lock: an update runs for
                    # minutes, and holding it would stall a login's callback
                    # instead of refusing it outright.
                    self.state = 'UPDATING'
                    update = True
            if update:
                await self.update()

    async def retry_locked(self):
        """Reconcile a failed cleanup so a transient fault is not a permanent outage."""
        self.last_recovery = time.monotonic()
        try:
            await self.browser.stop()
            await self.browser.recover()
            status = await self.browser.command(
                'dpkg-query', '-W', '-f=${db:Status-Status}', 'brave-origin', timeout=30)
            if status != 'installed':
                LOG.warning('Admission stays closed; browser package status is %s', status)
                return
        except Exception as error:
            LOG.warning('Admission stays closed; recovery did not settle: %r', error)
            return
        LOG.info('Reconciled after a failed cleanup; admission reopens')
        self.state = 'IDLE'

    async def update(self):
        try:
            await self.browser.command('/usr/local/bin/update-brave.sh', timeout=900)
            state = 'IDLE'
        except Exception:
            LOG.exception('Idle browser update failed')
            # A refused or failed download leaves the installed browser intact and
            # must not close admission. Only an interrupted package transaction,
            # which leaves dpkg mid-flight, is worth locking the appliance for.
            try:
                status = await self.browser.command(
                    'dpkg-query', '-W', '-f=${db:Status-Status}', 'brave-origin', timeout=30)
                state = 'IDLE' if status == 'installed' else 'ERROR'
            except Exception:
                state = 'ERROR'
            if state == 'ERROR':
                LOG.error('Browser package is not fully installed; admission remains closed')
        async with self.lock:
            # Nothing else may claim UPDATING, so this is still our transition.
            self.state = state
            if state == 'ERROR':
                self.last_recovery = time.monotonic()
            self.last_update = time.monotonic()

    async def lifecycle(self, app):
        self.http = ClientSession(timeout=ClientTimeout(total=None, connect=10, sock_read=30))
        await self.browser.recover()
        task = asyncio.create_task(self.monitor())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            async with self.lock:
                await self.stop_locked()
            await self.http.close()

    def app(self):
        @web.middleware
        async def security(request, handler):
            if request.path != '/health':
                # nginx preserves the incoming Host. Never trust forwarded host/proto
                # headers supplied by a client; the public application requires HTTPS.
                host = request.headers.get('Host', '')
                if not re.fullmatch(r'(?:[A-Za-z0-9.-]+|\[[A-Fa-f0-9:]+\])(?::[0-9]{1,5})?', host):
                    raise web.HTTPBadRequest(text='Invalid host')
                try:
                    parsed = urlsplit('https://' + host)
                    port = parsed.port
                    if port == 0:
                        raise ValueError('Invalid port')
                except ValueError:
                    raise web.HTTPBadRequest(text='Invalid host') from None
                app_origin = 'https://' + host.lower()
                request['app_origin'] = app_origin
                origin = request.headers.get('Origin')
                if origin and origin != request['app_origin']:
                    raise web.HTTPForbidden(text='Invalid origin')
            try:
                response = await handler(request)
            except web.HTTPException as exception:
                response = exception
            except Exception:
                LOG.exception('Request failed')
                response = web.Response(status=503, text='Service temporarily unavailable')
            response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "frame-ancestors 'self'"})
            return response
        app = web.Application(middlewares=[security], client_max_size=1024*1024)
        app.cleanup_ctx.append(self.lifecycle)
        async def portal(request):
            return web.FileResponse('/usr/local/share/brave-origin/portal.html')
        async def script(request):
            return web.FileResponse('/usr/local/share/brave-origin/portal.js')
        async def health(request):
            return web.json_response({'state': self.state}, status=503 if self.state == 'ERROR' else 200)
        app.add_routes([web.get('/', portal), web.get('/portal.js', script), web.get('/health', health),
                        web.get('/session/status', self.status), web.get('/auth/login', self.login),
                        web.get('/auth/callback', self.callback), web.post('/auth/logout', self.logout),
                        web.route('*', '/{path:.*}', self.proxy)])
        return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    # No access log: authorization codes and stream parameters are not log data.
    web.run_app(Manager(Config()).app(), host='127.0.0.1', port=8084, access_log=None, shutdown_timeout=25)
