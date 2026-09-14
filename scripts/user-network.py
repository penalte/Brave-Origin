"""Root-owned per-profile routing records, relay workers and UID firewall grants."""
import asyncio
import errno
import importlib.util
import json
import os
import pwd
from pathlib import Path
import re
import socket
import time

def module(name, file):
    spec = importlib.util.spec_from_file_location(name, '/usr/local/bin/'+file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

relay = module('relay', 'browser-relay.py')
network = module('network', 'browser-network.py')
USERS = Path('/config/users')
FORCE = Path('/config/network-policy.json')
# Commands run as a desktop account get only this environment; the broker's
# own environment is not for that account to read back out.
HELPER_ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
        raise RuntimeError('Unsafe routing metadata')
    return json.loads(path.read_text())


def save(path, data):
    if path.exists() or path.is_symlink():
        read(path)
    temporary = path.parent / ('.network-'+os.urandom(12).hex())
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def metadata(key):
    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
        raise ValueError('Invalid user')
    directory = USERS / key
    if directory.is_symlink() or directory.stat().st_uid != 0:
        raise ValueError('Unsafe user directory')
    path = directory / 'identity.json'
    record = read(path)
    if record.get('key') != key:
        raise ValueError('Identity mismatch')
    return path, record


def forced():
    return bool(read(FORCE).get('force_warp', False)) if FORCE.exists() else False


def register(key, name):
    path, data = metadata(key)
    data.update(name=name, last_login=time.time())
    data.setdefault('allow_direct', False)
    data.setdefault('warp_enabled', os.environ.get('WARP_ENABLED') == 'true')
    save(path, data)
    return data


async def nft(*arguments):
    process = await asyncio.create_subprocess_exec('nft', *arguments,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, error = await process.communicate()
    if process.returncode:
        raise RuntimeError('Could not enforce private routing: '+error.decode()[:200])


async def recover():
    # Called only after the broker has stopped every old desktop UID. Never
    # leave a stale UID/port grant that a later session could inherit by reuse.
    await asyncio.to_thread(network.install_firewall, network.configuration())
    root = Path('/run/brave-network/users')
    if root.exists():
        for directory in root.iterdir():
            if (directory.is_symlink() or directory.stat().st_uid != 0 or
                not re.fullmatch('[0-9a-f]{64}', directory.name)):
                raise RuntimeError('Unsafe relay runtime directory')
            for name in ('config.json','config.tmp','port'):
                (directory/name).unlink(missing_ok=True)
            directory.rmdir()


class UserProxy:
    def __init__(self, key, uid):
        self.key, self.uid = key, uid
        self.port = None
        self.manager = None
        self.granted = False
        self.state = Path('/run/brave-network/users') / key

    async def start(self):
        _, data = metadata(self.key)
        mode = 'warp' if forced() or data.get('warp_enabled', os.environ.get('WARP_ENABLED') == 'true') else 'direct'
        network.configuration(dict(os.environ, WARP_ENABLED=str(mode == 'warp').lower()))
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        network.write_json(self.state / 'config.json', {'mode':mode})
        listener = socket.socket()
        for port in relay.USER_PORTS:
            try:
                listener.bind(('127.0.0.1', port))
                break
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    listener.close()
                    raise
        else:
            listener.close()
            raise RuntimeError('No private proxy port available')
        listener.listen(128)
        listener.setblocking(False)
        self.port = listener.getsockname()[1]
        (self.state / 'port').write_text(str(self.port))
        self.manager = relay.Controller(listener, mode, self.state)
        try:
            await self.manager.start_worker()
            await nft('add', 'element', 'inet', 'brave_egress', 'clients',
                      '{', str(self.uid), '.', str(self.port), '}')
            self.granted = True
        except BaseException:
            await self.stop()
            raise

    async def wait_ready(self):
        """Verify WARP through this user's relay and UID firewall grant."""
        if self.manager.mode == 'direct':
            if self.manager.worker.returncode is not None:
                raise RuntimeError('The direct proxy stopped during startup')
            return
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            process = await asyncio.create_subprocess_exec(
                '/usr/sbin/runuser', '-u', pwd.getpwuid(self.uid).pw_name, '--',
                'curl', '--disable', '--silent', '--fail', '--max-time', '5',
                '--noproxy', '', '--proxy', f'socks5h://127.0.0.1:{self.port}',
                'https://www.cloudflare.com/cdn-cgi/trace',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=HELPER_ENV)
            try:
                output, _ = await process.communicate()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode == 0 and any(line in (b'warp=on', b'warp=plus') for line in output.splitlines()):
                return
            await asyncio.sleep(.5)
        raise RuntimeError('WARP is not ready. Please retry shortly.')

    async def switch(self, enabled):
        network.configuration(dict(os.environ, WARP_ENABLED=str(enabled).lower()))
        await self.manager.switch('warp' if enabled else 'direct')

    async def stop(self):
        if self.manager:
            await self.manager.stop_worker()
            if self.granted:
                # Keep the listening port reserved until its grant is removed.
                # A failed firewall cleanup must not expose a reused port.
                await nft('delete','element','inet','brave_egress','clients',
                          '{',str(self.uid),'.',str(self.port),'}')
                self.granted = False
            self.manager.listener.close()
            self.manager = None
        (self.state / 'config.json').unlink(missing_ok=True)
        (self.state / 'port').unlink(missing_ok=True)
        if self.state.exists():
            self.state.rmdir()

    def status(self, admin=False):
        _, data = metadata(self.key)
        force = forced()
        mode = self.manager.mode
        health = network.STATE / 'status.json'
        status = json.loads(health.read_text()) if health.exists() else {}
        alive = self.manager.worker.returncode is None
        state = 'direct' if mode == 'direct' and alive else 'unavailable'
        if mode == 'warp' and alive and status.get('mode') == 'warp' and time.time()-status.get('checked_at',0)<45:
            state = status.get('state','unavailable')
        return {'mode':mode, 'state':state, 'can_toggle':not force and (admin or data.get('allow_direct',False)),
                'forced':force}

    async def recover(self):
        if forced() and self.manager.mode != 'warp':
            await self.switch(True)
            return
        async with self.manager.lock:
            if self.manager.worker.returncode is not None:
                await self.manager.start_worker()
