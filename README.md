# Brave Origin in Docker

Run Brave Origin in a web browser over HTTPS. Bookmarks, settings, extensions and
downloads stay in a persistent folder. The container uses Debian 13 Trixie Slim
and the official stable `brave-origin` package.

This fork adds **[Pocket ID / OIDC sessions](POCKET-ID.md)**: several people can
sign in with their own identity and each gets a private desktop, browser profile
and download folder, isolated by a separate Linux user. Upstream image tags do
not include this integration; use this fork's images or build it yourself.

> **Testing release: `1.2.0-beta.7`.** The OIDC multi-user mode is under active
> testing and carries unresolved dependency findings, so it is not security
> cleared for production. See [security guidance](SECURITY.md) and the
> [release notes](CHANGELOG.md).

## Two ways to run it

| Mode | Who can sign in | What each person gets |
| --- | --- | --- |
| **Password** (default) | One shared login | One shared browser profile in `/config/profile` |
| **Pocket ID / OIDC** | Any identity your provider admits | A private desktop, profile and `Downloads` folder under `/config/users`, on its own Linux UID |

Password mode is the default and is unchanged. Set `OIDC_ENABLED=true` to switch;
the two modes do not share profile data.

### Private multi-user sessions

With OIDC enabled, the container and its HTTPS proxy stay running while nothing
else does. No browser and no desktop exist while the service is idle. Each
authorised login starts that identity's own compositor, streaming server, audio
service and Brave, and the whole stack stops when the person signs out, their
session expires, or their connection stays gone past the reconnect grace.

Up to `MAX_CONCURRENT_SESSIONS` people (default 2) can be signed in at once, and
each identity may hold one desktop at a time. Sessions do not share a compositor,
an audio socket, a streaming socket or a home directory, and one person's logout
never disturbs another's session.

Website uploads and Save dialogs use a private **My files** picker that browses
only that identity's `Downloads` tree. It rejects symlinks and paths outside that
tree. These are picker restrictions, not a complete filesystem sandbox for Brave.

[POCKET-ID.md](POCKET-ID.md) covers provider registration, every session setting,
and the limits of what the isolation does and does not promise.

## Unraid

The [Unraid template](templates/brave-origin.xml) installs **upstream** images
from `ghcr.io/shoyrock/brave-origin`, which do not include OIDC sessions. It
defaults to **Wayland stable (`latest`)**, uses bridge networking, HTTPS, and
Unraid's user ID 99 and group ID 100.

When installed through **Apps / Community Applications**, these choices appear:

| Choice | Image tag | Container name | Appdata folder | HTTPS port |
| --- | --- | --- | --- | --- |
| **Default: Wayland stable** | `latest` | `Brave-Origin` | `/mnt/user/appdata/brave-origin` | `8443` |
| Wayland beta | `beta` | `Brave-Origin-Beta` | `/mnt/user/appdata/brave-origin-beta` | `8444` |
| X11 stable | `x11` | `Brave-Origin-X11` | `/mnt/user/appdata/brave-origin-x11` | `8445` |
| X11 beta | `x11-beta` | `Brave-Origin-X11-Beta` | `/mnt/user/appdata/brave-origin-x11-beta` | `8446` |

Select a channel, set a password, review the storage path and port, then apply.
Open the container's **WebUI** menu to start browsing. The separate defaults let
you test another channel alongside an existing container; choose a different port
if one is already in use.

Beta refers to the container's development channel. Every channel installs the
official stable Brave Origin browser package.

To run this fork's OIDC build on Unraid, use the Wayland template, change
**Repository** to this fork's testing image, and add the OIDC variables from
[POCKET-ID.md](POCKET-ID.md). The X11 edition does not support OIDC sessions.

### Manual installation

If Brave-Origin is not yet listed in Apps, install the template from the Unraid
terminal:

```bash
curl -fL --create-dirs https://raw.githubusercontent.com/shoyrock/Brave-Origin/main/templates/brave-origin.xml -o /boot/config/plugins/dockerMan/templates-user/my-brave-origin.xml
```

In **Docker → Add Container**, select **Brave-Origin**. This manual screen does
not show the Community Applications channel menu. To choose another channel,
enable **Advanced View**, change **Repository** to the desired tag, and set the
name, appdata folder and host port from the table above.

For Intel or AMD graphics, add a **Device** mapping from `/dev/dri` to
`/dev/dri`. Leave it out on systems without that device. Installation through the
Unraid web interface has not been verified on this development host.

### Changing an existing installation

