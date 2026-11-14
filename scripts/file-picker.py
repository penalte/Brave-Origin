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


def render(fd, name, width, height):
    """PNG bytes picturing a file: an image scaled down, a PDF's first page or a frame of a video."""
    if kind(name) == 'video':
        # One still frame, taken by a separate short-lived program with a deadline.
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('XDG_RUNTIME_DIR')) as scratch:
            target = os.path.join(scratch, 'frame.png')
            subprocess.run(['ffmpegthumbnailer', '-i', f'/dev/fd/{fd}', '-o', target, '-c', 'png',
                            '-s', str(max(width, height)), '-t', '10%'], pass_fds=[fd], timeout=12, check=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(target, 'rb') as frame:
                return frame.read(32 * 1024 * 1024)
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib
    if kind(name) == 'pdf':
        import cairo
        gi.require_version('Poppler', '0.18')
        from gi.repository import Poppler
        with os.fdopen(os.dup(fd), 'rb') as source:
            data = source.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError('Too large to preview')
        page = Poppler.Document.new_from_bytes(GLib.Bytes.new(data), None).get_page(0)
        page_width, page_height = page.get_size()
        scale = min(width / page_width, height / page_height)
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, int(page_width * scale)), max(1, int(page_height * scale)))
        context = cairo.Context(surface)
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.scale(scale, scale)
        page.render(context)
        import io
        output = io.BytesIO()
        surface.write_to_png(output)
        return output.getvalue()
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(f'/proc/self/fd/{fd}', width, height, True)
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    return pixbuf.save_to_bufferv('png', [], [])[1]


