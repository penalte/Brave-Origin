#!/usr/bin/env bash
# X11 session: hold the profile lock until all browser and desktop processes stop.
set -eo pipefail
export HOME=/config DISPLAY=:1 XDG_SESSION_TYPE=x11
export XDG_RUNTIME_DIR=/tmp/runtime-braveuser
export XAUTHORITY="${XDG_RUNTIME_DIR}/.Xauthority"
export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
unset WAYLAND_DISPLAY
# The shell owns the profile lock until Brave and all session daemons stop.
exec 9>/config/state/profile.lock
flock -n 9 || { echo 'Profile is already in use.' >&2; exit 1; }
set_status() {
    printf '%s\n' "$1" > "/config/state/.status.$$"
    mv -f "/config/state/.status.$$" /config/state/status
}
VNC_PID='' OPENBOX_PID='' BRAVE_PID='' DBUS_PID='' AUDIO_PID=''
cleanup() {
    trap - EXIT TERM INT HUP
    # Keep X11 and D-Bus alive while Chromium flushes its profile.
    if [ -n "$BRAVE_PID" ]; then
        kill -TERM "$BRAVE_PID" 2>/dev/null || true
        for ((i=0; i<15; i++)); do
            kill -0 "$BRAVE_PID" 2>/dev/null || break
            sleep 1
        done
    fi
    # Reap the whole browser tree before releasing the profile lock.
    pkill -KILL -u "$(id -u)" -x brave 2>/dev/null || true
    for pid in "${OPENBOX_PID}" "${VNC_PID}" "${DBUS_PID}" "${AUDIO_PID}"; do
        [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null || true
    done
    pulseaudio --kill 2>/dev/null || true
    sleep 1
    for pid in "${BRAVE_PID}" "${OPENBOX_PID}" "${VNC_PID}" "${DBUS_PID}" "${AUDIO_PID}"; do
        [ -z "$pid" ] || kill -KILL "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    rm -f /tmp/brave.pid
}
trap cleanup EXIT
trap 'exit 0' TERM INT HUP

# Refuse to open a profile with an older browser, even with updates disabled.
[ "$(dpkg-query -W -f='${db:Status-Status}' brave-origin)" = installed ] || exit 1
USED_VER="$(dpkg-query -W -f='${Version}' brave-origin)"
LAST_VER="$(cat /config/state/last-brave-version 2>/dev/null || cat /config/.last-brave-version 2>/dev/null || true)"
if [ -n "$LAST_VER" ] && { ! dpkg --validate-version "$LAST_VER" || dpkg --compare-versions "$USED_VER" lt "$LAST_VER"; }; then
    echo "Refusing browser $USED_VER: profile requires $LAST_VER." >&2
    set_status DOWNGRADE_BLOCKED
    sleep "${DOWNGRADE_RETRY_INTERVAL:-300}"
    exit 1
fi
[ ! -f /config/state/quiesce.flag ] || exit 0
mkdir -p "${XDG_RUNTIME_DIR}" /config/downloads /config/profile
chmod 700 "${XDG_RUNTIME_DIR}"
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 "${XDG_RUNTIME_DIR}"/pulse/pid "${XDG_RUNTIME_DIR}"/dbus/session_bus_socket


mkdir -p "${XDG_RUNTIME_DIR}/dbus"
DBUS_PID=$(dbus-daemon --session --fork --print-pid --address="unix:path=${XDG_RUNTIME_DIR}/dbus/session_bus_socket" 9>&-)
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/dbus/session_bus_socket"

if [ "${ENABLE_AUDIO:-true}" = true ]; then
    mkdir -p "${XDG_RUNTIME_DIR}/pulse"
    pulseaudio --exit-idle-time=-1 --daemonize=true 9>&-
    pactl load-module module-null-sink sink_name=output sink_properties=device.description=Browser_Audio >/dev/null
    pactl set-default-sink output
    python3 /usr/local/bin/audio-server.py 9>&- > /config/state/audio-relay.log 2>&1 &
    AUDIO_PID=$!
fi

# The proxy carries all traffic; a fixed loopback publicIP avoids external STUN lookups.
# The launcher requires a user record even when nginx handles authentication.
# Keep this internal record ephemeral and separate from saved web credentials.
internal_password=$(openssl rand -hex 16)
printf '%s\n%s\nn\n' "$internal_password" "$internal_password" | HOME="$XDG_RUNTIME_DIR" kasmvncpasswd -u session -wo >/dev/null
unset internal_password
HOME="$XDG_RUNTIME_DIR" vncserver :1 -config /config/kasmvnc/kasmvnc.yaml -geometry "${DISPLAY_WIDTH:-1920}x${DISPLAY_HEIGHT:-1080}" \
    -depth 24 -interface 127.0.0.1 -publicIP 127.0.0.1 -websocketPort 8444 -disableBasicAuth -SecurityTypes None \
    -noxstartup 9>&- > /config/state/kasmvnc.log 2>&1
VNC_PID=$(cat "$XDG_RUNTIME_DIR/.vnc/$(hostname):1.pid")
printf '%s\n' "$VNC_PID" > /config/state/kasmvnc.pid
for ((i=0; i<100; i++)); do
    if xdpyinfo >/dev/null 2>&1; then break; fi
    kill -0 "$VNC_PID" 2>/dev/null || { cat /config/state/kasmvnc.log >&2; exit 1; }
    sleep 0.2
done
xdpyinfo >/dev/null 2>&1 || { echo 'X11 display did not start.' >&2; exit 1; }
openbox --config-file /etc/xdg/openbox/rc.xml 9>&- > /config/state/openbox.log 2>&1 &
OPENBOX_PID=$!
# 6. GPU Detection & Flags Configuration (ENABLE_GPU=false forces software rendering)
GPU_FLAGS=""
if [ "${ENABLE_GPU:-true}" = "false" ]; then
    echo "[start-session] ENABLE_GPU=false - forcing software rasterization"
elif [ -e "${DRI_NODE:-/dev/dri/renderD128}" ]; then
    echo "[start-session] GPU ${DRI_NODE:-/dev/dri/renderD128} detected - enabling hardware acceleration"
    export LIBVA_DRIVER_NAME_OVERRIDE=""
    GPU_FLAGS="--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist --disable-features=Vulkan"
else
    echo "[start-session] No /dev/dri GPU device detected - running with software rasterization"
fi
if [ -z "${GPU_FLAGS}" ]; then
    GPU_FLAGS="--disable-gpu --disable-gpu-compositing"
fi

# 7. Launch Brave Origin on X11 (Direct Process Execution)
echo "[start-session] Starting Brave Origin with X11 Ozone backend..."

# Serialize browser launch with offline installation and backup requests.
exec 8>/run/lock/brave-origin-launch.lock
flock 8
[ ! -f /config/state/quiesce.flag ] || exit 0
USED_VER="$(dpkg-query -W -f='${Version}' brave-origin)"
if [ -n "$LAST_VER" ] && dpkg --compare-versions "$USED_VER" lt "$LAST_VER"; then
    set_status DOWNGRADE_BLOCKED
    exit 1
fi

# Safe singleton recovery: clear stale Chromium artifacts only after the flock
rm -f /config/profile/Singleton* 2>/dev/null || true

# Skip Brave Origin's one-time welcome modal on fresh profiles: it takes over
# the whole window and hides the browser chrome until dismissed. Seeding the
# dismissal pref before first launch makes a brand-new /config start directly
# in the usable browser.
if [ ! -f "/config/profile/Local State" ]; then
    printf '%s\n' '{"brave":{"has_seen_brave_welcome_page":true}}' > "/config/profile/Local State"
fi

# Restore Brave's native client-side window frame. A previous image version
# forced browser.custom_chrome_frame=false (system titlebar mode), which left
# a permanent strip of reserved space at the top of the display; profiles
# touched by it are cleaned up here on every start.
if [ -f "/config/profile/Default/Preferences" ]; then
    python3 - << 'EOF' || true
import json
p = "/config/profile/Default/Preferences"
try:
    with open(p) as f:
        d = json.load(f)
    if d.get("browser", {}).get("custom_chrome_frame") is not True:
        d.setdefault("browser", {})["custom_chrome_frame"] = True
        with open(p, "w") as f:
            json.dump(d, f)
except Exception:
    pass
EOF
fi

# Split extra flags without eval, command substitution, or pathname expansion.
read -r -a EXTRA_FLAGS <<< "${BRAVE_FLAGS:-}"
for flag in "${EXTRA_FLAGS[@]}"; do
    case "$flag" in
        --*sandbox*|--test-type*|--single-process*|--user-data-dir*)
            echo "Unsafe or profile-changing BRAVE_FLAGS option rejected: $flag" >&2; exit 1 ;;
    esac
