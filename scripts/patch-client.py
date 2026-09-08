#!/usr/bin/env python3
"""Apply one audited hook to the checksum-pinned upstream KasmVNC client."""
import hashlib
from pathlib import Path

web = Path('/usr/share/kasmvnc/www')
bundle, = (web / 'assets').glob('ui-*.js')
source = bundle.read_text()
assert hashlib.sha256(bundle.read_bytes()).hexdigest() == '351b7a0b3b05261b8bff17419e28a8216f691e2eee1e6800842f4773a143c727', 'KasmVNC client changed; review the clipboard integration'
needle = 'addClipboardHandlers(){'
assert source.count(needle) == 1
source = 'import {installClipboard} from "../clipboard-client.js";\n' + source.replace(needle, needle + 'installClipboard(o);')
bundle.write_text(source)
