# Brave Origin in Docker

Run Brave Origin in a web browser over HTTPS. Your bookmarks, settings, extensions, and downloads stay in a persistent folder. The container uses Debian 13 Trixie Slim and the official stable `brave-origin` package.

**Status: beta.** The container is still being tested before its first stable release. The browser package itself uses Brave's stable channel. Use a separate profile when testing this container; never share a live profile between stable and beta instances.

## Get started

You need an x86-64 Linux host with Docker and Docker Compose v2. GPU access is optional.

```bash
git clone https://github.com/shoyrock/Brave-Origin.git
cd Brave-Origin
cp .env.example .env
```

Edit `.env` and set `AUTH_PASSWORD` to a password of your choice. Then start the container:

```bash
docker compose pull
docker compose up -d --no-build
```

Open `https://YOUR-SERVER-IP:8443` and sign in as `brave`. The container creates a self-signed certificate, so your browser will show a certificate warning. For a trusted connection, supply your own certificate as described below.

Images are available from both registries:

- `ghcr.io/shoyrock/brave-origin:beta`
- `forgejo.foss.homes/shoy/brave-origin:beta`

Set `IMAGE_NAME` in `.env` to choose a registry or a specific version. To build from source, run `docker compose build` followed by `docker compose up -d --no-build`.

## Unraid

The [Unraid template](templates/brave-origin.xml) uses bridge networking, HTTPS port 8443, and `/mnt/user/appdata/brave-origin` for persistent storage. It defaults to Unraid's user ID 99 and group ID 100.

To install the template from the Unraid terminal:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user
curl -fL https://raw.githubusercontent.com/shoyrock/Brave-Origin/main/templates/brave-origin.xml \
  -o /boot/config/plugins/dockerMan/templates-user/my-brave-origin.xml
