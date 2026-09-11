"""Failed installations must stay closed until package health is restored."""
import asyncio
import importlib.util

spec = importlib.util.spec_from_file_location('manager', '/usr/local/bin/session-manager.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Browser:
    status = 'half-configured'

    async def command(self, *args, **kwargs):
        if args[0] == 'dpkg-query':
            if self.status is None:
                raise RuntimeError('Package database unavailable')
            return self.status
        raise RuntimeError('Interrupted update')

    async def stop(self):
        pass

    async def recover(self):
        pass


def check_policies():
    blocked = m.policies('/home/someone/Downloads', {})
    # Each of these opens a browsing context outside the managed profile.
    assert blocked['IncognitoModeAvailability'] == 1
    assert blocked['TorDisabled'] is True
    assert blocked['BrowserGuestModeEnabled'] is False
    assert blocked['BrowserAddPersonEnabled'] is False
    # Casting reaches the host's network from inside a remote session.
    assert blocked['EnableMediaRouter'] is False
    assert blocked['ShowCastIconInToolbar'] is False
    assert blocked['DownloadDirectory'] == '/home/someone/Downloads'
    extended = m.policies('/home/someone/Downloads',
                          {'BROWSER_POLICY': '{"SyncDisabled": true, "DownloadDirectory": "/etc"}'})
    assert extended['SyncDisabled'] is True, 'Operator policy was not merged'
    assert extended['DownloadDirectory'] == '/home/someone/Downloads', 'Download directory was overridden'
    assert extended['IncognitoModeAvailability'] == 1
    # An operator may deliberately relax a block, but never the private path.
    relaxed = m.policies('/home/someone/Downloads', {'BROWSER_POLICY': '{"IncognitoModeAvailability": 0}'})
    assert relaxed['IncognitoModeAvailability'] == 0
    for broken in ('{"unclosed": ', '[]', '"text"', '3'):
        try:
            m.policies('/home/someone/Downloads', {'BROWSER_POLICY': broken})
            raise AssertionError(f'Accepted invalid BROWSER_POLICY {broken!r}')
        except ValueError:
            pass
    print('PASS: managed policy blocks private windows, keeps the private download path and rejects bad input')


async def main():
    check_policies()
    browser = Browser()
    manager = m.Manager(object(), browser=browser)
    await manager.update()
    assert manager.state == 'ERROR'
    for status in ('half-configured', 'unpacked', '', None):
        browser.status = status
        await manager.retry_locked()
        assert manager.state == 'ERROR', status
    browser.status = 'installed'
    await manager.retry_locked()
    assert manager.state == 'IDLE'
    # A failed download with an intact installed browser can reopen admission.
    await manager.update()
    assert manager.state == 'IDLE'
    print('PASS: failed installation stays closed until package status is installed')


asyncio.run(main())
