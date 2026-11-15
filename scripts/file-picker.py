#!/usr/bin/python3
"""Private FileChooser portal. The UI only traverses a no-symlink Downloads tree."""
import fnmatch
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import struct
import subprocess
import errno
import sys
import time

# What the preview pane can draw, by file extension.
IMAGE_TYPES = frozenset('jpg jpeg jfif png apng gif bmp ico webp avif svg tif tiff'.split())
VIDEO_TYPES = frozenset('mp4 m4v webm ogv mkv mov avi wmv flv mpg mpeg 3gp ts'.split())
AUDIO_TYPES = frozenset('mp3 wav ogg oga opus flac m4a aac weba wma'.split())
TEXT_TYPES = frozenset('txt text log md json xml html htm css js csv yaml yml ini conf py sh srt'.split())


def kind(filename):
    suffix = Path(filename).suffix.lower().lstrip('.')
    return ('image' if suffix in IMAGE_TYPES else 'pdf' if suffix == 'pdf' else 'video' if suffix in VIDEO_TYPES
            else 'audio' if suffix in AUDIO_TYPES else 'text' if suffix in TEXT_TYPES else 'other')


# What "Open in Brave" does with a file. The gateway applies the same lists.
# Formats the browser displays itself; anything else it would only download again.
BROWSER_TYPES = frozenset('''jpg jpeg jfif png apng gif webp avif svg bmp ico
mp4 m4v webm ogv mkv mov mp3 wav ogg oga opus flac m4a aac weba
pdf txt text log md json xml html htm css js yaml yml ini conf'''.split())
# Formats shown by an ONLYOFFICE Docs server, by its document type.
OFFICE_TYPES = {suffix: sort for sort, suffixes in {
    'word': 'doc docx docm dot dotx odt ott rtf',
    'cell': 'xls xlsx xlsm xlt xltx ods ots csv',
    'slide': 'ppt pptx pptm pps ppsx pot potx odp otp'}.items() for suffix in suffixes.split()}


def openable(filename, office):
    """None when Open in Brave can show this file, otherwise the reason it cannot."""
    suffix = Path(filename).suffix.lower().lstrip('.')
    if suffix in OFFICE_TYPES:
        return None if office else ('Office documents need a document server, and none is configured '
                                    '(OFFICE_SERVER_URL). Download the file to open it on your device.')
    if suffix in BROWSER_TYPES:
        return None
    return "Brave can't display this type of file. Download it to open it on your device instead."



def valid(name):
    """A plain, visible entry name: no path, no traversal, no dotfile."""
    return bool(name) and len(name.encode()) <= 255 and not name.startswith('.') and not any(
        bad in name for bad in ('/', '\\', '\x00'))


class Files:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def open(self, parts, directory=False):
        fd = os.dup(self.fd)
        try:
            for index, name in enumerate(parts):
                if not name or name in ('.', '..') or '/' in name or '\\' in name or '\x00' in name:
                    raise ValueError('Invalid name')
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if directory or index < len(parts)-1:
                    flags |= os.O_DIRECTORY
                nxt = os.open(name, flags, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return fd
        except Exception:
            os.close(fd)
            raise

    def entries(self, parts):
        fd = self.open(parts, directory=True)
        try:
            result = []
            for name in os.listdir(fd):
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    # Dotfiles are upload fragments and application state, not the user's files.
                    if not name.startswith('.') and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        result.append((name, stat.S_ISDIR(info.st_mode), info.st_size, info.st_mtime))
                except OSError:
                    continue
            return sorted(result, key=lambda item: (not item[1], item[0].casefold()))
        finally:
            os.close(fd)

    def mkdir(self, parts, owner=None):
        """Create a folder. A privileged caller passes the UID that must own it."""
        if not parts or not valid(parts[-1]):
            raise ValueError('Use a plain folder name')
        fd = self.open(parts[:-1], directory=True)
        try:
            os.mkdir(parts[-1], 0o755, dir_fd=fd)
            if owner is not None:
                os.chown(parts[-1], owner, owner, dir_fd=fd, follow_symlinks=False)
        finally:
            os.close(fd)

    def rename(self, parts, name):
        """Rename a file or folder in place. Never replaces an existing entry."""
        if not parts or not valid(parts[-1]) or not valid(name):
            raise ValueError('Use a plain name')
        fd = self.open(parts[:-1], directory=True)
        try:
            info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Only files and folders can be renamed')
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(parts[-1], name, src_dir_fd=fd, dst_dir_fd=fd)
            else:
                raise FileExistsError(name)
        finally:
            os.close(fd)

    def remove(self, parts):
        """Delete a file, or a folder with everything in it, never through a link."""
        if not parts or not valid(parts[-1]):
            raise ValueError('Choose a file or folder')
        fd = self.open(parts[:-1], directory=True)
        try:
            self.discard(fd, parts[-1])
        finally:
            os.close(fd)

    @classmethod
    def discard(cls, fd, name, depth=0):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(name, dir_fd=fd)  # A link is removed, never followed.
            return
        if depth > 64:
            raise ValueError('Folder is nested too deeply')
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            for entry in os.listdir(child):
                cls.discard(child, entry, depth + 1)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=fd)

    @staticmethod
    def free_name(fd, name):
        """The name itself when nothing in the folder has it, otherwise "name (1)", "name (2)"..."""
        stem, suffix = Path(name).stem, Path(name).suffix
        for count in range(1000):
            candidate = name if not count else f'{stem} ({count}){suffix}'
            try:
                os.stat(candidate, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                if valid(candidate):
                    return candidate
        raise FileExistsError(name)

    def pair(self, parts, target):
        """Open the folder an entry is in and the folder it is going to."""
        if not parts or not valid(parts[-1]):
            raise ValueError('Choose a file or folder')
        if target[:len(parts)] == parts:
            raise ValueError('A folder cannot go inside itself')
        source = self.open(parts[:-1], directory=True)
        try:
            info = os.stat(parts[-1], dir_fd=source, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Only files and folders can be moved or copied')
            return source, self.open(target, directory=True)
        except Exception:
            os.close(source)
            raise

    def move(self, parts, target):
        """Move a file or folder into another folder. Never replaces an existing entry."""
        source, destination = self.pair(parts, target)
        try:
            try:
                os.stat(parts[-1], dir_fd=destination, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(parts[-1], parts[-1], src_dir_fd=source, dst_dir_fd=destination)
            else:
                raise FileExistsError(parts[-1])
        finally:
            os.close(source)
            os.close(destination)

    def copy(self, parts, target, owner=None):
        """Copy a file, or a folder with everything in it, and return the copy's name.

        Links and special files inside a folder are left out, never followed.
        """
        source, destination = self.pair(parts, target)
        try:
            name = self.free_name(destination, parts[-1])
            self.duplicate(source, parts[-1], destination, name, owner)
            return name
        finally:
            os.close(source)
            os.close(destination)

    @classmethod
    def duplicate(cls, source, name, destination, new, owner, depth=0):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source)
        except OSError as error:
            if depth and error.errno in (errno.ELOOP, errno.ENXIO, errno.ENOENT):
                return
            raise
        try:
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                if depth > 64:
                    raise ValueError('Folder is nested too deeply')
                os.mkdir(new, 0o755, dir_fd=destination)
                child = os.open(new, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination)
                try:
                    if owner is not None:
                        os.fchown(child, owner, owner)
                    for entry in os.listdir(fd):
                        cls.duplicate(fd, entry, child, entry, owner, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                target = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=destination)
                try:
                    if owner is not None:
                        os.fchown(target, owner, owner)
                    while True:
                        chunk = os.read(fd, 1 << 20)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(target, view):]
                finally:
                    os.close(target)
        finally:
            os.close(fd)

    def uri(self, parts, save=False):
        if not parts:
            raise ValueError('Choose a file')
        try:
            fd = self.open(parts)
        except FileNotFoundError:
            if not save:
                raise
            fd = self.open(parts[:-1], directory=True)
            os.close(fd)
            name = parts[-1]
            if not name or name in ('.', '..') or '/' in name or '\\' in name or '\x00' in name:
                raise ValueError('Use a file name without a path')
        else:
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('Choose a regular file')
            finally:
                os.close(fd)
        # Recheck the current path too: an open directory descriptor may refer to
        # a directory renamed since it was opened. The caller receives a URI.
        path = self.root.joinpath(*parts)
        if path.resolve().parent != self.root.joinpath(*parts[:-1]).absolute():
            raise ValueError('Links are not selectable')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('File is outside My files')
        return path.as_uri()


def readable_size(size):
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024:
            return f'{int(value)} B' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} TB'


