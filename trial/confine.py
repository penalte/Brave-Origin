#!/usr/bin/env python3
"""Trial: run a command that can only see its own storage, using Landlock.

confine.py STORAGE SCRATCH -- command...
Needs no privilege and no mount, so it works under Docker's default AppArmor.
"""
import ctypes
import os
import sys

CREATE, ADD, RESTRICT = 444, 445, 446
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1, 2, 4, 8
libc = ctypes.CDLL(None, use_errno=True)
abi = libc.syscall(CREATE, None, 0, 1)
if abi < 1:
    sys.exit(f'Landlock unavailable: {os.strerror(ctypes.get_errno())}')
handled = (1 << 13) - 1
if abi >= 2: handled |= 1 << 13
if abi >= 3: handled |= 1 << 14
if abi >= 5: handled |= 1 << 15
FILE_ONLY = EXECUTE | WRITE_FILE | READ_FILE | (handled & (1 << 14 | 1 << 15))
READ = READ_FILE | READ_DIR
WRITE = handled & ~EXECUTE

storage, scratch = sys.argv[1], sys.argv[2]
command = sys.argv[sys.argv.index('--') + 1:]
ruleset = libc.syscall(CREATE, ctypes.byref(ctypes.c_uint64(handled)), 8, 0)
if ruleset < 0:
    sys.exit(f'Landlock ruleset failed: {os.strerror(ctypes.get_errno())}')


class Rule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('access', ctypes.c_uint64), ('fd', ctypes.c_int32)]


def allow(path, access):
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return
    if not os.path.isdir(path):
        access &= FILE_ONLY
    if libc.syscall(ADD, ruleset, 1, ctypes.byref(Rule(access, fd)), 0):
        sys.exit(f'Landlock rule failed for {path}: {os.strerror(ctypes.get_errno())}')
    os.close(fd)


allow('/usr', READ | EXECUTE)
for path in ('/etc/fonts', '/etc/ld.so.cache', '/etc/gtk-3.0', '/etc/gtk-4.0', '/etc/alternatives',
             '/etc/pulse', '/etc/machine-id', '/etc/localtime', '/etc/nsswitch.conf', '/etc/dbus-1',
             '/var/cache/fontconfig', '/var/lib/dbus', '/proc', '/sys/devices/system/cpu'):
    allow(path, READ)
allow('/dev', READ | WRITE)
allow(storage, READ | WRITE)
allow(scratch, READ | WRITE)
libc.prctl(38, 1, 0, 0, 0)
if libc.syscall(RESTRICT, ruleset, 0):
    sys.exit(f'Landlock restrict failed: {os.strerror(ctypes.get_errno())}')
print(f'[confine] Landlock version {abi} active', file=sys.stderr, flush=True)
os.execvp(command[0], command)
