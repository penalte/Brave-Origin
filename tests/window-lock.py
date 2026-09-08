"""Exercise window state requests against Labwc in a disposable container.

Use the standard wlr foreign-toplevel protocol with only Python's standard
library. The same requests must work with --unlocked, proving that the test
actually reaches the compositor rather than checking a configuration file.
"""
import os
from pathlib import Path
import select
import socket
import struct
import sys
import time


def uint(value):
    return struct.pack('=I', value)


def string(value):
    data = value.encode() + b'\0'
    return uint(len(data)) + data + b'\0' * (-len(data) % 4)


def read_string(data):
    length = struct.unpack_from('=I', data)[0]
    return data[4:4 + length - 1].decode(), 4 + (length + 3) // 4 * 4


class Windows:
    def __init__(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp/runtime-braveuser'))
        self.socket.connect(str(runtime / os.environ['WAYLAND_DISPLAY']))
        self.buffer = b''
        self.handles = {}
        self.manager = None
        self.native_configures = 0
        self.next_id = 3
        self.wm = self.compositor = self.native_surface = None
        self.send(1, 1, uint(2))  # wl_display.get_registry
        self.pump(1)
        assert self.manager, 'Labwc foreign-toplevel manager is missing'

    def allocate(self):
        identifier = self.next_id
        self.next_id += 1
        return identifier

    def send(self, target, opcode, data=b''):
        self.socket.sendall(uint(target) + uint((len(data) + 8) << 16 | opcode) + data)

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while (remaining := end - time.monotonic()) > 0:
            if not select.select([self.socket], [], [], remaining)[0]:
                break
            chunk = self.socket.recv(65536)
            assert chunk, 'Compositor disconnected'
            self.buffer += chunk
            while len(self.buffer) >= 8:
                target, header = struct.unpack_from('=II', self.buffer)
                size, opcode = header >> 16, header & 65535
                assert size >= 8 and size % 4 == 0
                if len(self.buffer) < size:
                    break
                data = self.buffer[8:size]
                self.buffer = self.buffer[size:]
                self.event(target, opcode, data)

    def event(self, target, opcode, data):
        if target == 1 and opcode == 0:
            raise AssertionError(f'Wayland protocol error: {data!r}')
        if target == 2 and opcode == 0:
            name = struct.unpack_from('=I', data)[0]
            interface, offset = read_string(data[4:])
            version = struct.unpack_from('=I', data, 4 + offset)[0]
            if interface == 'zwlr_foreign_toplevel_manager_v1':
                self.manager = self.allocate()
                self.send(2, 0, uint(name) + string(interface) + uint(min(version, 3)) + uint(self.manager))
            elif interface in ('xdg_wm_base', 'wl_compositor'):
                identifier = self.allocate()
                if interface == 'xdg_wm_base':
                    self.wm = identifier
                else:
                    self.compositor = identifier
                self.send(2, 0, uint(name) + string(interface) + uint(min(version, 3)) + uint(identifier))
        elif target == self.wm and opcode == 0:  # xdg_wm_base.ping
            self.send(self.wm, 3, data)
        elif target == self.native_surface and opcode == 0:  # xdg_surface.configure
            self.native_configures += 1
            self.send(self.native_surface, 4, data)  # ack_configure
        elif target == self.manager and opcode == 0:
            self.handles[struct.unpack_from('=I', data)[0]] = {}
        elif target in self.handles:
            window = self.handles[target]
            if opcode == 1:
                window['app_id'] = read_string(data)[0]
            elif opcode == 4:
                size = struct.unpack_from('=I', data)[0]
                window['state'] = set(struct.unpack(f'={size // 4}I', data[4:4 + size]))
            elif opcode == 6:
                del self.handles[target]


unlocked = '--unlocked' in sys.argv
windows = Windows()
matches = [handle for handle, info in windows.handles.items()
           if 'brave' in info.get('app_id', '').lower() and 0 in info.get('state', set())]
assert matches, f'No maximized Brave window: {windows.handles}'
handle = matches[0]


def state():
    return windows.handles[handle]['state']


try:
    for opcode, flag, label in ((1, 0, 'restore'), (2, 1, 'minimize')):
        windows.send(handle, opcode)
        windows.pump(0.8)
        if unlocked:
            assert (flag in state()) == (opcode == 2), (label, state())
        else:
            assert 0 in state() and 1 not in state(), (label, state())
        windows.send(handle, 3)  # unset_minimized
        windows.send(handle, 0)  # set_maximized
        windows.pump(0.5)
        assert 0 in state() and 1 not in state()
        print(f'{label}: {"allowed" if unlocked else "blocked"}', flush=True)

    # Fullscreen content must return to a maximized browser afterward.
    windows.send(handle, 8, uint(0))
    windows.pump(0.8)
    assert 3 in state(), state()
    windows.send(handle, 9)
    windows.pump(0.8)
    assert 0 in state() and not state() & {1, 3}, state()
    print('Fullscreen enter/exit preserves maximization.', flush=True)

    if not unlocked:
        # A synthetic native client verifies the reply to set_minimized.
        # State-only foreign requests cannot catch Chromium retaining its own
        # minimized state after a compositor silently rejects that request.
        surface = windows.allocate()
        windows.native_surface = windows.allocate()
        toplevel = windows.allocate()
        windows.send(windows.compositor, 0, uint(surface))  # wl_compositor.create_surface
        windows.send(windows.wm, 2, uint(windows.native_surface) + uint(surface))  # get_xdg_surface
        windows.send(windows.native_surface, 1, uint(toplevel))  # get_toplevel
        windows.send(toplevel, 3, string('window-lock-protocol-test'))
        windows.send(surface, 6)  # initial empty surface commit
        windows.pump(0.5)
        assert windows.native_configures > 0, 'Missing initial configure'
        before = windows.native_configures
        windows.send(toplevel, 13)  # xdg_toplevel.set_minimized
        windows.pump(0.5)
        assert windows.native_configures > before, 'Minimize rejection must send a configure reply'
        windows.send(toplevel, 0)
        windows.send(windows.native_surface, 0)
        windows.send(surface, 0)
        print('Native minimize rejection sends a configure reply.', flush=True)
finally:
    if handle in windows.handles:
        windows.send(handle, 9)
        windows.send(handle, 3)
        windows.send(handle, 0)
        windows.pump(0.3)
    windows.socket.close()