def readable_date(moment):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(moment))


def sniff(head):
    """The kind of a file from its first bytes, for a name that does not say."""
    if head.startswith((b'\x89PNG\r\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a')) or head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image'
    if head.startswith(b'%PDF-'):
        return 'pdf'
    if head[4:8] == b'ftyp' or head.startswith(b'\x1a\x45\xdf\xa3'):
        return 'video'
    if head.startswith((b'ID3', b'fLaC', b'OggS', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')) or head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'audio'
    if head and b'\x00' not in head:
        try:
            head.decode('utf-8')
            return 'text'
        except UnicodeDecodeError as error:
            # A sample may end in the middle of one character; that is still text.
            return 'text' if error.start >= len(head) - 3 else 'other'
    return 'other'


def kind_of(files, parts):
    """The kind of a file by its name, or by its content when the name does not say."""
    shape = kind(parts[-1])
    if shape != 'other':
        return shape
    try:
        fd = files.open(parts)
        try:
            return sniff(os.read(fd, 512)) if stat.S_ISREG(os.fstat(fd).st_mode) else 'other'
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return 'other'


def readable_time(seconds):
    seconds = max(0, int(seconds))
    return (f'{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}' if seconds >= 3600
            else f'{seconds // 60}:{seconds % 60:02}')


def scaled(fd, width, height):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(f'/proc/self/fd/{fd}', width, height, True)
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    return pixbuf.save_to_bufferv('png', [], [])[1]


def pdf_document(fd):
    import gi
    gi.require_version('Poppler', '0.18')
    from gi.repository import GLib, Poppler
    with os.fdopen(os.dup(fd), 'rb') as source:
        data = source.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError('Too large to preview')
    return Poppler.Document.new_from_bytes(GLib.Bytes.new(data), None)


def render(files, parts, fd, shape, width, height, page=0):
    """PNG bytes picturing a file: a picture scaled down, one page of a PDF, a
    frame of a video, or the cover of a piece of music."""
    if shape == 'video':
        # One still frame, taken by a separate short-lived program with a deadline.
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('XDG_RUNTIME_DIR')) as scratch:
            target = os.path.join(scratch, 'frame.png')
            subprocess.run(['ffmpegthumbnailer', '-i', f'/dev/fd/{fd}', '-o', target, '-c', 'png',
                            '-s', str(max(width, height)), '-t', '10%'], pass_fds=[fd], timeout=12, check=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(target, 'rb') as frame:
                return frame.read(32 * 1024 * 1024)
    if shape == 'audio':
        # The cover stored in the file, or a cover picture kept beside it.
        embedded = subprocess.run(
            ['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', f'/dev/fd/{fd}', '-an', '-frames:v', '1',
             '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease', '-f', 'image2pipe',
             '-vcodec', 'png', 'pipe:1'], pass_fds=[fd], timeout=10, capture_output=True, stdin=subprocess.DEVNULL)
        if embedded.returncode == 0 and embedded.stdout.startswith(b'\x89PNG'):
            return embedded.stdout
        for name, directory, size, _ in files.entries(parts[:-1]):
            if not directory and size <= 32 * 1024 * 1024 and re.fullmatch(r'(folder|cover|front)\.(jpe?g|png)', name, re.I):
                cover = files.open(parts[:-1] + [name])
                try:
                    return scaled(cover, width, height)
                finally:
                    os.close(cover)
        return b''
    if shape == 'pdf':
        import cairo
        import io
        document = pdf_document(fd)
        sheet = document.get_page(min(max(0, int(page)), document.get_n_pages() - 1))
        page_width, page_height = sheet.get_size()
        scale = min(width / page_width, height / page_height)
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, int(page_width * scale)), max(1, int(page_height * scale)))
        context = cairo.Context(surface)
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.scale(scale, scale)
        sheet.render(context)
        output = io.BytesIO()
        surface.write_to_png(output)
        return output.getvalue()
    return scaled(fd, width, height)