```

In **Docker → Add Container**, select **Brave-Origin**. Set a password, review the appdata path and port, then apply. Use the container's **WebUI** menu to open it.

For Intel or AMD graphics, add a **Device** mapping from `/dev/dri` to `/dev/dri` in the advanced template view. Leave this mapping out on systems without that device. GPU behavior and installation on a physical Unraid host still need verification before the stable release.

## Copy and paste

Use Ctrl+C and Ctrl+V inside the remote session. On macOS, use the shortcuts supported by your client browser. Text can move in both directions, including Unicode and multiple lines. The server also supports image clipboard transfer when the client enables it.

Clipboard access depends on your client browser's permissions and a secure context. Allow clipboard access when prompted and keep the session page focused. If automatic clipboard access is blocked, use the clipboard controls in the session sidebar. End-to-end clipboard behavior across client browsers is still being verified.

## Settings

Set these values in `.env`, the Unraid template, or your container's environment.

| Setting | Default | Purpose |
| --- | --- | --- |
| `CONFIG_PATH` | `./appdata` | Compose host folder mounted at `/config`. |
| `IMAGE_NAME` | `ghcr.io/shoyrock/brave-origin:beta` | Compose image and release channel. |
| `WEB_PORT` | `8443` | Compose host port. The container always listens on 8443. |
| `PUID` / `PGID` | `1000` / `1000` | Nonzero user and group IDs for browser files. |
| `UMASK` | `022` | File creation permissions. |
| `TZ` | `Etc/UTC` | Timezone. |
| `AUTH_ENABLED` | `true` | Require a login. Disable only when access is already restricted by your network or proxy. |
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
| `DISPLAY_WIDTH` / `DISPLAY_HEIGHT` | `1920` / `1080` | Fixed remote display size in pixels. |
| `BRAVE_FLAGS` | Empty | Extra space-separated browser arguments. Shell quoting is not interpreted; flags that disable the sandbox or change the profile are rejected. |
| `CONTAINER_HOSTNAME` | `brave-origin` | Compose container hostname. |

The older `KASM_AUTH_ENABLED`, `KASM_USER`, `KASM_PASSWORD`, and `KASM_PASSWORD_FILE` names remain accepted. The corresponding `AUTH_*` setting takes precedence.

Saved credentials take precedence over password environment variables. On first setup, a password file takes precedence over `AUTH_PASSWORD`; an unreadable or empty file stops startup. Passwords are stored as bcrypt hashes.

To change a saved password:

```bash
docker exec brave-origin /usr/local/bin/reset-password.sh --generate
```

The generated password is printed to that command's output. Save it securely. You can also pass a chosen password as the argument, but doing so can leave it in your shell history. Resetting a password does not enable authentication if you explicitly disabled it.

## Storage and backups

| Container path | Contents |
| --- | --- |
| `/config/profile` | Browser profile, bookmarks, history, and extensions. |
| `/config/downloads` | Downloads and files transferred through the session. |
| `/config/state` | Locks, version records, status, and logs. |
| `/config/ssl` | HTTPS certificate and private key. |
| `/config/.passwd` | Saved login credentials. |

Back up the entire appdata folder while the container is stopped. For backups without stopping the container, pause the browser first:

```bash
docker exec brave-origin /usr/local/bin/profile-control.sh quiesce
# Back up your appdata folder after the command succeeds.
docker exec brave-origin /usr/local/bin/profile-control.sh resume
```

The backup command waits for the profile lock and flushes writes before reporting success. A failed command means the profile is not ready for backup. A backup hold persists across container restarts until you run `resume`.

Run `docker exec brave-origin /usr/local/bin/profile-control.sh status` to inspect the session. A healthy container can be paused for backup. A browser that is too old for the saved profile reports `DOWNGRADE_BLOCKED` and does not open the profile.

Changing `PUID` or `PGID` repairs profile ownership at the next startup. This can take time for a large profile. Do not run two containers against the same appdata folder.

## Updates and release channels

Browser updates download first while the browser stays open. After the download succeeds, the browser closes, the package installs from the local cache, and the session restarts. Expect a brief interruption. A download failure leaves the browser running. Failed installation recovery also uses cached packages only.

To check for a browser update manually:

```bash
docker exec brave-origin /usr/local/bin/update-brave.sh
```

Container updates are separate: use `docker compose pull` and `docker compose up -d --no-build`, or Unraid's container update controls.

- `beta` is the development branch and image tag. It receives tested development builds.
- `main` holds release preparation and stable code. A push to `main` builds and tests but does not publish images.
- A tag such as `v1.0.0-beta.1` publishes a beta version. It does not change `latest`.
- A stable tag such as `v1.0.0`, created from `main`, publishes the version and updates `latest` in both registries.

No stable version has been approved yet. Older `wayland` and `latest` images predate this release process; use the documented beta tag for current testing. See [release notes](CHANGELOG.md) for changes and [the release guide](RELEASING.md) for maintainer steps.

## HTTPS and access

The web session provides access to the browser profile and downloaded files. Keep it behind a trusted network, VPN, or authenticated proxy. Login protection is enabled by default.

Brave runs as `braveuser` with its Chromium sandbox enabled. The supplied configuration uses `seccomp:unconfined` so the browser can create user namespaces; this disables Docker's syscall filter for this container. The host must permit unprivileged user namespaces. Do not add `--no-sandbox`, privileged mode, or `SYS_ADMIN`.

To use your own TLS certificate, place its certificate chain at `/config/ssl/cert.pem` and its private key at `/config/ssl/cert.key`, then restart the container. A simple nginx reload does not copy newly supplied files into place.

## GPU support

Without a GPU mapping, the container uses software rendering. With an Intel or AMD GPU available, start Compose with:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d --no-build
```

Support depends on the host driver and device. Passing a GPU does not guarantee that every page or video uses hardware acceleration. `ENABLE_GPU=false` disables browser GPU rendering.

## License

This container project uses the [MIT License](LICENSE). Brave Origin, Selkies, and the other included components retain their own licenses. Brave Origin and the Brave logo are trademarks of Brave Software, Inc. This project is unofficial and is not affiliated with or endorsed by Brave Software, Inc.
