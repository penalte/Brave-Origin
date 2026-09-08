#!/usr/bin/env bash
# Download while running, then stop the browser and install from the cache.
set -euo pipefail
STATE_DIR=/config/state
mkdir -p /run/lock /run/brave-origin "$STATE_DIR"
set_status() {
    printf '%s\n' "$1" | runuser -u braveuser -- tee /config/state/status >/dev/null
}
exec 200>/run/lock/brave-origin-update.lock
flock -n 200 || { echo '[updater] An update is already running.'; exit 0; }

# Only the owner of the update lock may remove its transaction marker.
trap 'rm -f /run/brave-origin/update-in-progress' EXIT
[ ! -f "$STATE_DIR/quiesce.flag" ] || { echo '[updater] Backup hold is active.'; exit 0; }
minimum="${MIN_UPDATE_FREE_SPACE_MB:-1024}"
[[ "$minimum" =~ ^[1-9][0-9]*$ ]] && (( ${#minimum} <= 9 )) || exit 1
available=$(df -Pk / | awk 'NR==2 {print $4}')
(( available >= minimum * 1024 )) || { echo '[updater] Not enough free space; keeping the installed browser.'; exit 0; }

# Treat repository failures as failures even when apt can use stale metadata.
if ! apt-get update -o APT::Update::Error-Mode=any -qq; then
    echo '[updater] Repository unavailable; keeping the installed browser.'
    exit 0
fi
installed=$(dpkg-query -W -f='${Version}' brave-origin)
target="${BRAVE_ORIGIN_VERSION:-latest}"
if [ "$target" = latest ]; then
    target=$(apt-cache policy brave-origin | awk '/Candidate:/ {print $2}')
fi
[ -n "$target" ] && [ "$target" != '(none)' ] || exit 0
dpkg --validate-version "$target" || exit 1
last=$(cat "$STATE_DIR/last-brave-version" 2>/dev/null || cat /config/.last-brave-version 2>/dev/null || true)
if [ -n "$last" ]; then
    dpkg --validate-version "$last" || exit 1
    if dpkg --compare-versions "$target" lt "$last"; then
        echo '[updater] The requested version is older than this profile; update refused.'
        exit 2
    fi
fi
dpkg --compare-versions "$target" gt "$installed" || { echo "[updater] Keeping version $installed."; exit 0; }
export DEBIAN_FRONTEND=noninteractive
echo "[updater] Downloading Brave Origin $target while the browser stays open."
if ! apt-get install -y --download-only --no-install-recommends "brave-origin=$target"; then
    echo '[updater] Download failed; the running browser was left open.'
    exit 1
fi

exec 8>/run/lock/brave-origin-launch.lock
flock 8
[ ! -f "$STATE_DIR/quiesce.flag" ] || exit 0
touch /run/brave-origin/update-in-progress
set_status UPDATING
if [ -f /tmp/brave.pid ]; then
    pid=$(cat /tmp/brave.pid)
    # Match the executable as well as the PID; never signal a reused PID.
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]] && [ "$(cat "/proc/$pid/comm" 2>/dev/null)" = brave ] && [ "$(awk '/^Uid:/ {print $2}' "/proc/$pid/status" 2>/dev/null)" = "$(id -u braveuser)" ]; then
        kill -TERM "$pid" 2>/dev/null || true
        for ((i=0; i<20; i++)); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$pid" 2>/dev/null || true
    fi
fi
# Verify the complete browser process tree has stopped before replacing files.
for ((i=0; i<10; i++)); do
    pgrep -x brave >/dev/null || break
    sleep 1
done
if pgrep -x brave >/dev/null; then
    echo '[updater] Browser processes are still running; installation refused.' >&2
    exit 1
fi
echo '[updater] Installing downloaded packages. The browser will reopen afterward.'
if ! apt-get install -y --no-download --no-install-recommends "brave-origin=$target"; then
    echo '[updater] Installation failed; attempting repair using cached packages only.' >&2
    if ! { dpkg --configure -a && apt-get -f install -y --no-download; }; then
        set_status ERROR
        exit 1
    fi
fi
[ "$(dpkg-query -W -f='${db:Status-Status}' brave-origin)" = installed ] || { set_status ERROR; exit 1; }
apt-get clean
set_status STARTING
echo "[updater] Installed $(dpkg-query -W -f='${Version}' brave-origin)."
