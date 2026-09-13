# Pocket ID browser sessions

This fork adds an optional OIDC mode to the **Wayland** image. The container and
nginx stay running. Each authorized login starts a private compositor, Selkies,
audio service and Brave. The whole private desktop closes when the user ends the application session, the
session expires, or the last streaming connection exceeds its reconnect grace.

Up to `MAX_CONCURRENT_SESSIONS` users can enter at once (default 2). Each identity
can have one active desktop; a second open tab cannot replace its active stream. Each OIDC issuer/subject pair
gets a private home and browser profile under `/config/users`, running under a
separate Linux UID. Cookies, settings, extensions, cache and browser downloads
persist there. Existing `/config/profile` data is not automatically assigned to a
new identity. OS-level file permissions prevent another profile UID reading it.

## Configure

Create an OIDC client in Pocket ID with this exact callback for the example host:

`https://web.penalte.pt/auth/callback`

Copy `.env.example` to `.env`, then configure:

```dotenv
OIDC_ENABLED=true
OIDC_ISSUER_URL=https://id.penalte.pt
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
OIDC_SCOPES=openid profile email groups
OIDC_ALLOWED_GROUPS=
SESSION_MAX_SECONDS=3600
DISCONNECT_GRACE_SECONDS=30
SESSION_CONNECT_TIMEOUT=90
SESSION_START_TIMEOUT=90
MAX_CONCURRENT_SESSIONS=2
MAX_UPLOAD_MB=1024
IMAGE_NAME=ghcr.io/penalte/brave-origin:1.2.0-beta.4
```

All values are Docker environment variables. For a mounted secret, set
`OIDC_CLIENT_SECRET_FILE` to its container path and add a read-only secret mount in
your Compose override. The file takes precedence over `OIDC_CLIENT_SECRET`.
Never commit `.env` or a client secret. The external HTTPS address is
automatically detected from the incoming Host header,
including a nonstandard port. The reverse proxy must preserve the public Host.
Forwarded host/protocol headers are ignored. Register that HTTPS address plus
`/auth/callback` in Pocket ID; only register addresses you control. Each login
flow and browser session is bound to the address where it started.
`OIDC_ISSUER_URL` is the issuer, not the discovery URL.

`OIDC_ALLOWED_GROUPS` optionally restricts admission to at least one listed group.
`OIDC_GROUPS_CLAIM` selects the claim, default `groups`. The provider must include
the selected group claim in the ID token when group restriction is enabled.
Only asymmetric RS256, ES256 or EdDSA ID-token signatures are accepted. The
implementation validates discovery issuer, signature, audience, subject, nonce,
expiry and authorized party, and uses authorization-code flow with S256 PKCE.

Pull the numbered Linux AMD64 testing image from this fork:

```bash
docker compose pull
docker compose up -d --no-build
```

Forward `web.penalte.pt` to container HTTPS port 8443. Preserve the original Host
header and WebSocket upgrades. Publish only nginx's port, never internal 8082 or
8084. Use a trusted external certificate. The internal nginx certificate is
self-signed unless replaced using the project's normal certificate configuration.

To build locally instead, set `IMAGE_NAME=brave-origin:pocket-id` and run
`docker compose build` before starting it. Upstream images lack this feature.
Testing releases and dependency scan reports are on this fork's GitHub Releases
page; review the unresolved findings before testing with sensitive browsing data.

For Unraid, use the Wayland template with this fork's testing image and add
the same OIDC variables; the upstream template defaults to an upstream image.
The X11 image is not supported by this integration.

## Session behavior

- No browser runs while idle. An idle gateway remains healthy.
- The application lifetime is bounded by `SESSION_MAX_SECONDS` and the ID token's
  expiry, whichever comes first. No silent refresh extends browser occupancy.
- Before the first stream attaches, `SESSION_CONNECT_TIMEOUT` permits loading the
  interface. After a disconnect, `DISCONNECT_GRACE_SECONDS` permits reconnection.
  Network failure detection also requires WebSocket heartbeat timeout.
- End session performs local application logout. It does not log the user out of
  Pocket ID or unrelated applications. Logging out of Pocket ID elsewhere does
  not promise immediate browser termination; no back-channel logout is implemented.
