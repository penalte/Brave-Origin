#!/usr/bin/env python3
"""Prepare persistent storage without following links supplied by its contents."""
import fcntl
import os
import re
import stat
import sys


def identity(name):
    value = os.environ.get(name, "1000")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 2147483647:
        raise ValueError(f"{name} must be a nonzero user ID.")
    return int(value)


def directory(path, uid, gid, mode):
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        pass
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.fchown(fd, uid, gid)
    os.fchmod(fd, mode)
    os.close(fd)


def regular(path, uid, gid, mode, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            return None
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, mode)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise ValueError(f"Unsafe storage file: {path}")
    os.fchown(fd, uid, gid)
    os.fchmod(fd, mode)
    return fd


def repair_tree(path, uid, gid):
    # fwalk holds directory descriptors; a renamed directory cannot redirect us.
    for _, _, files, rootfd in os.fwalk(path, follow_symlinks=False):
        os.fchown(rootfd, uid, gid)
        for name in files:
            info = os.stat(name, dir_fd=rootfd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=rootfd)
            try:
                if os.fstat(fd).st_nlink != 1:
                    raise ValueError(f"Hard-linked storage file: {path}/{name}")
                os.fchown(fd, uid, gid)
            finally:
                os.close(fd)


def prepare():
    uid, gid = identity("PUID"), identity("PGID")
    # Root owns the mount root so the browser cannot replace trusted directories.
    directory("/config", 0, 0, 0o755)
    # Sticky ownership protects root-owned locks while allowing browser state.
    directory("/config/state", 0, gid, 0o1770)
    lock = regular("/config/state/instance.lock", 0, 0, 0o600, True)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("Another container is using this /config directory.") from None
    os.dup2(lock, 7, inheritable=True)
    if lock != 7:
        os.close(lock)
    fd = regular("/config/state/profile.lock", 0, gid, 0o660, True)
    os.close(fd)
    writable = ("profile", "downloads", ".config", ".cache", ".local", ".pki", ".vnc", ".dbus", ".nv")
    owner = regular("/config/.owner", 0, 0, 0o600, True)
    expected = f"{uid}:{gid}\n".encode()
    repair = os.read(owner, 64) != expected
    for name in writable:
        path = "/config/" + name
        directory(path, uid, gid, 0o700)
        if repair:
            repair_tree(path, uid, gid)
    if repair:
        # State files are writable, except the two root-owned lifetime locks.
        with os.scandir("/config/state") as entries:
            for entry in entries:
                if entry.name not in ("instance.lock", "profile.lock") and entry.is_file(follow_symlinks=False):
                    fd = regular(entry.path, uid, gid, 0o600)
                    os.close(fd)
        os.lseek(owner, 0, os.SEEK_SET)
        os.write(owner, expected)
        os.ftruncate(owner, len(expected))
    os.close(owner)
    for path, mode in (("/config/ssl", 0o700), ("/config/kasmvnc", 0o755)):
        directory(path, 0, 0, mode)
    for path, gid_, mode in (("/config/.passwd", 33, 0o640), ("/config/ssl/cert.pem", 0, 0o644), ("/config/ssl/cert.key", 0, 0o600), ("/config/kasmvnc/kasmvnc.yaml", 0, 0o644)):
        fd = regular(path, 0, gid_, mode)
        if fd is not None:
            os.close(fd)
    directory("/run/lock", 0, 0, 0o755)
    directory("/run/brave-origin", 0, 0, 0o755)
    for path in ("/tmp/runtime-braveuser", "/tmp/brave-cache"):
        directory(path, uid, gid, 0o700)
    os.environ["BRAVE_STORAGE_READY"] = "1"
    os.execv("/usr/local/bin/entrypoint.sh", ["/usr/local/bin/entrypoint.sh", *sys.argv[1:]])


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError) as error:
        sys.exit(f"Storage initialization refused: {error}")
