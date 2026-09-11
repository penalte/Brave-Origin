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
IMAGE_NAME=ghcr.io/penalte/brave-origin:1.2.0-beta.1
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
- Only the session owner can use their stream. Sharing and terminal commands are
  disabled. Selkies uploads and downloads use that identity's private `Downloads`
  folder, also used by Brave. `MAX_UPLOAD_MB` bounds uploads. Other identities
  cannot access the session socket or home directory.
- Closing the last browser window ends the app session once its process exits.
- Automatic updates run while idle. Admission stays closed during installation.
  An update that fails without touching the installed browser reopens admission;
  only an interrupted package transaction leaves the gateway locked. After a
  failed cleanup the gateway retries reconciliation every 30 seconds and reopens
  once no profile process remains, so a transient fault is not a lasting outage.
  Container restart recovers conservatively and requires a fresh login.
- Browsers run under a private headless Labwc desktop. Selkies captures that
  compositor directly. Each runtime directory, audio socket and streaming socket
  is private to its identity UID. GTK and Brave use dark mode.
- A private desktop failure closes that user's session. A gateway failure uses
  the container restart policy. Normal logins/logouts never restart the container.

Legacy `profile-control.sh` backup hooks are disabled in OIDC mode. For a
consistent full backup, end the application session and stop the container, then
back up `/config`. Maintenance shutdown is distinct from normal user logout.

## Tests and limits

Run the self-contained acceptance suite against a locally built image:

```bash
bash scripts/test-oidc.sh brave-origin:pocket-id
```

The multi-user suite launches two real desktops and navigates through their
authenticated keyboard streams. It decodes keyframes and delta frames, verifies
distinct page pixels, tests private transfers and socket permissions, and checks
independent logout. Container start time must stay unchanged across user transitions.
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