Edit the container, enable **Advanced View**, and change the tag in
**Repository**. Existing installations keep their saved name, port and appdata
path; new template defaults do not change them automatically. Back up appdata
before switching channels, and never run two channels against the same live
profile.

## Other Linux hosts (Docker Compose)

You need an x86-64 Linux host with Docker and Docker Compose v2. GPU access is
optional.

```bash
git clone https://github.com/penalte/Brave-Origin.git
```

```bash
cp .env.example .env
```

For password mode, set `AUTH_PASSWORD` in `.env`. For OIDC mode, set
`OIDC_ENABLED=true` and the provider settings from [POCKET-ID.md](POCKET-ID.md).
Then start it:

```bash
docker compose pull
```

```bash
docker compose up -d --no-build
```

Open `https://YOUR-SERVER-IP:8443`. The container creates a self-signed
certificate, so your browser will warn you. Supply your own certificate for a
trusted connection, as described below.

Set `IMAGE_NAME` in `.env` to choose a version or registry. To build from source,
run `docker compose build` followed by `docker compose up -d --no-build`.

## Copy and paste

Use Ctrl+C and Ctrl+V inside the remote session. On macOS, use the shortcuts your
client browser supports. Text moves in both directions, including Unicode and
multiple lines. Image clipboard transfer works when the client enables it.

Clipboard access depends on your client browser's permissions and a secure
context. Allow access when prompted and keep the session page focused. If
automatic access is blocked, open **Clipboard** in the sidebar, enter your text,
click **Send to session**, then paste inside Brave. Clipboard permissions and
image support vary by client browser.

## Settings

Set these in `.env`, the Unraid template, or your container's environment.

| Setting | Default | Purpose |
| --- | --- | --- |
| `CONFIG_PATH` | `./appdata` | Compose host folder mounted at `/config`. |
| `IMAGE_NAME` | `brave-origin:pocket-id` | Compose image and release channel. |
| `WEB_PORT` | `8443` | Compose host port. The container always listens on 8443. |
| `PUID` / `PGID` | `1000` / `1000` | Nonzero user and group IDs for browser files. In OIDC mode both must sit outside 200000–1000199999, which is reserved for private profiles. |
| `UMASK` | `022` | File creation permissions. |
| `TZ` | `Etc/UTC` | Timezone. |
| `AUTH_ENABLED` | `true` | Require a login in password mode. Ignored when `OIDC_ENABLED=true`. |
| `AUTH_USER` | `brave` | Initial login username. |
| `AUTH_PASSWORD` | Empty | Initial password; required unless a password file or saved credentials exist. |
| `AUTH_PASSWORD_FILE` | Empty | Path inside the container to a mounted password file. |
| `AUTO_UPDATE` | `true` | Update the browser inside the running container. |
| `UPDATE_INTERVAL` | `21600` | Seconds between update checks (six hours). |
| `MIN_UPDATE_FREE_SPACE_MB` | `1024` | Free disk space required before downloading an update. |
| `BRAVE_ORIGIN_VERSION` | `latest` | Browser package version to request. Older versions are never installed over newer ones. |
| `DOWNGRADE_RETRY_INTERVAL` | `300` | Seconds before retrying a session blocked by an older browser. |
| `BRAVE_STARTUP_TIMEOUT` | `15` | Seconds the resume command waits before reporting that startup is pending. |
| `ENABLE_AUDIO` | `true` | Stream session audio. |
| `ENABLE_GPU` | `true` | Use available GPU hardware for browser rendering. |
| `DRI_NODE` | `/dev/dri/renderD128` | Render device when a GPU is passed through. |
| `DISPLAY_AUTO_RESIZE` | `true` | Resize the desktop to the browser window, including 1440p, 4K and ultrawide. |
| `DISPLAY_WIDTH` / `DISPLAY_HEIGHT` | `1920` / `1080` | Fixed desktop size, used only when `DISPLAY_AUTO_RESIZE=false`. |
| `BROWSER_LOCK_MAXIMIZED` | `true` | Keep Brave maximized. Set `false` to allow minimizing, restoring and dragging. |
| `BRAVE_FLAGS` | Empty | Extra space-separated browser arguments. Shell quoting is not interpreted; flags that disable the sandbox or change the profile are rejected. |
| `BROWSER_POLICY` | Empty | Extra Chromium/Brave managed policies as one JSON object, merged over the built-in blocks below. `DownloadDirectory` is fixed and cannot be overridden. |
| `CONTAINER_HOSTNAME` | `brave-origin` | Compose container hostname. |

### Locked browser features

