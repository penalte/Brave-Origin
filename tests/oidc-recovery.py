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


async def main():
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
