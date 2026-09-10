# Pocket ID browser sessions

This fork adds an optional OIDC mode to the **Wayland** image. The container, nginx,
Selkies, compositor and audio service stay running. Brave starts only after an
authorized login and closes when the user ends the application session, the
session expires, or the last streaming connection exceeds its reconnect grace.

Only one user can enter at a time. A second login receives a busy response, and a
second open tab cannot replace the active stream. Each OIDC issuer/subject pair
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
IMAGE_NAME=ghcr.io/penalte/brave-origin:1.1.0-beta.2
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
- Only the session owner can use the stream. Sharing, terminal commands, secondary
  sessions and the Selkies upload/download interface are disabled in OIDC mode.
  Downloads inside Brave remain in the user's private home. This avoids exposing
  a shared transfer directory; a per-user web transfer UI is not implemented.
- Closing the last browser window ends the app session once its process exits.
- Automatic updates run while idle. Admission stays closed during installation.
  A failed cleanup or failed update leaves the gateway locked for administrator
  attention. Container restart recovers conservatively and requires a fresh login.
- A desktop/gateway failure shuts down the container for its configured restart
  policy. Normal user logins/logouts never start or stop the container.

Legacy `profile-control.sh` backup hooks are disabled in OIDC mode. For a
consistent full backup, end the application session and stop the container, then
back up `/config`. Maintenance shutdown is distinct from normal user logout.

## Tests and limits

Run the self-contained acceptance suite against a locally built image:

```bash
bash scripts/test-oidc.sh brave-origin:pocket-id
```

Verified locally on 10 September 2026: full source image build, shell/Python syntax,
Python dependency consistency, Compose configuration, signed-token rejection cases,
simultaneous admission, real video over the gateway, second-tab rejection, live
socket logout, private-profile file permissions, audio access, session expiry and
disconnect shutdown. Container start time remained unchanged across user transitions.
Legacy password mode also became healthy and returned 401 without credentials and
200 with credentials. GPU rendering and live Pocket ID credentials were not tested.

In a disposable running OIDC container with no real user session, copy and execute
`tests/oidc-session.py` and `tests/oidc-tokens.py` using `docker cp` and
`docker exec ... python3 /tmp/<test-file>`. The session test creates private test
profiles and launches real Brave. **Never run it against a production profile**:
it exercises crash recovery and stops managed profile processes.

The integration test uses a fake identity exchange with real gateway HTTP routes,
browser processes and Selkies video. Token tests independently exercise real signed
JWT verification. These do not prove a live Pocket ID registration, your reverse
proxy, GPU compatibility or every browser client. Upstream dependency security
findings in `SECURITY.md` remain relevant; this feature is not a clean image scan.