def describe(fd, shape):
    """Facts about a file as JSON: a PDF's page count, or a recording's length and tags."""
    if shape == 'pdf':
        return json.dumps({'pages': pdf_document(fd).get_n_pages()}).encode()
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:format_tags=title,artist,album',
                            '-of', 'json', f'/dev/fd/{fd}'], pass_fds=[fd], capture_output=True, timeout=8,
                           stdin=subprocess.DEVNULL)
    found = json.loads(probe.stdout).get('format', {})
    facts = {key.lower(): str(value)[:200] for key, value in found.get('tags', {}).items()
             if key.lower() in ('title', 'artist', 'album')}
    try:
        facts['duration'] = max(0.0, float(found.get('duration', 0)))
    except ValueError:
        facts['duration'] = 0.0
    return json.dumps(facts).encode()


def render_worker(root):
    """Draw previews for the picker in a separate process.

    Image, PDF and media decoders read whatever the user downloaded. Here a
    decoder that crashes or hangs costs one preview; inside the picker it would
    end the picker, and the desktop closes with it.
    """
    files = Files(root)
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    for line in source:
        request = json.loads(line)
        payload = b''
        try:
            parts, shape = request['parts'], request['kind']
            fd = files.open(parts)
            try:
                info = os.fstat(fd)
                limit = 64 * 1024 ** 3 if shape in ('video', 'audio') else 256 * 1024 ** 2
                if stat.S_ISREG(info.st_mode) and info.st_size <= limit:
                    payload = (describe(fd, shape) if request['op'] == 'describe' else
                               render(files, parts, fd, shape, request['width'], request['height'], request.get('page', 0)))
            finally:
                os.close(fd)
        except Exception as error:  # One unreadable file is one missing preview.
            print('Preview unavailable: ' + type(error).__name__, file=sys.stderr, flush=True)
        sink.write(struct.pack('!II', request['id'], len(payload)) + payload)
        sink.flush()


def matches(filename, patterns):
    """Whether a file passes a portal filter: (0, glob) or (1, MIME type) pairs.

    Names compare without case: a site asking for *.jpg means PHOTO.JPG too.
    """
    mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return not patterns or any(
        fnmatch.fnmatchcase((filename if kind == 0 else mime).casefold(), str(pattern).casefold())
        for kind, pattern in patterns)