def render_worker(root):
    """Draw previews for the picker in a separate process.

    Image and PDF decoders read whatever the user downloaded. Here a decoder
    that crashes or hangs costs one preview; inside the picker it would end the
    picker, and the desktop closes with it.
    """
    files = Files(root)
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    for line in source:
        request = json.loads(line)
        payload = b''
        try:
            fd = files.open(request['parts'])
            try:
                info = os.fstat(fd)
                limit = 64 * 1024 ** 3 if kind(request['parts'][-1]) == 'video' else 256 * 1024 ** 2
                if stat.S_ISREG(info.st_mode) and info.st_size <= limit:
                    payload = render(fd, request['parts'][-1], request['width'], request['height'])
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

        def request(self, parts, width, height, callback, first=False):
            self.count += 1
            item = (self.count, {'id': self.count, 'parts': parts, 'width': width, 'height': height}, callback)
            self.queue.insert(0, item) if first else self.queue.append(item)
            self.pump()

        def forget(self):
            self.queue.clear()

        def pump(self):
            if self.busy or not self.queue:
                return
            try:
                if self.process is None or self.process.poll() is not None:
                    self.start()
                number, request, callback = self.queue.pop(0)
                self.busy = (number, callback)
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
            picture = None
            if payload:
                try:
                    loader = GdkPixbuf.PixbufLoader.new_with_type('png')
                    loader.write(payload)
                    loader.close()
                    picture = loader.get_pixbuf()
                except GLib.Error:
                    picture = None
            if busy and busy[0] == number:
                busy[1](picture)
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
        """Plays a video in the preview pane.

        ffmpeg decodes in its own processes, one sending small frames down a
        pipe at the video's own speed and one sending the sound to the desktop.
        The picker only paints finished frames, so a video that breaks a decoder
        stops the playback and nothing else.
        """
        def __init__(self, picture, changed):
            self.picture, self.changed = picture, changed
            self.video = self.audio = self.watch = None
            self.buffer, self.frame, self.size, self.paused = b'', 0, (0, 0), False

        @property
        def playing(self):
            return self.video is not None or self.audio is not None

        def listening(self):
            if self.audio and self.audio.poll() is None:
                return True
            self.watch = None  # Returning False removes this timer.
            self.stop()
            return False

        def start(self, parts, width=320, height=420):
            self.stop()
            try:
                fd = files.open(parts)
            except (OSError, ValueError):
                return False
            if kind(parts[-1]) == 'audio':
                try:
                    self.audio = subprocess.Popen(
                        ['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', f'/dev/fd/{fd}', '-vn', '-sn',
                         '-f', 'pulse', 'My files preview'],
                        pass_fds=[fd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    return False
                finally:
                    os.close(fd)
                self.paused = False
                self.watch = GLib.timeout_add(400, self.listening)
                self.changed()
                return True
            try:
                probe = subprocess.run(
                    ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                     '-of', 'json', f'/dev/fd/{fd}'], pass_fds=[fd], capture_output=True, timeout=8,
                    stdin=subprocess.DEVNULL)
                stream = json.loads(probe.stdout)['streams'][0]
                scale = min(width / int(stream['width']), height / int(stream['height']), 1)
                # Even sizes: several decoders and scalers require them.
                w = max(2, int(int(stream['width']) * scale) // 2 * 2)
                h = max(2, int(int(stream['height']) * scale) // 2 * 2)
                source = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-re', '-i', f'/dev/fd/{fd}']
                self.video = subprocess.Popen(
                    source + ['-an', '-sn', '-vf', f'scale={w}:{h},fps=20', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                    pass_fds=[fd], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.audio = subprocess.Popen(
                    source + ['-vn', '-sn', '-f', 'pulse', 'My files preview'],
                    pass_fds=[fd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError):
                self.stop()
                return False
            finally:
                os.close(fd)
            self.size, self.frame, self.buffer, self.paused = (w, h), w * h * 3, b'', False
            os.set_blocking(self.video.stdout.fileno(), False)
            self.watch = GLib.io_add_watch(self.video.stdout.fileno(), GLib.IO_IN | GLib.IO_HUP, self.readable)
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
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.tree)
            middle = Gtk.Box(spacing=12)
            middle.pack_start(scroll, True, True, 0)
            # Quick preview of the selected file, hidden until asked for.
            self.pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.pane.set_size_request(320, -1)
            self.pane.set_no_show_all(True)
            self.picture = Gtk.Image()
            self.text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
            self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_scroll = Gtk.ScrolledWindow()
            text_scroll.add(self.text)
            self.stack = Gtk.Stack()
            self.stack.add_named(self.picture, 'picture')
            self.stack.add_named(text_scroll, 'text')
            self.stack.add_named(Gtk.Label(label=''), 'none')
            self.details = Gtk.Label(xalign=0)
            self.details.set_line_wrap(True)
            self.details.set_selectable(False)
            self.pane.pack_start(self.stack, True, True, 0)
            self.player = Player(self.picture, self.player_changed)
            self.play = Gtk.Button(label='Play')
            self.play.set_tooltip_text('Play this file here')
            self.play.set_no_show_all(True)
            self.play.connect('clicked', self.play_video)
            self.pane.pack_start(self.play, False, False, 0)
            self.pane.pack_start(self.details, False, False, 0)
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
            if self.pane.get_visible():
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
            self.pane.set_visible(not self.pane.get_visible())
            if self.pane.get_visible():
                for child in self.pane.get_children():
                    if child is not self.play:
                        child.show_all()
                self.window.resize(max(self.window.get_size()[0], 1080), self.window.get_size()[1])
            self.show_preview()

        def player_changed(self):
            if not self.finished:
                self.play.set_label('Play' if not self.player.playing else 'Resume' if self.player.paused else 'Pause')

        def play_video(self, *_):
            if self.player.playing:
                self.player.pause()
                return
            chosen = self.chosen()
            if chosen and not chosen[1] and kind(chosen[0]) in ('video', 'audio'):
                if kind(chosen[0]) == 'video':
                    self.stack.set_visible_child_name('picture')
                if not self.player.start(self.parts + [chosen[0]]):
                    self.details.set_text(self.details.get_text() + '\nThis file cannot be played here.')

        def show_preview(self):
            if self.finished:
                return
            self.player.stop()
            self.play.set_visible(False)
            if not self.pane.get_visible():
                return
            self.preview = getattr(self, 'preview', 0) + 1
            chosen = self.chosen()
            if not chosen:
                self.stack.set_visible_child_name('none')
                self.details.set_text('Select one file to preview it.')
                return
            name, directory = chosen
            row = next(row for row in self.model if row[0] == name)
            sort = 'Folder' if directory else (mimetypes.guess_type(name)[0] or 'File')
            self.details.set_text(f'{name}\n{sort}' + ('' if directory else ' · ' + row[1]) + f'\nModified {row[5]}')
            self.stack.set_visible_child_name('none')
            if directory:
                return
            shape = kind(name)
            self.play.set_visible(shape in ('video', 'audio'))
            if shape == 'audio':
                self.details.set_text(self.details.get_text() + '\nPress Play to listen.')
            if shape in ('image', 'pdf', 'video'):
                previews.request(self.parts + [name], 320, 420,
                                 lambda picture, preview=self.preview: self.picture_ready(preview, picture), first=True)
            elif shape == 'text':
                try:
                    fd = files.open(self.parts + [name])
                    try:
                        sample = os.read(fd, 65536)
                    finally:
                        os.close(fd)
                    self.text.get_buffer().set_text(sample.decode('utf-8', 'replace').replace('\x00', ' '))
                    self.stack.set_visible_child_name('text')
                except (OSError, ValueError):
                    pass

        def picture_ready(self, preview, picture):
            if self.finished or preview != self.preview or self.player.playing:
                return
            if picture:
                self.picture.set_from_pixbuf(picture)
                self.stack.set_visible_child_name('picture')
                if kind((self.chosen() or ('',))[0]) == 'video':
                    self.details.set_text(self.details.get_text() + '\nPress Play to watch it here.')
            else:
                self.details.set_text(self.details.get_text() + '\nNo preview available.')

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
