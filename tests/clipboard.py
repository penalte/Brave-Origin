"""Exercise the installed clipboard implementation against the real compositor."""
import asyncio
import os
import subprocess
from selkies.input_handler import WebRTCInput
from pixelflux import ScreenCapture

async def main():
    handler = WebRTCInput.__new__(WebRTCInput)
    handler.is_wayland = True
    handler.wayland_input = ScreenCapture()  # Exercise the installed native data-control ABI.
    handler._app_wl_display_cached = "wayland-0"
    handler._app_watch_failure = None
    handler._app_clip_read_failure = None
    handler._app_wayland_display = lambda: 'wayland-0'
    handler._has_separate_app_compositor = lambda: True
    env = dict(os.environ, WAYLAND_DISPLAY='wayland-0')
    for value in ('Clipboard round trip', 'Unicode café 中文 🎉\nsecond line\n', 'x' * 131072):
        assert await handler.write_clipboard(value), 'Client-to-session write failed'
        result = subprocess.check_output(['wl-paste', '--no-newline', '--type', 'text'], env=env).decode()
        assert result == value, 'Client-to-session bytes changed'
        result, mime = await handler._app_clipboard_read(False)
        assert (result, mime) == (value, 'text/plain'), 'Session-to-client bytes changed'
    subprocess.run(['wl-copy', '--type', 'text/plain'], input=b'Plain MIME selection', env=env, check=True)
    result, mime = await handler._app_clipboard_read(False)
    assert (result, mime) == ('Plain MIME selection', 'text/plain'), 'Plain text MIME is unsupported'
    import base64
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
    assert await handler.write_clipboard(png, 'image/png')
    assert await handler._app_clipboard_read(True) == (png, 'image/png')

    # Exercise actual outbound polling, consumer gating, and echo suppression.
    handler.enable_clipboard = 'true'
    handler.enable_binary_clipboard = 'true'
    handler._clipboard_monitor_active = False
    handler._clipboard_last_bytes = None
    consumers = False
    async def until(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(.05)
    broadcasts = []
    async def no_x11(): return None
    async def broadcast(data, mime): broadcasts.append((data, mime))
    handler._ensure_x11_clipboard_monitor_async = no_x11
    handler._clipboard_has_consumers = lambda: consumers
    handler.on_clipboard_read = broadcast
    monitor = asyncio.create_task(handler.start_clipboard())
    try:
        await asyncio.sleep(1.2)
        assert broadcasts == [], 'Clipboard broadcast without a connected client'
        consumers = True
        await until(lambda: bool(broadcasts))
        assert broadcasts == [(png, 'image/png')], 'Selection not delivered on connection'
        await handler.write_clipboard('Incoming clipboard')
        await asyncio.sleep(1.2)
        assert len(broadcasts) == 1, 'Incoming clipboard echoed back to client'
        subprocess.run(['wl-copy'], input=b'Remote copy', env=env, check=True)
        await until(lambda: broadcasts[-1] == ('Remote copy', 'text/plain'))
        assert broadcasts[-1] == ('Remote copy', 'text/plain'), 'Remote copy was not delivered'
        count = len(broadcasts)
        await asyncio.sleep(1.2)
        assert len(broadcasts) == count, 'Unchanged clipboard was sent again'
    finally:
        handler.clipboard_running = False
        monitor.cancel()
        try: await monitor
        except asyncio.CancelledError: pass
    print('Clipboard passed: text, Unicode, multiline, large data, PNG, polling, consumer gating, no echoes.')

asyncio.run(main())