def main():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk
    try:
        # Coloured, numbered text previews when the library is installed.
        gi.require_version('GtkSource', '4')
        from gi.repository import GtkSource
    except (ValueError, ImportError):
        GtkSource = None
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    GLib.set_prgname('brave-files')
    Gtk.init([])
    Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', True)
    files = Files(Path.home() / 'Downloads')

    class Previews:
        """Queue of pictures drawn by the separate render process, one at a time."""
        def __init__(self):
            self.process = self.watch = self.busy = self.timer = None
            self.queue, self.buffer, self.count = [], b'', 0

        def request(self, parts, width, height, callback, first=False, shape='image', page=0, op='render'):
            self.count += 1
            item = (self.count, {'id': self.count, 'op': op, 'parts': parts, 'kind': shape, 'page': page,
                                 'width': width, 'height': height}, callback)
            self.queue.insert(0, item) if first else self.queue.append(item)
            self.pump()

        def describe(self, parts, shape, callback):
            """Facts about a file (pages, length, tags) as a dictionary, or None."""
            self.request(parts, 0, 0, callback, first=True, shape=shape, op='describe')

        def forget(self):
            self.queue.clear()

        def pump(self):
            if self.busy or not self.queue:
                return
            try:
                if self.process is None or self.process.poll() is not None:
                    self.start()
                number, request, callback = self.queue.pop(0)
                self.busy = (number, callback, request['op'])
                self.process.stdin.write((json.dumps(request) + '\n').encode())
                self.process.stdin.flush()
                self.timer = GLib.timeout_add_seconds(15, self.expired, number)
            except OSError:
                self.fail()

        def start(self):
            self.stop()
            self.process = subprocess.Popen([sys.executable, os.path.abspath(__file__), '--render', str(files.root)],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            os.set_blocking(self.process.stdout.fileno(), False)
            self.buffer = b''
            self.watch = GLib.io_add_watch(self.process.stdout.fileno(), GLib.IO_IN | GLib.IO_HUP, self.readable)

        def stop(self):
            if self.watch:
                GLib.source_remove(self.watch)
                self.watch = None
            if self.process:
                self.process.kill()
                self.process.wait()
                for pipe in (self.process.stdin, self.process.stdout):
                    try:
                        pipe.close()
                    except OSError:
                        pass
                self.process = None

        def readable(self, fd, condition):
            try:
                chunk = os.read(fd, 1 << 20)
            except BlockingIOError:
                return True
            except OSError:
                chunk = b''
            if not chunk:
                self.watch = None  # Returning False removes this watch.
                self.fail()
                return False
            self.buffer += chunk
            while len(self.buffer) >= 8:
                number, length = struct.unpack('!II', self.buffer[:8])
                if len(self.buffer) < 8 + length:
                    break
                payload, self.buffer = self.buffer[8:8 + length], self.buffer[8 + length:]
                self.answer(number, payload)
            return True

        def answer(self, number, payload):
            busy, self.busy = self.busy, None
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            result = None
            if payload and busy and busy[2] == 'describe':
                try:
                    result = json.loads(payload)
                except ValueError:
                    result = None
            elif payload:
                try:
                    loader = GdkPixbuf.PixbufLoader.new_with_type('png')
                    loader.write(payload)
                    loader.close()
                    result = loader.get_pixbuf()
                except GLib.Error:
                    result = None
            if busy and busy[0] == number:
                busy[1](result)
            self.pump()

        def fail(self):
            # The renderer died or stalled on this file: no preview, start afresh.
            self.stop()
            busy, self.busy = self.busy, None
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            if busy:
                busy[1](None)
            self.pump()

        def expired(self, number):
            self.timer = None
            if self.busy and self.busy[0] == number:
                self.fail()
            return False

    previews = Previews()

    class Player:
        """Plays a video, an animation or a recording in the preview pane.

        ffmpeg decodes in its own processes, one sending small frames down a
        pipe at the video's own speed and one sending the sound to the desktop.
        The picker only paints finished frames, so a file that breaks a decoder
        stops the playback and nothing else.
        """
        def __init__(self, picture, changed):
            self.picture, self.changed = picture, changed
            self.video = self.audio = self.watch = None
            self.buffer, self.frame, self.size, self.paused = b'', 0, (0, 0), False
            self.origin = self.held = self.length = 0.0

        @property
        def playing(self):
            return self.video is not None or self.audio is not None

        @property
        def position(self):
            """Seconds into the file, as far as the clock can tell."""
            if not self.playing:
                return 0.0
            place = (self.held if self.paused else time.monotonic()) - self.origin
            return place % self.length if self.length and place > self.length else place

        def listening(self):
            if self.audio and self.audio.poll() is None:
                return True
            self.watch = None  # Returning False removes this timer.
            self.stop()
            return False

        def start(self, parts, shape, width=320, height=420, at=0.0, length=0.0, loop=False, sound=True):
            self.stop()
            try:
                fd = files.open(parts)
            except (OSError, ValueError):
                return False
            begin = ['ffmpeg', '-nostdin', '-loglevel', 'error']
            if loop:
                begin += ['-ignore_loop', '0'] if parts[-1].lower().endswith('.gif') else ['-stream_loop', '-1']
            if at > 0:
                begin += ['-ss', f'{at:.2f}']
            quiet = dict(pass_fds=[fd], stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if shape == 'audio':
                    self.audio = subprocess.Popen(begin + ['-i', f'/dev/fd/{fd}', '-vn', '-sn', '-f', 'pulse', 'My files preview'],
                                                  stdout=subprocess.DEVNULL, **quiet)
                    self.watch = GLib.timeout_add(400, self.listening)
                else:
                    probe = subprocess.run(
                        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                         '-of', 'json', f'/dev/fd/{fd}'], pass_fds=[fd], capture_output=True, timeout=8,
                        stdin=subprocess.DEVNULL)
                    stream = json.loads(probe.stdout)['streams'][0]
                    # Fit the space given, enlarging a small video up to three times.
                    scale = min(width / int(stream['width']), height / int(stream['height']), 3)
                    # Even sizes: several decoders and scalers require them.
                    w = max(2, int(int(stream['width']) * scale) // 2 * 2)
                    h = max(2, int(int(stream['height']) * scale) // 2 * 2)
                    self.video = subprocess.Popen(
                        begin + ['-re', '-i', f'/dev/fd/{fd}', '-an', '-sn', '-vf', f'scale={w}:{h},fps=20',
                                 '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], stdout=subprocess.PIPE, **quiet)
                    if sound:
                        self.audio = subprocess.Popen(
                            begin + ['-re', '-i', f'/dev/fd/{fd}', '-vn', '-sn', '-f', 'pulse', 'My files preview'],
                            stdout=subprocess.DEVNULL, **quiet)
                    self.size, self.frame, self.buffer = (w, h), w * h * 3, b''
                    os.set_blocking(self.video.stdout.fileno(), False)
                    self.watch = GLib.io_add_watch(self.video.stdout.fileno(), GLib.IO_IN | GLib.IO_HUP, self.readable)
            except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError):
                self.stop()
                return False
            finally:
                os.close(fd)
            self.paused, self.length, self.origin = False, length, time.monotonic() - at
            self.changed()
            return True

        def readable(self, fd, condition):
            try:
                chunk = os.read(fd, 4 << 20)
            except BlockingIOError:
                return True
            except OSError:
                chunk = b''
            if not chunk:
                self.watch = None  # Returning False removes this watch.
                self.stop()
                return False
            self.buffer += chunk
            if len(self.buffer) >= self.frame:
                # Paint the newest whole frame and drop any it has fallen behind.
                whole = len(self.buffer) // self.frame * self.frame
                frame, self.buffer = self.buffer[whole - self.frame:whole], self.buffer[whole:]
                w, h = self.size
                self.picture.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_bytes(
                    GLib.Bytes.new(frame), GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3))
            return True

        def pause(self):
            if not self.playing:
                return
            self.paused = not self.paused
            if self.paused:
                self.held = time.monotonic()
            else:
                self.origin += time.monotonic() - self.held
            for process in (self.video, self.audio):
                if process and process.poll() is None:
                    process.send_signal(signal.SIGSTOP if self.paused else signal.SIGCONT)
            self.changed()

        def stop(self):
            if self.watch:
                GLib.source_remove(self.watch)
                self.watch = None
            for process in (self.video, self.audio):
                if process:
                    process.kill()
                    process.wait()
                    if process.stdout:
                        process.stdout.close()
            was, self.video, self.audio, self.buffer = self.playing, None, None, b''
            if was:
                self.changed()
    bus = dbus.SessionBus()
    name = dbus.service.BusName('org.freedesktop.portal.Desktop', bus, do_not_queue=True)
    chooser = 'org.freedesktop.portal.FileChooser'
    requests = 'org.freedesktop.portal.Request'
    properties = 'org.freedesktop.DBus.Properties'
    active = {}

    class Request(dbus.service.Object):
        def __init__(self, sender, token, options, save):
            self.sender, self.options, self.save = sender, options, save
            self.parts, self.finished = [], False
            self.path = '/org/freedesktop/portal/desktop/request/' + sender[1:].replace('.', '_') + '/' + token
            super().__init__(bus, self.path)
            active[self.path] = self
            self.window = None
            GLib.idle_add(self.show)

        @dbus.service.method(requests, in_signature='', out_signature='', sender_keyword='sender')
        def Close(self, sender=None):
            if sender == self.sender:
                self.finish(1)

        def finish(self, code, uris=None):
            if self.finished:
                return
            self.finished = True
            result = dbus.Dictionary(signature='sv')
            if uris is not None:
                result['uris'] = dbus.Array(uris, signature='s')
                result['writable'] = dbus.Boolean(self.save)
            message = dbus.lowlevel.SignalMessage(self.path, requests, 'Response')
            message.set_destination(self.sender)
            message.append(dbus.UInt32(code), result, signature='ua{sv}')
            bus.send_message(message)
            if getattr(self, 'player', None):
                self.player.stop()
            if self.window:
                self.window.destroy()
            self.remove_from_connection()
            active.pop(self.path, None)
            print('My files picker closed: ' + str(code), flush=True)

        def show(self):
            if self.finished:
                return False
            try:
                self.build()
            except Exception:
                print('File picker could not open; request cancelled', file=sys.stderr, flush=True)
                self.finish(2)
            return False

        def build(self):
            # Folder uploads are deliberately rejected until recursive selection
            # can be constrained through the browser's own directory enumeration.
            if self.options.get('directory', False):
                self.finish(2)
                return
            w = self.window = Gtk.Window(title='My files')
            w.set_decorated(False)
            w.set_wmclass('brave-files', 'BraveFiles')
            w.set_default_size(760, 460)
            w.connect('delete-event', lambda *_: (self.finish(1), True)[1])
            w.connect('key-press-event', self.window_key)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
            w.add(box)
            top = Gtk.Box(spacing=8)
            back = Gtk.Button(label='Up')
            back.connect('clicked', self.up)
            self.back = back
            top.pack_start(back, False, False, 0)
            self.heading = Gtk.Label(label='My files', xalign=0)
            self.heading.set_ellipsize(3)
            top.pack_start(self.heading, True, True, 0)
            actions = []
            if os.environ.get('BRAVE_PRIVATE_FILES') == 'true':
                actions.append(('Open in Brave', 'Open the selected file as a tab in Brave', self.open_in_brave))
            for label, tip, action in (('Refresh', 'Reload this folder', lambda *_: self.refresh()), *actions,
                                       ('Preview', 'Show or hide the preview (Space)', self.toggle_preview),
                                       ('Delete', 'Delete the selected item (Delete)', self.delete),
                                       ('Rename', 'Rename the selected item (F2)', self.rename),
                                       ('New folder', 'Create a folder here', self.new_folder)):
                button = Gtk.Button(label=label)
                button.set_tooltip_text(tip)
                button.connect('clicked', action)
                top.pack_end(button, False, False, 0)
            box.pack_start(top, False, False, 0)
            # name, size text, folder, bytes, modified, date text, thumbnail
            self.model = Gtk.ListStore(str, str, bool, GObject.TYPE_INT64, float, str, GdkPixbuf.Pixbuf)
            for column in (0, 3, 4):
                self.model.set_sort_func(column, self.compare, column)
            self.model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
            self.tree = Gtk.TreeView(model=self.model)
            name = Gtk.TreeViewColumn('Name')
            picture = Gtk.CellRendererPixbuf()
            name.pack_start(picture, False)
            name.add_attribute(picture, 'pixbuf', 6)
            text = Gtk.CellRendererText()
            text.set_property('ellipsize', 3)
            name.pack_start(text, True)
            name.add_attribute(text, 'text', 0)
            name.set_expand(True)
            name.set_sort_column_id(0)
            self.tree.append_column(name)
            modified = Gtk.TreeViewColumn('Modified', Gtk.CellRendererText(), text=5)
            modified.set_sort_column_id(4)
            self.tree.append_column(modified)
            size = Gtk.TreeViewColumn('Size', Gtk.CellRendererText(), text=1)
            size.set_sort_column_id(3)
            self.tree.append_column(size)
            # Typing a name jumps to it.
            self.tree.set_enable_search(True)
            self.tree.set_search_column(0)
            self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE if self.options.get('multiple') and not self.save else Gtk.SelectionMode.SINGLE)
            self.tree.connect('row-activated', self.activate)
            self.tree.connect('key-press-event', self.tree_key)
            self.tree.get_selection().connect('changed', lambda *_: self.show_preview())
            scroll = self.listing = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.tree)
            middle = self.middle = Gtk.Box(spacing=12)
            middle.pack_start(scroll, True, True, 0)
            # Quick preview of the selected file, hidden until asked for.
            self.pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.pane.set_size_request(320, -1)
            self.pane.set_no_show_all(True)
            self.picture = Gtk.Image()
            # Click plays or pauses; a double click enlarges the preview.
            frame = Gtk.EventBox()
            frame.add(self.picture)
            frame.connect('button-press-event', self.picture_clicked)
            if GtkSource:
                self.text = GtkSource.View(editable=False, cursor_visible=False, monospace=True)
                self.text.set_buffer(GtkSource.Buffer())
                scheme = GtkSource.StyleSchemeManager.get_default().get_scheme('oblivion')
                if scheme:
                    self.text.get_buffer().set_style_scheme(scheme)
            else:
                self.text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
            self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_scroll = Gtk.ScrolledWindow()
            text_scroll.add(self.text)
            self.stack = Gtk.Stack()
            self.stack.add_named(frame, 'picture')
            self.stack.add_named(text_scroll, 'text')
            self.stack.add_named(Gtk.Label(label=''), 'none')
            self.details = Gtk.Label(xalign=0)
            self.details.set_line_wrap(True)
            self.details.set_selectable(False)
            self.pane.pack_start(self.stack, True, True, 0)
            self.player = Player(self.picture, self.player_changed)
            self.large, self.shape, self.media, self.page, self.pages, self.at = False, 'other', {}, 0, 1, 0.0
            self.preview, self.base, self.notes, self.counting, self.ticker = 0, '', [], None, None
            # Position in a video or recording; drag to move.
            self.seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
            self.seek.set_draw_value(False)
            self.seek.connect('change-value', self.seek_to)
            self.clock = Gtk.Label(label='0:00')
            self.seek_row = Gtk.Box(spacing=8)
            self.seek_row.pack_start(self.seek, True, True, 0)
            self.seek_row.pack_start(self.clock, False, False, 0)
            self.pane.pack_start(self.seek_row, False, False, 0)
            # Pages of a PDF.
            self.page_back, self.page_next = Gtk.Button(label='\u2039'), Gtk.Button(label='\u203a')
            self.page_back.set_tooltip_text('Previous page')
            self.page_next.set_tooltip_text('Next page')
            self.page_back.connect('clicked', lambda *_: self.turn(-1))
            self.page_next.connect('clicked', lambda *_: self.turn(1))
            self.page_label = Gtk.Label(label='')
            self.pages_row = Gtk.Box(spacing=8)
            self.pages_row.pack_start(self.page_back, False, False, 0)
            self.pages_row.pack_start(self.page_label, True, True, 0)
            self.pages_row.pack_start(self.page_next, False, False, 0)
            self.pane.pack_start(self.pages_row, False, False, 0)
            controls = Gtk.Box(spacing=8)
            self.play = Gtk.Button(label='Play')
            self.play.set_tooltip_text('Play this file here')
            self.play.set_no_show_all(True)
            self.play.connect('clicked', self.play_video)
            self.bigger = Gtk.Button(label='Enlarge')
            self.bigger.set_tooltip_text('Give the preview the whole window (double click the preview)')
            self.bigger.connect('clicked', self.toggle_large)
            controls.pack_start(self.play, True, True, 0)
            controls.pack_start(self.bigger, True, True, 0)
            self.pane.pack_start(controls, False, False, 0)
            self.pane.pack_start(self.details, False, False, 0)
            for row in (self.seek_row, self.pages_row):
                row.set_no_show_all(True)
            middle.pack_start(self.pane, False, False, 0)
            box.pack_start(middle, True, True, 0)
            self.filename = Gtk.Entry()
            self.filename.set_placeholder_text('File name')
            if self.save:
                suggested = str(self.options.get('current_name', ''))
                self.filename.set_text(Path(suggested).name[:255])
                box.pack_start(self.filename, False, False, 0)
                self.filename.connect('activate', self.accept)
            self.filter = Gtk.ComboBoxText()
            self.filters = list(self.options.get('filters', []))[:30]
            for label, _ in self.filters:
                self.filter.append_text(str(label)[:100])
            if self.filters:
                self.filter.set_active(0)
                self.filter.connect('changed', lambda *_: self.refresh())
                box.pack_start(self.filter, False, False, 0)
            self.error = Gtk.Label(xalign=0)
            self.error.set_line_wrap(True)
            box.pack_start(self.error, False, False, 0)
            buttons = Gtk.Box(spacing=10)
            cancel = Gtk.Button.new_with_mnemonic('_Cancel')
            cancel.connect('clicked', lambda *_: self.finish(1))
            accept = Gtk.Button.new_with_mnemonic('_Save' if self.save else '_Open')
            accept.connect('clicked', self.accept)
            buttons.pack_end(accept, False, False, 0)
            buttons.pack_end(cancel, False, False, 0)
            box.pack_end(buttons, False, False, 0)
            self.refresh()
            w.show_all()
            w.present()
            (self.filename if self.save else self.tree).grab_focus()
            print('My files picker opened: ' + ('SaveFile' if self.save else 'OpenFile'), flush=True)

        def compare(self, model, first, second, column):
            # Folders stay on top whichever way the column is sorted.
            descending = model.get_sort_column_id()[1] == Gtk.SortType.DESCENDING
            a, b = model[first], model[second]
            if a[2] != b[2]:
                return (1 if a[2] else -1) if descending else (-1 if a[2] else 1)
            x, y = (a[0].casefold(), b[0].casefold()) if column == 0 else (a[column], b[column])
            return (x > y) - (x < y) or (a[0] > b[0]) - (a[0] < b[0])

        def window_key(self, _, event):
            if event.keyval != Gdk.KEY_Escape:
                return False
            if self.large:
                self.toggle_large()
            elif self.pane.get_visible():
                self.toggle_preview()
            else:
                self.finish(1)
            return True

        def tree_key(self, _, event):
            if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
                return False
            action = {Gdk.KEY_space: self.toggle_preview, Gdk.KEY_F2: self.rename, Gdk.KEY_Delete: self.delete}.get(event.keyval)
            if action:
                action()
                return True
            return False

        def chosen(self):
            """The single selected row's name and whether it is a folder, or None."""
            _, rows = self.tree.get_selection().get_selected_rows()
            return (self.model[rows[0]][0], self.model[rows[0]][2]) if len(rows) == 1 else None

        def refresh(self):
            self.generation = getattr(self, 'generation', 0) + 1
            previews.forget()
            self.model.clear()
            self.back.set_sensitive(bool(self.parts))
            self.heading.set_text('My files' + (' / ' + ' / '.join(self.parts) if self.parts else ''))
            try:
                pictures = 0
                for filename, directory, size, modified in files.entries(self.parts):
                    if not directory and self.filters:
                        if not matches(filename, self.filters[self.filter.get_active()][1]):
                            continue
                    row = self.model.append([filename, 'Folder' if directory else readable_size(size), directory,
                                             size, modified, readable_date(modified), None])
                    # Small pictures beside picture files, drawn in the background.
                    if not directory and kind(filename) == 'image' and size <= 32 * 1024 * 1024 and pictures < 400:
                        pictures += 1
                        reference = Gtk.TreeRowReference.new(self.model, self.model.get_path(row))
                        previews.request(self.parts + [filename], 28, 28,
                                         lambda picture, reference=reference, generation=self.generation:
                                         self.thumbnail(reference, generation, picture))
                self.error.set_text('')
            except (OSError, ValueError):
                self.error.set_text('This folder is no longer available. Use Up or Refresh.')
            self.show_preview()

        def thumbnail(self, reference, generation, picture):
            if picture and generation == self.generation and not self.finished and reference.valid():
                self.model[reference.get_path()][6] = picture

        def toggle_preview(self, *_):
            if self.large:
                self.toggle_large()
            self.pane.set_visible(not self.pane.get_visible())
            if self.pane.get_visible():
                for child in self.pane.get_children():
                    if child not in (self.seek_row, self.pages_row):
                        child.show_all()
                self.play.set_visible(False)
                self.window.resize(max(self.window.get_size()[0], 1080), max(self.window.get_size()[1], 640))
            self.show_preview()

        def toggle_large(self, *_):
            """Give the preview the whole window, or hand the file list back."""
            self.large = not self.large
            self.listing.set_visible(not self.large)
            self.middle.set_child_packing(self.pane, self.large, self.large, 0, Gtk.PackType.START)
            self.bigger.set_label('Shrink' if self.large else 'Enlarge')
            if self.player.playing and self.shape != 'audio':
                self.begin(self.player.position)
            elif self.shape in ('image', 'pdf', 'video', 'audio'):
                self.draw()

        def area(self):
            """Room for a preview picture, in pixels."""
            if not self.large:
                return 320, 420
            width, height = self.window.get_size()
            return max(320, width - 80), max(240, height - 260)

        def picture_clicked(self, _, event):
            if event.type == Gdk.EventType._2BUTTON_PRESS:
                if self.shape in ('video', 'audio'):
                    self.play_video()  # Undo what the first click of the pair did.
                self.toggle_large()
            elif event.type == Gdk.EventType.BUTTON_PRESS and self.shape in ('video', 'audio'):
                self.play_video()
            return True

        def show_notes(self):
            self.details.set_text('\n'.join([self.base] + self.notes))

        def player_changed(self):
            if self.finished:
                return
            self.play.set_label('Play' if not self.player.playing else 'Resume' if self.player.paused else 'Pause')
            if self.player.playing and not self.ticker and self.shape in ('video', 'audio'):
                self.ticker = GLib.timeout_add(500, self.tick)
            if not self.player.playing and self.shape in ('video', 'audio'):
                self.at = 0.0
                self.show_clock(0.0)

        def tick(self):
            if self.finished or not self.player.playing:
                self.ticker = None
                return False
            self.show_clock(self.player.position)
            return True

        def show_clock(self, place):
            length = self.media.get('duration', 0)
            self.seek.set_value(min(place, length) if length else 0)
            self.clock.set_text(readable_time(place) + (' / ' + readable_time(length) if length else ''))

        def seek_to(self, _, scroll, value):
            value = min(max(0.0, value), max(0.0, self.media.get('duration', 0) - 0.5))
            if self.player.playing:
                self.begin(value)
            else:
                self.at = value
                self.show_clock(value)
            return False

        def begin(self, at=0.0):
            chosen = self.chosen()
            if not chosen or chosen[1]:
                return False
            length = self.media.get('duration', 0)
            if self.shape == 'video':
                self.stack.set_visible_child_name('picture')
            # Short clips repeat, as an animation would.
            return self.player.start(self.parts + [chosen[0]], self.shape, *self.area(), at=at, length=length,
                                     loop=self.shape == 'video' and 0 < length <= 30)

        def play_video(self, *_):
            if self.player.playing:
                self.player.pause()
            elif self.shape in ('video', 'audio') and not self.begin(self.at):
                self.notes.append('This file cannot be played here.')
                self.show_notes()

        def turn(self, by):
            page = min(max(0, self.page + by), self.pages - 1)
            if page != self.page:
                self.page = page
                self.show_pages()
                self.draw()

        def show_pages(self):
            self.page_label.set_text(f'{self.page + 1} of {self.pages}')
            self.page_back.set_sensitive(self.page > 0)
            self.page_next.set_sensitive(self.page < self.pages - 1)

        def draw(self):
            chosen = self.chosen()
            if chosen and not chosen[1]:
                previews.request(self.parts + [chosen[0]], *self.area(),
                                 lambda picture, preview=self.preview: self.picture_ready(preview, picture),
                                 first=True, shape=self.shape, page=self.page)

        def count_folder(self, parts, preview):
            """Add up a folder's contents a little at a time, so the window stays responsive."""
            def walk():
                waiting, count, folders, total = [parts], 0, 0, 0
                while waiting and count + folders < 200000:
                    current = waiting.pop()
                    try:
                        for index, (name, directory, size, _) in enumerate(files.entries(current)):
                            if directory:
                                folders += 1
                                if len(current) < 64:
                                    waiting.append(current + [name])
                            else:
                                count += 1
                                total += size
                            if index % 500 == 499:
                                yield count, folders, total, False
                    except (OSError, ValueError):
                        continue
                    yield count, folders, total, False
                yield count, folders, total, True
            steps = walk()

            def step():
                if self.finished or preview != self.preview:
                    return False
                try:
                    count, folders, total, done = next(steps)
                except StopIteration:
                    return False
                items = f'{count} file' + ('' if count == 1 else 's') + (f', {folders} folder' + ('' if folders == 1 else 's') if folders else '')
                self.notes = [items + ' · ' + readable_size(total) + ('' if done else ' (counting…)')]
                self.show_notes()
                return not done
            GLib.idle_add(step)

        def show_preview(self):
            if self.finished:
                return
            self.player.stop()
            for widget in (self.play, self.seek_row, self.pages_row):
                widget.set_visible(False)
            if not self.pane.get_visible():
                return
            self.preview += 1
            self.shape, self.media, self.page, self.pages, self.at, self.notes = 'other', {}, 0, 1, 0.0, []
            chosen = self.chosen()
            if not chosen:
                self.stack.set_visible_child_name('none')
                self.details.set_text('Select one file to preview it.')
                return
            name, directory = chosen
            row = next(row for row in self.model if row[0] == name)
            sort = 'Folder' if directory else (mimetypes.guess_type(name)[0] or 'File')
            self.base = f'{name}\n{sort}' + ('' if directory else ' · ' + row[1]) + f'\nModified {row[5]}'
            self.show_notes()
            self.stack.set_visible_child_name('none')
            target = self.parts + [name]
            if directory:
                self.count_folder(target, self.preview)
                return
            shape = self.shape = kind_of(files, target)
            if shape in ('video', 'audio'):
                self.play.set_visible(True)
                self.notes = ['Press Play to watch it here.' if shape == 'video' else 'Press Play to listen.']
                self.show_notes()
            if shape in ('video', 'audio', 'pdf'):
                previews.describe(target, shape, lambda facts, preview=self.preview: self.facts_ready(preview, facts))
            if shape in ('image', 'pdf', 'video', 'audio'):
                self.draw()
            if shape == 'image' and name.lower().endswith('.gif') and row[3] <= 32 * 1024 * 1024:
                # Animations move, as they do everywhere else.
                self.player.start(target, 'image', *self.area(), loop=True, sound=False)
            elif shape == 'text':
                try:
                    fd = files.open(target)
                    try:
                        sample = os.read(fd, 65536)
                    finally:
                        os.close(fd)
                    buffer = self.text.get_buffer()
                    if GtkSource:
                        language = GtkSource.LanguageManager.get_default().guess_language(name, None)
                        buffer.set_language(language)
                        self.text.set_show_line_numbers(language is not None)
                    buffer.set_text(sample.decode('utf-8', 'replace').replace('\x00', ' '))
                    self.stack.set_visible_child_name('text')
                except (OSError, ValueError):
                    pass

        def facts_ready(self, preview, facts):
            if self.finished or preview != self.preview or not facts:
                return
            self.media = facts
            if self.shape == 'pdf':
                self.pages = max(1, int(facts.get('pages', 1)))
                self.pages_row.set_visible(self.pages > 1)
                self.pages_row.show_all() if self.pages > 1 else None
                self.show_pages()
                return
            credits = ' — '.join(facts[key] for key in ('title', 'artist', 'album') if facts.get(key))
            if credits:
                self.notes.insert(0, credits)
                self.show_notes()
            length = facts.get('duration', 0)
            if length > 1:
                self.seek.set_range(0, length)
                self.seek_row.set_visible(True)
                self.seek_row.show_all()
                self.show_clock(self.at)

        def picture_ready(self, preview, picture):
            if self.finished or preview != self.preview or self.player.video is not None:
                return
            if picture:
                self.picture.set_from_pixbuf(picture)
                self.stack.set_visible_child_name('picture')
            elif self.shape != 'audio':
                self.notes.append('No preview available.')
                self.show_notes()

        def open_in_brave(self, *_):
            chosen = self.chosen()
            if not chosen or chosen[1]:
                self.error.set_text('Select one file to open in Brave.')
                return
            reason = openable(chosen[0], os.environ.get('BRAVE_OFFICE_VIEWER') == 'true')
            if reason:
                self.error.set_text(reason)
                return
            # The gateway watches this private folder and opens the file as it
            # does from the My files panel, office documents included.
            try:
                files.uri(self.parts + [chosen[0]])
                folder = Path(os.environ['XDG_RUNTIME_DIR'])
                draft = folder / ('.open-request-' + secrets.token_hex(6))
                draft.write_text(json.dumps({'parts': self.parts + [chosen[0]]}))
                os.replace(draft, folder / 'open-request')
                self.error.set_text(f'Opening "{chosen[0]}" in Brave. Close this window to see it.')
            except (OSError, ValueError, KeyError):
                self.error.set_text('That file could not be opened in Brave.')

        def ask(self, title, value=''):
            dialog = Gtk.Dialog(title=title, transient_for=self.window, modal=True)
            dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'OK', Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.OK)
            entry = Gtk.Entry(text=value, activates_default=True, margin=12)
            dialog.get_content_area().pack_start(Gtk.Label(label=title, xalign=0, margin=12), False, False, 0)
            dialog.get_content_area().pack_start(entry, False, False, 0)
            dialog.show_all()
            answer = dialog.run()
            text = entry.get_text().strip()
            dialog.destroy()
            return text if answer == Gtk.ResponseType.OK and text else None

        def change(self, action, problem):
            try:
                action()
            except FileExistsError:
                self.refresh()
                self.error.set_text('Something with that name already exists here.')
            except (OSError, ValueError):
                self.refresh()
                self.error.set_text(problem)
            else:
                self.refresh()

        def new_folder(self, *_):
            name = self.ask('Name of the new folder')
            if name:
                self.change(lambda: files.mkdir(self.parts + [name]), 'Use a plain folder name without / or a leading dot.')

        def rename(self, *_):
            chosen = self.chosen()
            if not chosen:
                self.error.set_text('Select one item to rename.')
                return
            name = self.ask('New name', chosen[0])
            if name and name != chosen[0]:
                self.change(lambda: files.rename(self.parts + [chosen[0]], name), 'Use a plain name without / or a leading dot.')

        def delete(self, *_):
            chosen = self.chosen()
            if not chosen:
                self.error.set_text('Select one item to delete.')
                return
            question = (f'Permanently delete the folder "{chosen[0]}" and everything in it?' if chosen[1]
                        else f'Permanently delete "{chosen[0]}"?')
            dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.WARNING,
                                       buttons=Gtk.ButtonsType.OK_CANCEL, text=question)
            answer = dialog.run()
            dialog.destroy()
            if answer == Gtk.ResponseType.OK:
                self.change(lambda: files.remove(self.parts + [chosen[0]]), 'That item could not be deleted.')

        def up(self, *_):
            self.parts = self.parts[:-1]
            self.refresh()

        def activate(self, tree, path, column):
            row = self.model[path]
            if row[2]:
                self.parts.append(row[0])
                self.refresh()
            elif self.save:
                self.filename.set_text(row[0])
            else:
                self.accept()

        def accept(self, *_):
            try:
                if self.save:
                    parts = self.parts + [self.filename.get_text()]
                    uri = files.uri(parts, save=True)
                    if files.root.joinpath(*parts).exists():
                        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL, text='Replace this file?')
                        answer = dialog.run()
                        dialog.destroy()
                        if answer != Gtk.ResponseType.OK:
                            return
                    uris = [uri]
                else:
                    _, rows = self.tree.get_selection().get_selected_rows()
                    if not rows:
                        raise ValueError('Choose a file')
                    if len(rows) == 1 and self.model[rows[0]][2]:
                        self.activate(self.tree, rows[0], None)
                        return
                    uris = [files.uri(self.parts + [self.model[row][0]]) for row in rows]
                self.finish(0, uris)
            except (OSError, ValueError):
                self.error.set_text('Choose a regular file inside My files. Links and paths outside it are not allowed.')

    class Portal(dbus.service.Object):
        @dbus.service.method(properties, in_signature='ss', out_signature='v')
        def Get(self, interface, prop):
            if interface == chooser and prop == 'version':
                return dbus.UInt32(4, variant_level=1)
            raise dbus.exceptions.DBusException('Unknown property', name='org.freedesktop.DBus.Error.InvalidArgs')

        @dbus.service.method(properties, in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            return {'version': dbus.UInt32(4)} if interface == chooser else {}

        def request(self, options, sender, save):
            token = str(options.get('handle_token', secrets.token_hex(12)))
            if not re.fullmatch(r'[A-Za-z0-9_]{1,200}', token) or len(active) >= 4:
                raise dbus.exceptions.DBusException('Invalid or excessive request', name='org.freedesktop.DBus.Error.InvalidArgs')
            path = '/org/freedesktop/portal/desktop/request/' + sender[1:].replace('.', '_') + '/' + token
            if path in active:
                raise dbus.exceptions.DBusException('Duplicate request', name='org.freedesktop.DBus.Error.InvalidArgs')
            return dbus.ObjectPath(Request(sender, token, options, save).path)

        @dbus.service.method(chooser, in_signature='ssa{sv}', out_signature='o', sender_keyword='sender')
        def OpenFile(self, parent, title, options, sender=None):
            return self.request(options, sender, False)

        @dbus.service.method(chooser, in_signature='ssa{sv}', out_signature='o', sender_keyword='sender')
        def SaveFile(self, parent, title, options, sender=None):
            return self.request(options, sender, True)

    portal = Portal(name, '/org/freedesktop/portal/desktop')
    def disconnected(owner, old, new):
        if owner.startswith(':') and not new:
            for request in list(active.values()):
                if request.sender == owner:
                    request.finish(1)
    bus.add_signal_receiver(disconnected, signal_name='NameOwnerChanged', dbus_interface='org.freedesktop.DBus', path='/org/freedesktop/DBus')
    Path(os.environ['XDG_RUNTIME_DIR'], 'picker-ready').touch()
    Gtk.main()
    return portal


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--render':
        render_worker(sys.argv[2])
    else:
        main()
