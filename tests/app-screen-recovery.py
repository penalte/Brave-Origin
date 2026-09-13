"""Run the actual patched Selkies methods with a simulated compositor."""
import ast
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

source = Path(sys.argv.pop(1)).read_text()
names = {'_size_wayland_screen', '_configure_app_screen', '_settle_app_screen', '_display_dpi', 'stop'}
owner = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef)
             and any(isinstance(m, ast.AsyncFunctionDef) and m.name == '_size_wayland_screen' for m in n.body))
methods = [n for n in owner.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
assert {n.name for n in methods} == names
namespace = dict(asyncio=asyncio, app_settings=SimpleNamespace(app_wayland_display='wayland-0', scaling_dpi=96),
                 data_logger=logging.getLogger('test'), WAYLAND_SCREEN_OUTPUT_ID=0)
module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
exec(compile(ast.fix_missing_locations(module), '<patched-selkies>', 'exec'), namespace)

class Compositor:
    def __init__(self): self.calls = []
    def resize_output(self, *args): return True
    def list_outputs(self): return []
    def set_app_screen_geometry(self, *args):
        self.calls.append(args)
        return True
    def list_app_screens(self, display): return [('WL-1', 0, 0, *self.calls[-1][1:3])]

class Recovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.obj = namespace['Subject']()
        self.backend = Compositor()
        self.obj._wayland_control_module = lambda: self.backend
        self.obj.display_clients = {'primary': {'scale': 1.0, 'scaling_dpi': 96}}
        self.obj.shutdown_event = asyncio.Event()
        self.guard = asyncio.Lock()
        @asynccontextmanager
        async def guard():
            async with self.guard: yield
        self.obj._reconfigure_guard = guard
    async def asyncTearDown(self): await self.obj.stop()
    async def test_latest_resize_and_dpi_after_settle_delay(self):
        await self.obj._size_wayland_screen(1910, 912)
        task = self.obj._app_screen_settle
        await self.obj._size_wayland_screen(1280, 720)
        self.obj.display_clients['primary']['scaling_dpi'] = 144
        await task
        self.assertEqual(self.backend.calls[-1], ('wayland-0',1280,720,1.5,0))
        self.assertIs(task, self.obj._app_screen_settle)
        self.assertEqual(len(self.backend.calls),3)
    async def test_shutdown_wakes_timer_without_recovery(self):
        await self.obj._size_wayland_screen(1024,768)
        await asyncio.wait_for(self.obj.stop(),1)
        self.assertEqual(len(self.backend.calls),1)
    async def test_disconnect_skips_recovery(self):
        await self.obj._size_wayland_screen(1024,768)
        self.obj.display_clients.clear()
        await self.obj._app_screen_settle
        self.assertEqual(len(self.backend.calls),1)
    async def test_reconnect_uses_replacement_client_dpi(self):
        await self.obj._size_wayland_screen(1024,768)
        self.obj.display_clients = {'primary': {'scale':1,'scaling_dpi':192}}
        await self.obj._size_wayland_screen(1600,900)
        await self.obj._app_screen_settle
        self.assertEqual(self.backend.calls[-1],('wayland-0',1600,900,2.0,0))
    async def test_recovery_serialized_with_resize(self):
        await self.guard.acquire()
        await self.obj._size_wayland_screen(1024,768)
        await asyncio.sleep(5.1)
        self.assertEqual(len(self.backend.calls),1)
        await self.obj._size_wayland_screen(1280,720)
        self.guard.release()
        await self.obj._app_screen_settle
        self.assertEqual(self.backend.calls[-1][1:3],(1280,720))

unittest.main()
