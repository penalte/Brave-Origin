"""Exercise X11 clipboard ownership and exact UTF-8/PNG round trips."""
import base64
import subprocess
import time

for mime, payload in [
    ('UTF8_STRING', 'X11 clipboard: café 日本語 🎉\nSecond line\n'.encode()),
    ('image/png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')),
]:
    writer = subprocess.Popen(['xclip', '-selection', 'clipboard', '-t', mime, '-i', '-quiet'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        writer.stdin.write(payload)
        writer.stdin.close()
        time.sleep(.1)
        result = subprocess.check_output(['xclip', '-selection', 'clipboard', '-t', mime, '-o'], timeout=5)
        assert result == payload, (mime, result)
    finally:
        writer.terminate()
        writer.wait(timeout=5)
print('X11 UTF-8 and PNG clipboard round trips passed.')
