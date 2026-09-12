# Labwc browser maximization policy

Based on upstream Labwc 0.8.3, licensed GPL-2.0-only. Modified for Brave Origin
on September 8, 2026. The Dockerfile pins the official source archive by SHA-256.

The modified compositor rejects minimization, restoring either axis of an
already maximized window, and interactive movement/resizing of a maximized
window. Rejected restore and minimize requests send a Wayland configure reply
so Chromium does not retain its own minimized state and stop processing input.
Fullscreen, output resizing, client-side decorations, dialogs, and popups use
the existing upstream implementation. This is a window policy, not a security
boundary or a restriction on closing tabs or the browser.

`labwc-browser` is built separately from the stock Debian `labwc` package;
`BROWSER_LOCK_MAXIMIZED=false` selects the stock binary. Brave is not patched.
The build disables unused XWayland and window-icon support, enables compiler
hardening, and runs upstream tests. No build tools enter the final image.

The complete modified Labwc source, its license, and the Dockerfile build recipe
are shipped in `/usr/local/share/brave-origin/labwc-source.tar.xz`. The patch in
this directory and the Dockerfile reproduce those sources and the binary.

Geometry diagnostics log the first window map and changed maximized commits that
differ from the pending configure size, using the `[browser-geometry]` prefix.
They include dimensions, scale and configure serials, but no titles or URLs.
The diagnostics do not issue configure requests or change window policy.