- Only the session owner can manage their desktop. Owner-created guest links can
  share its stream; terminal commands remain disabled. Selkies uploads and downloads use that identity's private `Downloads`
  folder, also used by Brave. `MAX_UPLOAD_MB` bounds uploads. Other identities
  cannot access the session socket or home directory.
- Closing the last browser window ends the app session once its process exits.
- Automatic updates run while idle. Admission stays closed during installation.
  An update that fails without touching the installed browser reopens admission;
  only an interrupted package transaction leaves the gateway locked. After a
  failed cleanup the gateway retries reconciliation every 30 seconds and reopens
  once no profile process remains, so a transient fault is not a lasting outage.
  Container restart recovers conservatively and requires a fresh login.
- Browsers run under a private Labwc desktop nested in that user's Selkies
  capture compositor. This keeps CSS cursor sprites separate from video while
  supporting native cursor rendering when selected. Each runtime directory, audio socket and streaming socket
  is private to its identity UID. GTK and Brave use dark mode.
- A private desktop failure closes that user's session. A gateway failure uses
  the container restart policy. Normal logins/logouts never restart the container.

Legacy `profile-control.sh` backup hooks are disabled in OIDC mode. For a
consistent full backup, end the application session and stop the container, then
back up `/config`. Maintenance shutdown is distinct from normal user logout.

## Tests and limits

Selkies My files includes a Delete button for individual files, with permanent
deletion confirmed before sending the request. Only the current identity's
Downloads files can be removed; folders and symlinks cannot be deleted.

Website upload buttons and Save dialogs use a private **My files** picker. It
opens in the same Downloads folder used by Selkies transfers, supports subfolders,
multiple files and filename filters, and has no root, Desktop, Other Locations,
or arbitrary path entry. Symlinks and outside paths are rejected. Folder-upload
requests are cancelled; select individual files instead. Save names must be plain
filenames, and existing files require overwrite confirmation.
The picker has no window-manager title bar or menu; use Cancel or Escape to
close it. Brave uses its own frame with a close-only button layout (plus tab search).
“Use system titlebar and borders” is unchecked by default. This
default is applied once per identity profile, preserving subsequent user changes.

The picker runs on each desktop's private D-Bus session and must be ready before
Brave starts. If it exits, that user's desktop is closed. These are picker
restrictions, not a complete Brave filesystem sandbox: they do not restrict every
other way the browser can read files accessible to its Linux account. Other
identities' private homes remain protected by separate Linux UIDs.

Run the self-contained acceptance suite against a locally built image:

```bash
bash scripts/test-oidc.sh brave-origin:pocket-id
```

The multi-user suite launches two real desktops and navigates through their
authenticated keyboard streams. It decodes keyframes and delta frames, verifies
distinct page pixels, tests private transfers and socket permissions, and checks
independent logout. Container start time must stay unchanged across user transitions.
It also exercises website uploads and saves through the real custom picker,
Escape cancellation, path rejection and cleanup after picker failure.
Cursor tests verify text/hand shape updates, no cursor pixels in CSS mode,
native cursor rendering and private-desktop resizing.
Signed-token and interrupted-update cases are tested separately. GPU rendering,
live Pocket ID credentials and simultaneous audible playback require host testing.

In a disposable running OIDC container with no real user session, copy and execute
`tests/multi-session.py` and `tests/oidc-tokens.py` using `docker cp` and
`docker exec ... python3 /tmp/<test-file>`. The session test creates private test
profiles and launches real Brave. **Never run it against a production profile**:
it exercises crash recovery and stops managed profile processes.

The integration test uses a fake identity exchange with real gateway HTTP routes,
browser processes and Selkies video. Token tests independently exercise real signed
JWT verification. These do not prove a live Pocket ID registration, your reverse
proxy, GPU compatibility or every browser client. Upstream dependency security
findings in `SECURITY.md` remain relevant; this feature is not a clean image scan.

## Private desktop configuration and diagnostics

Docker variables are forwarded through an explicit allowlist to each identity.
OIDC secrets, authentication, socket paths, command execution and sharing cannot
be overridden through the private desktop environment.