done
read -r -a GPU_ARGS <<< "$GPU_FLAGS"
/opt/brave.com/brave-origin/brave \
    --ozone-platform=x11 \
    --enable-features=UseOzonePlatform \
    --user-data-dir=/config/profile \
    --disk-cache-dir=/tmp/brave-cache \
    --no-first-run \
    --no-default-browser-check \
    --password-store=basic \
    --start-maximized \
    "${GPU_ARGS[@]}" \
    "${EXTRA_FLAGS[@]}" \
    "$@" 8>&- 9>&- >> /config/state/brave.log 2>&1 &
BRAVE_PID=$!
printf '%s\n' "$BRAVE_PID" > /tmp/brave.pid
# Conservatively record any version allowed to open the profile.
printf '%s\n' "$USED_VER" > "/config/state/.last-brave-version.tmp.$$"
chmod 600 "/config/state/.last-brave-version.tmp.$$"
mv -f "/config/state/.last-brave-version.tmp.$$" /config/state/last-brave-version
flock -u 8
exec 8>&-
set_status RUNNING
# A failed compositor or streaming server also requires a complete restart.
while kill -0 "$BRAVE_PID" 2>/dev/null && kill -0 "$OPENBOX_PID" 2>/dev/null && kill -0 "$VNC_PID" 2>/dev/null; do
    [ -z "$AUDIO_PID" ] || kill -0 "$AUDIO_PID" 2>/dev/null || break
    sleep 1
done
