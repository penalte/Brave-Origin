#!/usr/bin/python3
"""Private FileChooser portal. The UI only traverses a no-symlink Downloads tree."""
import fnmatch
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import sys


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
                    if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                        result.append((name, stat.S_ISDIR(info.st_mode), info.st_size))
                except OSError:
                    continue
            return sorted(result, key=lambda item: (not item[1], item[0].casefold()))
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


def main():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    GLib.set_prgname('brave-files')
    Gtk.init([])
    Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', True)
    files = Files(Path.home() / 'Downloads')
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
            w.set_default_size(680, 440)
            w.connect('delete-event', lambda *_: (self.finish(1), True)[1])
            w.connect('key-press-event', lambda _, event: (self.finish(1), True)[1] if event.keyval == 65307 else False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
            w.add(box)
            top = Gtk.Box(spacing=10)
            back = Gtk.Button(label='Up')
            back.connect('clicked', self.up)
            self.back = back
            top.pack_start(back, False, False, 0)
            self.heading = Gtk.Label(label='My files', xalign=0)
            self.heading.set_ellipsize(3)
            top.pack_start(self.heading, True, True, 0)
            refresh = Gtk.Button(label='Refresh')
            refresh.connect('clicked', lambda *_: self.refresh())
            top.pack_end(refresh, False, False, 0)
            box.pack_start(top, False, False, 0)
            self.model = Gtk.ListStore(str, str, bool)
            self.tree = Gtk.TreeView(model=self.model)
            self.tree.append_column(Gtk.TreeViewColumn('Name', Gtk.CellRendererText(), text=0))
            self.tree.get_column(0).set_expand(True)
            self.tree.append_column(Gtk.TreeViewColumn('Size', Gtk.CellRendererText(), text=1))
            self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE if self.options.get('multiple') and not self.save else Gtk.SelectionMode.SINGLE)
            self.tree.connect('row-activated', self.activate)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.tree)
            box.pack_start(scroll, True, True, 0)
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

        def refresh(self):
            self.model.clear()
            self.back.set_sensitive(bool(self.parts))
            self.heading.set_text('My files' + (' / ' + ' / '.join(self.parts) if self.parts else ''))
            try:
                for filename, directory, size in files.entries(self.parts):
                    if not directory and self.filters:
                        patterns = self.filters[self.filter.get_active()][1]
                        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                        if patterns and not any(fnmatch.fnmatchcase(filename if kind == 0 else mime, str(pattern)) for kind, pattern in patterns):
                            continue
                    self.model.append([filename, 'Folder' if directory else f'{size / 1024:.1f} KB', directory])
                self.error.set_text('')
            except (OSError, ValueError):
                self.error.set_text('This folder is no longer available. Use Up or Refresh.')

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
    main()