Supported tuning variables: `SELKIES_FRAMERATE`, `SELKIES_VIDEO_BITRATE`,
`SELKIES_VIDEO_CRF`, `SELKIES_AUDIO_BITRATE`, `SELKIES_SCALING_DPI`,
`SELKIES_USE_BROWSER_CURSORS`, `SELKIES_USE_CSS_SCALING`,
`SELKIES_ENABLE_CLIPBOARD`, `SELKIES_ENABLE_BINARY_CLIPBOARD`, and
`SELKIES_MICROPHONE_ENABLED`. Values use the bundled Selkies settings syntax;
for example `SELKIES_FRAMERATE=30` sets the initial frame rate.

`AUTO_GPU` defaults to `true` and supports the bundled Selkies vendor selection
(for example `nvidia`). `DRINODE` selects the compositor render device;
`DRI_NODE` selects the encoder device. Explicit nodes must be accessible to the
private UID. `LIBVA_DRIVER_NAME` overrides VA-API driver selection. An Intel
i965 compatibility probe runs when no driver override is supplied and a matching
device is identified. `ENABLE_GPU=false` disables desktop GPU auto-selection and
Brave acceleration. GPU detection does not prove hardware encoding is active;
inspect the selected encoder in `selkies.log`.

`LANG`, `LC_ALL`, `TZ`, `XKB_DEFAULT_LAYOUT`, `XKB_DEFAULT_VARIANT`, and
`XKB_DEFAULT_OPTIONS` reach the private desktop. Locale names must be installed
in the image (`locale -a`); the default remains `C.UTF-8`.

Docker health checks now test both the broker and the HTTPS nginx endpoint.
Broker health includes aggregate session and failure counts, without identities.
An idle container with no signed-in users is healthy.

Failed launches and unexpectedly ended desktops retain log tails in
`/config/session-failures` before runtime cleanup. Only root can read this folder:
at most five archives, five logs per archive, 64 KiB per log. These are diagnostic
logs and may contain browser activity; normal logout does not create an archive.
The supervisor log includes GPU access checks and the component exit status.

The occasional small Brave startup window still receives one recovery at five
seconds. `selkies.log` records the requested mode, desktop scale, acceptance and
application-screen readback. The private session's `labwc.log` records
`[browser-geometry]` first-map dimensions and mismatched maximized commits with
configure serials. These entries omit page titles and URLs and do not change
window behavior. Collect both logs while the affected session is still active;
a normal logout removes its runtime logs.

## Streaming stack compatibility

Private desktops support the owner's gamepad through player slot 1. Browser
processes preload the matching Selkies joystick and fake-udev adapters, both
using the desktop's private `SELKIES_JS_SOCKET_PATH`. Kernel uinput gamepads
are disabled; do not map host input devices for this feature. Shared guests
remain unable to inject gamepad input, even when granted mouse/keyboard control.
Virtual slots may remain discoverable while a controller is disconnected;
Selkies releases held inputs and reuses the slots when it reconnects.

The backend and dashboard are pinned together at Selkies `v2.0.0rc0`
(`f5eb10c8b1bdbb9c8e0d8ed3deb8387bc566630e`). Capture wheels are pinned by
checksum to the matching Pixelflux `2.1.0rc0` and pcmflux `2.1.0rc0` releases.
The locked compositor uses Labwc `0.9.7` with wlroots `0.19.3`, built from
checksum-pinned sources. The standard Debian compositor remains available via
`BROWSER_LOCK_MAXIMIZED=false`. The one-time nested window recovery runs after
1.2 seconds; it is retained pending NVIDIA-host validation of the newer stack.
The stack uses upstream screen/view sizing and nested-compositor scale adoption;
the partial local resize patch has been removed. Test first login, reconnect and
resize after changing these components as a set. The viewport test checks page
corners, not only video dimensions, at 96 and 144 DPI.

## Browser network and administration

`WARP_ENABLED` sets the initial preference for newly registered users. The old
`BROWSER_NETWORK_MODE` and `BROWSER_PROXY_URL` variables are unused and can be
removed. Each active user gets a fixed loopback SOCKS5 port and one lightweight
relay worker; all WARP users share the official WARP proxy and tunnel. The
relay is closed on logout. Preferences and permissions persist beside the
profile in root-owned identity metadata.