The image ships a managed policy at `/etc/brave/policies/managed/policies.json`,
the same mechanism an organisation would use. It blocks the ways a session could
open a browsing context outside its managed profile, or reach the host's network:

| Policy | Effect |
| --- | --- |
| `IncognitoModeAvailability: 1` | No Incognito windows. |
| `TorDisabled: true` | No private window with Tor. |
| `BrowserGuestModeEnabled: false` | No guest profile. |
| `BrowserAddPersonEnabled: false` | Cannot add another profile. |
| `EnableMediaRouter: false` | Casting and device discovery off. |
| `ShowCastIconInToolbar: false` | No cast button. |
| `DownloadDirectory` | Forced to the session's private `Downloads` folder. |

Add or relax policies with `BROWSER_POLICY`, for example to also disable
developer tools and profile sync:

```bash
BROWSER_POLICY={"DeveloperToolsAvailability":2,"SyncDisabled":true}
```

Malformed JSON stops startup rather than silently ignoring the setting. Confirm
what the browser actually applied by opening `brave://policy` inside a session.

### OIDC session settings

These apply only when `OIDC_ENABLED=true`. [POCKET-ID.md](POCKET-ID.md) explains
each one in full.

| Setting | Default | Purpose |
| --- | --- | --- |
| `OIDC_ENABLED` | `false` | Replace the shared password login with per-identity private desktops. |
| `OIDC_ISSUER_URL` | Empty | Provider issuer, not the discovery URL. |
| `OIDC_CLIENT_ID` | Empty | Registered client identifier. |
| `OIDC_CLIENT_SECRET` | Empty | Client secret. Prefer `OIDC_CLIENT_SECRET_FILE` with a mounted secret. |
| `OIDC_SCOPES` | `openid profile email groups` | Requested scopes; must include `openid`. |
| `OIDC_ALLOWED_GROUPS` | Empty | Optional comma-separated groups allowed to sign in. Empty admits every user of the client. |
| `OIDC_GROUPS_CLAIM` | `groups` | ID-token claim carrying group membership. |
| `MAX_CONCURRENT_SESSIONS` | `2` | Private desktops allowed at once. Each one costs real CPU and memory. |
| `MAX_UPLOAD_MB` | `1024` | Largest single upload through the session's file transfer. It does not cap total stored files. |
| `SESSION_MAX_SECONDS` | `3600` | Session lifetime ceiling. The ID token's own expiry can end it sooner. |
| `SESSION_CONNECT_TIMEOUT` | `90` | Seconds to attach the first stream before the desktop is torn down. |
| `SESSION_START_TIMEOUT` | `90` | Seconds a private desktop may take to become ready. |
| `DISCONNECT_GRACE_SECONDS` | `30` | Reconnect window after the last stream drops. |

The older `KASM_AUTH_ENABLED`, `KASM_USER`, `KASM_PASSWORD` and
`KASM_PASSWORD_FILE` names remain accepted; the matching `AUTH_*` setting takes
precedence.

Saved credentials take precedence over password environment variables. On first
setup a password file takes precedence over `AUTH_PASSWORD`; an unreadable or
empty file stops startup. New and reset passwords use bcrypt hashes.

To change a saved password:

```bash
docker exec brave-origin /usr/local/bin/reset-password.sh --generate
```

The generated password is printed to that command's output. Save it securely.
Resetting a password does not enable authentication if you disabled it.

## Window behavior

Brave stays maximized: minimize, restore and window dragging cannot take it off
screen or make it smaller. Its title-bar buttons stay visible, and tabs, the
address bar and automatic resizing keep working. Set
`BROWSER_LOCK_MAXIMIZED=false` and recreate the container to restore the previous
controls. In Unraid this is **Keep Browser Maximized**.

This belongs to the container's window manager. Brave remains the official,
unmodified package and updates independently. The image ships the modified Labwc
0.8.3 source at `/usr/local/share/brave-origin/labwc-source.tar.xz`; its build
recipe is in `Dockerfile` and its changes are in
`patches/labwc/lock-maximized.patch`.

## Storage and backups

| Container path | Contents |
| --- | --- |
| `/config/profile` | Password mode: browser profile, bookmarks, history and extensions. |
| `/config/downloads` | Password mode: downloads and transferred files. |
| `/config/users` | OIDC mode: one private home and profile per identity, each owned by its own Linux UID. |
| `/config/state` | Locks, version records, status and logs. |
| `/config/ssl` | HTTPS certificate and private key. |
| `/config/.passwd` | Saved login credentials. |

Back up the whole appdata folder while the container is stopped. In password
mode you can pause the browser instead:

```bash
docker exec brave-origin /usr/local/bin/profile-control.sh quiesce
```

Back up after that command succeeds, then resume:

```bash
docker exec brave-origin /usr/local/bin/profile-control.sh resume
```

The pause waits for the profile lock and flushes writes before reporting success.
A failed command means the profile is not ready for backup, and a hold survives
restarts until you run `resume`. These hooks are disabled in OIDC mode: end the
sessions and stop the container for a consistent backup.

The `/config` mount root, login file, TLS directory, lifetime locks and every
private home's parent belong to root. Do not recursively change ownership of the
appdata folder. Nginx access and error logs are available through `docker logs`.

Changing `PUID` or `PGID` repairs profile ownership at the next startup, which
can take time for a large profile. Do not run two containers against the same
appdata folder.

## Updates and release channels

Browser updates download first while the browser stays open. The browser then
closes, the package installs from the local cache, and the session restarts.
Expect a brief interruption. A download failure leaves the installed browser
untouched and does not lock the service; only an interrupted package installation
holds admission closed for administrator attention.

In OIDC mode updates run only while no desktop is open, and no login can start
one mid-installation.

To check for a browser update manually:

```bash
docker exec brave-origin /usr/local/bin/update-brave.sh
```

Container updates are separate: use `docker compose pull` and
`docker compose up -d --no-build`, or Unraid's container update controls.

This fork keeps two branches. `main` holds released code, and `pocket-id` is
where session work lands before it is merged. A tag such as `v1.2.0-beta.7`
builds, tests and publishes a testing image with its dependency and malware scan
reports attached to the GitHub release. The X11/KasmVNC edition is not part of
this fork's branches; it lives upstream and is kept here only as archive tags.

See [release notes](CHANGELOG.md) for changes and [the release guide](RELEASING.md)
for maintainer steps.

## HTTPS and access

The web session provides access to browser profiles and downloaded files. Keep it
behind a trusted network, VPN or authenticated proxy. A reverse proxy must
preserve the original `Host` header, including a nonstandard port. Requests from
unrelated website origins are rejected. Publish only the HTTPS port; never expose
the container's internal ports.

Brave runs unprivileged with its Chromium sandbox enabled. In OIDC mode each
identity runs under its own Linux UID, so file permissions — not application
logic — keep one person's profile out of another's reach. The supplied
configuration uses `seccomp:unconfined` so the browser can create user
namespaces; this disables Docker's syscall filter for this container, and the
host must permit unprivileged user namespaces. `no-new-privileges` prevents child
processes from gaining permissions through setuid programs. Do not add
`--no-sandbox`, privileged mode, or `SYS_ADMIN`.

To use your own TLS certificate, place the chain at `/config/ssl/cert.pem` and
the key at `/config/ssl/cert.key`, then restart the container. An nginx reload
does not copy newly supplied files into place.

## GPU support

Without a GPU mapping the container renders in software. With an Intel or AMD GPU
available:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d --no-build
```

For an NVIDIA GPU, install the NVIDIA Container Toolkit on the host and use:

```bash
docker compose -f compose.yaml -f compose.nvidia.yaml up -d --no-build
```

The browser needs the driver's graphics libraries, not only its compute
libraries. `--gpus all` on its own requests `compute,utility`, which is enough
for the streaming server to encode on the card while the browser has no EGL
driver and either falls back to software rendering or fails to display. The image
therefore sets `NVIDIA_DRIVER_CAPABILITIES=all`; keep that value if you pass the
variable yourself, and prefer the Compose override above to a bare `--gpus all`.
Startup logs the GPU it selected under `[gpu]`, the browser logs its choice under
`[browser-session]`, and both warn when an NVIDIA device is present without its
graphics driver.

Browser hardware rendering, WebGL and video decoding were verified on Intel
graphics. Stream encoding can fall back to the CPU when a driver does not support
the requested format. `ENABLE_GPU=false` disables browser GPU rendering.

The NVIDIA configuration follows LinuxServer's Selkies base image and **has not
been verified on NVIDIA hardware** by this project; the development host exposes
a GPU only through WSL2, which cannot present the Linux driver nodes this path
needs. Private desktops composite in software regardless of the GPU, so plan
`MAX_CONCURRENT_SESSIONS` against available CPU rather than the graphics card.

## License

This container project uses the [MIT License](LICENSE). Brave Origin, Selkies and
the other included components retain their own licenses. Brave Origin and the
Brave logo are trademarks of Brave Software, Inc. This project is unofficial and
is not affiliated with or endorsed by Brave Software, Inc.