Private OIDC desktops now require `NET_ADMIN` even when starting in direct mode:
UID-specific firewall rules allow each user to connect only to their own proxy
port, never another user's direct route or the WARP proxy itself. With Compose,
add `-f compose.warp.yaml`; on Unraid add `--cap-add=NET_ADMIN` to Extra Parameters.
Proxy ports 61000-61127 are reserved on loopback. Do not publish proxy ports or use host networking. No privileged container is
needed. Legacy direct-only mode without OIDC still works without NET_ADMIN.

WARP requires the included official package and `WARP_ACCEPT_TOS=true`, accepting
Cloudflare's terms. Map a private persistent appdata directory to
`/var/lib/cloudflare-warp` to retain registration. The WARP daemon runs only while
an active desktop needs it. A WARP outage blocks WARP users; direct users remain
online. There is no automatic direct fallback. Browser QUIC and non-proxied
WebRTC UDP remain disabled in both modes, so some calls/games may be unavailable.
The relay does not inspect HTTPS or buffer downloads to disk.

The admin panel lists all locally registered profiles, including offline users.
Names are saved on login; older profiles show a short identifier until they next
sign in. This does not enumerate people who have never logged in from the IdP.
Admins can grant **Allow disabling WARP** per user. Revoking it immediately
returns that user to WARP. Admins may change their own route without a grant.
**Force WARP for everyone** overrides all users, including admins, while retaining
saved preferences to restore when enforcement is removed. Grants, preferences
and enforcement persist across container restarts. These controls are checked
server-side against the authenticated session, origin and CSRF token.

The WARP tile toggles only the current user's route and never opens the admin
panel. It is disabled without permission or when WARP is enforced. Switching
uses a blue breathing animation, followed by the resulting connection status.
Other users' connections remain untouched. The affected user's browser tabs,
login and Wayland session stay open; existing network connections and pending
DNS work are terminated before acknowledging the new route, so downloads, calls
or streams may need retrying. WARP startup can take longer than the local switch.

The exact verified OIDC group `admin` grants administration. `OIDC_ADMIN_GROUP`
changes the group name; `OIDC_GROUPS_CLAIM` selects the claim. OIDC and nginx retain
normal network access independently of users' routes. WARP health uses a request
to Cloudflare's trace endpoint, requiring `warp=on` or `warp=plus`.

## Camera, microphone and controllers

Enable camera or microphone forwarding in the owner's Selkies panel, and allow
access in the local browser. Websites inside Brave request their own permission.
Camera forwarding uses the matching upstream V4L2 userspace adapter, with a
private socket for each desktop; no host `/dev/video0` mapping is needed.
Gamepads use the matching joystick and device-discovery adapters and private
sockets. Neither adapter is preloaded into the root broker.

Each desktop has its own PulseAudio server. `SelkiesVirtualMic` is created and
selected before Brave starts, with the input sink and source initialized at
100% (unity gain). The browser's local capture processing and hardware input
level can still affect voice volume. Camera and microphone forwarding remain owner-only.

## Guest sharing and collaboration

Use **Share desktop** in Selkies to create a unique link, choose its lifetime,
and independently enable **Give mouse and keyboard control** or **Allow gamepad**. Guests do not need
Pocket ID. Anyone holding the link has its selected permission until expiry or
revocation. Keep control links private. One guest controls input at a time; the
owner can grant/revoke it from the participant list. Control returns to the owner
when the controlling guest disconnects. The owner keeps Player 1; gamepad guests
receive one available slot from Players 2–4. A full controller pool leaves the
guest viewing and waiting for a slot. The owner can grant/revoke gamepad access
or disconnect a participant. Revocation and disconnect neutralize held controller
inputs before the slot is reused. Clipboard, files, commands, microphone,
and webcam remain unavailable through guest connections.

The admin panel has **View session** beside each online desktop. It opens a new
tab, initially view-only, and requires the administrator's active OIDC session.
Owners can see administrator viewers in the sharing panel. **Stop sharing** closes
current viewers including administrators. Logout, takeover, desktop shutdown and
expiry invalidate that desktop's links. Viewers do not extend the owner's idle
lifetime. Links and participant credentials are held in memory and disappear on
container restart. Up to eight viewers can connect to a desktop.
