#!/usr/bin/env bash
# ==============================================================================
# Native Wayland Desktop & Brave Origin Session Starter (Selkies + Labwc)
# ==============================================================================
set -eo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-braveuser}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
export PIXELFLUX_WAYLAND=true
export SELKIES_ENABLE_DUAL_MODE=false
export SELKIES_PORT=8082
export CUSTOM_WS_PORT=8082
export SELKIES_ADDR=127.0.0.1
export XCURSOR_THEME=Adwaita
export XCURSOR_SIZE=24
export XKB_DEFAULT_LAYOUT=us
export XKB_DEFAULT_RULES=evdev
export GTK_THEME=Adwaita:dark

# GTK dialogs follow the same dark appearance as the browser chrome.
for version in gtk-3.0 gtk-4.0; do
    mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/$version"
    printf '%s\n' '[Settings]' 'gtk-theme-name=Adwaita-dark' 'gtk-application-prefer-dark-theme=1' \
        > "${XDG_CONFIG_HOME:-$HOME/.config}/$version/settings.ini"
done

# Explicitly UNSET DISPLAY to guarantee zero X11 / Xwayland execution
unset DISPLAY

# The shell owns the profile lock until Brave and all session daemons stop.
exec 9>/config/state/profile.lock
flock -n 9 || { echo 'Profile is already in use.' >&2; exit 1; }
set_status() {
    printf '%s\n' "$1" > "/config/state/.status.$$"
    mv -f "/config/state/.status.$$" /config/state/status
}
SELKIES_PID='' LABWC_PID='' BRAVE_PID='' DBUS_PID=''
cleanup() {
    trap - EXIT TERM INT HUP
    # Keep Wayland and D-Bus alive while Chromium flushes its profile.
    if [ -n "$BRAVE_PID" ]; then
        kill -TERM "$BRAVE_PID" 2>/dev/null || true
        for ((i=0; i<15; i++)); do
            kill -0 "$BRAVE_PID" 2>/dev/null || break
            sleep 1
        done
    fi
    # Reap the whole browser tree before releasing the profile lock.
    pkill -KILL -u "$(id -u)" -x brave 2>/dev/null || true
    for pid in "${LABWC_PID}" "${SELKIES_PID}" "${DBUS_PID}"; do
        [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null || true
    done
    pulseaudio --kill 2>/dev/null || true
    sleep 1
    for pid in "${BRAVE_PID}" "${LABWC_PID}" "${SELKIES_PID}" "${DBUS_PID}"; do
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
if [ "${OIDC_ENABLED:-false}" != true ] && [ -n "$LAST_VER" ] && { ! dpkg --validate-version "$LAST_VER" || dpkg --compare-versions "$USED_VER" lt "$LAST_VER"; }; then
    echo "Refusing browser $USED_VER: profile requires $LAST_VER." >&2
    set_status DOWNGRADE_BLOCKED
    sleep "${DOWNGRADE_RETRY_INTERVAL:-300}"
    exit 1
fi
[ "${OIDC_ENABLED:-false}" = true ] || [ ! -f /config/state/quiesce.flag ] || exit 0
mkdir -p "${XDG_RUNTIME_DIR}" /config/downloads /config/profile
chmod 700 "${XDG_RUNTIME_DIR}"
rm -f "${XDG_RUNTIME_DIR}"/wayland-* "${XDG_RUNTIME_DIR}"/pulse/pid "${XDG_RUNTIME_DIR}"/dbus/session_bus_socket

# Helper function to check UNIX domain socket connectivity
check_socket_ready() {
    local socket_path="$1"
    python3 -c 'import socket,sys; s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(0.5); s.connect(sys.argv[1]); s.close()' "$socket_path" 2>/dev/null
}

# 1. Start D-Bus Session Daemon
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    mkdir -p "${XDG_RUNTIME_DIR}/dbus"
    DBUS_PID=$(dbus-daemon --session --fork --print-pid --address="unix:path=${XDG_RUNTIME_DIR}/dbus/session_bus_socket" 9>&-)
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/dbus/session_bus_socket"
fi

# 2. Start PulseAudio Virtual Sink (if ENABLE_AUDIO=true)
if [ "${ENABLE_AUDIO:-true}" = "true" ]; then
    echo "[start-session] Initializing PulseAudio virtual sink..."
    mkdir -p "${XDG_RUNTIME_DIR}/pulse"
    pulseaudio --exit-idle-time=-1 --daemonize=true 9>&- || true
    pactl load-module module-native-protocol-unix auth-anonymous=1 socket="${XDG_RUNTIME_DIR}/pulse/native" 2>/dev/null || true
    if [ "${OIDC_ENABLED:-false}" = true ]; then
        pactl load-module module-native-protocol-unix auth-anonymous=1 socket="${XDG_RUNTIME_DIR}/pulse/oidc"
    fi
    pactl load-module module-null-sink sink_name=output sink_properties=device.description="Default_Audio_Output" 2>/dev/null || true
    pactl set-default-sink output 2>/dev/null || true
    export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
    export AUDIO_ENABLED=true
else
    export AUDIO_ENABLED=false
fi

# 3. Start Selkies Streaming Server (Smithay Wayland Display on 127.0.0.1:8082)
echo "[start-session] Starting Selkies Wayland display & streaming server on 127.0.0.1:8082..."
export SELKIES_AUDIO_ENABLED="${AUDIO_ENABLED}"
export SELKIES_UI_TITLE="Brave Origin"
# Positive manual dimensions force Selkies to lock the desktop size. Leave them
# unset by default so the client can resize both axes to its current viewport.
unset SELKIES_MANUAL_WIDTH SELKIES_MANUAL_HEIGHT
if [ "${DISPLAY_AUTO_RESIZE:-true}" = false ]; then
    export SELKIES_MANUAL_WIDTH="${DISPLAY_WIDTH:-1920}"
    export SELKIES_MANUAL_HEIGHT="${DISPLAY_HEIGHT:-1080}"
fi
export SELKIES_AUDIO_DEVICE_NAME="output.monitor"
export FILE_MANAGER_PATH=/config/downloads
export SELKIES_ENABLE_BASIC_AUTH=false
export SELKIES_ENABLE_DUAL_MODE=false
export SELKIES_PORT=8082
export CUSTOM_WS_PORT=8082

if [ "${OIDC_ENABLED:-false}" = true ]; then
    export SELKIES_WEB_ROOT=/usr/share/selkies/web
    export SELKIES_COMMAND_ENABLED='false|locked'
    export SELKIES_FILE_TRANSFERS=none
    export SELKIES_ENABLE_SHARING='false|locked'
    export SELKIES_ENABLE_COLLAB='false|locked'
    export SELKIES_ENABLE_SHARED='false|locked'
    export SELKIES_ENABLE_PLAYER2='false|locked'
    export SELKIES_ENABLE_PLAYER3='false|locked'
    export SELKIES_ENABLE_PLAYER4='false|locked'
    export SELKIES_SECOND_SCREEN='false|locked'
    export SELKIES_UI_SIDEBAR_SHOW_FILES='false|locked'
    export SELKIES_UI_SIDEBAR_SHOW_APPS='false|locked'
    export SELKIES_UI_SIDEBAR_SHOW_SHARING='false|locked'
fi

python3 -m selkies \
    --addr=127.0.0.1 \
    --port=8082 \
    --mode=websockets \
    --wayland=true \
    --app-wayland-display=wayland-0 \
    --enable-basic-auth=false \
    9>&- > /config/state/selkies.log 2>&1 &
SELKIES_PID=$!
echo "${SELKIES_PID}" > /config/state/selkies.pid

# 4. Wait for Wayland display socket created by Pixelflux / Smithay AND test connectable
echo "[start-session] Waiting for root Wayland display socket (Smithay) in ${XDG_RUNTIME_DIR}..."
TIMEOUT=30
ELAPSED=0
SMITHAY_SOCKET=""
while [ -z "${SMITHAY_SOCKET}" ]; do
    for s in "${XDG_RUNTIME_DIR}"/wayland-*; do
        if [ -S "${s}" ]; then
            if check_socket_ready "${s}"; then
                SMITHAY_SOCKET="${s}"
                export SMITHAY_DISPLAY="${s##*/}"
                export WAYLAND_DISPLAY="${SMITHAY_DISPLAY}"
                break
            fi
        fi
    done
    [ -n "${SMITHAY_SOCKET}" ] && break
    sleep 0.2
    ELAPSED=$((ELAPSED + 1))
    if [ "${ELAPSED}" -ge "$((TIMEOUT * 5))" ]; then
        echo "[start-session] ERROR: Timeout waiting for root Wayland socket in ${XDG_RUNTIME_DIR}!" >&2
        cat /config/state/selkies.log >&2 || true
        exit 1
    fi
done
echo "[start-session] Root Wayland display ready: ${SMITHAY_SOCKET} (WAYLAND_DISPLAY=${WAYLAND_DISPLAY})"

# 5. Start Labwc Wayland Window Manager on root display
echo "[start-session] Starting Labwc window manager on root display ${WAYLAND_DISPLAY}..."
mkdir -p /config/.config/labwc
# Keep browser windows maximized and disable desktop window-management shortcuts.
# Brave draws its own frame so a second titlebar does not consume screen space.
cat << 'EOF' > /config/.config/labwc/rc.xml
<?xml version="1.0"?>
<labwc_config>
  <theme>
    <name>Adwaita</name>
    <cornerRadius>4</cornerRadius>
  </theme>
  <!-- Kiosk appliance policy: the browser UI (tabs, address bar, bookmarks)
       stays visible and every window opens maximized. -->
  <windowRules>
    <windowRule identifier="*" serverDecoration="no">
      <action name="Maximize" />
    </windowRule>
  </windowRules>
  <!-- No default keyboard bindings: window switching, closing, and
       un-maximization are unavailable. Swallow the escape hatches
       (quit, close window/tab, fullscreen toggle). -->
  <keyboard>
    <keybind key="C-q"><action name="None" /></keybind>
    <keybind key="C-S-q"><action name="None" /></keybind>
    <keybind key="C-w"><action name="None" /></keybind>
    <keybind key="C-S-w"><action name="None" /></keybind>
    <keybind key="A-F4"><action name="None" /></keybind>
    <keybind key="F11"><action name="None" /></keybind>
    <keybind key="A-Tab"><action name="None" /></keybind>
    <keybind key="S-A-Tab"><action name="None" /></keybind>
    <keybind key="A-F10"><action name="None" /></keybind>
  </keyboard>
  <!-- No default mouse bindings: no root desktop menu or window gestures. -->
  <mouse></mouse>
</labwc_config>
EOF

LABWC_COMMAND=labwc-browser
if [ "${BROWSER_LOCK_MAXIMIZED:-true}" = false ]; then
    LABWC_COMMAND=labwc
fi
"$LABWC_COMMAND" -c /config/.config/labwc/rc.xml 9>&- > /config/state/labwc.log 2>&1 &
LABWC_PID=$!
echo "${LABWC_PID}" > /config/state/labwc.pid

# Wait for Labwc client-facing Wayland socket (nested compositor socket) AND verify connectable
echo "[start-session] Waiting for Labwc application Wayland socket in ${XDG_RUNTIME_DIR}..."
ELAPSED=0
LABWC_SOCKET=""
while [ -z "${LABWC_SOCKET}" ]; do
    for s in "${XDG_RUNTIME_DIR}"/wayland-*; do
        if [ -S "${s}" ] && [ "${s}" != "${SMITHAY_SOCKET}" ]; then
            if check_socket_ready "${s}"; then
                LABWC_SOCKET="${s}"
                export LABWC_DISPLAY="${s##*/}"
                break
            fi
        fi
    done
    [ -n "${LABWC_SOCKET}" ] && break
    sleep 0.2
    ELAPSED=$((ELAPSED + 1))
    if [ "${ELAPSED}" -ge "$((TIMEOUT * 5))" ]; then
        echo "[start-session] ERROR: Labwc application Wayland socket failed to start!" >&2
        cat /config/state/labwc.log >&2 || true
        exit 1
    fi
done

export WAYLAND_DISPLAY="${LABWC_DISPLAY}"
echo "[start-session] Labwc application Wayland socket ready: ${LABWC_SOCKET} (WAYLAND_DISPLAY=${WAYLAND_DISPLAY})"

# OIDC mode keeps the desktop alive. Only the gateway may launch a browser.
# Publish the window manager's socket so the gateway starts browsers on the same
# compositor the legacy path uses, and with it the kiosk window rules.
if [ "${OIDC_ENABLED:-false}" = true ]; then
    printf '%s\n' "${LABWC_DISPLAY}" > /tmp/brave-desktop-ready
    wait -n "$LABWC_PID" "$SELKIES_PID"
    exit 1
fi

# 6. GPU Detection & Flags Configuration (ENABLE_GPU=false forces software rendering)
GPU_FLAGS=""
if [ "${ENABLE_GPU:-true}" = "false" ]; then
    echo "[start-session] ENABLE_GPU=false - forcing software rasterization"
elif [ -e /dev/nvidiactl ] && /sbin/ldconfig -p | grep libEGL_nvidia >/dev/null; then
    # Chromium selects its own GL backend once the vendor's loader files exist,
    # exactly as it does in the working LinuxServer image.
    echo "[start-session] NVIDIA GPU detected - enabling hardware acceleration"
    GPU_FLAGS="--enable-gpu-rasterization --ignore-gpu-blocklist --disable-features=Vulkan"
elif [ -e /dev/nvidiactl ]; then
    echo "[start-session] NVIDIA device present without its EGL driver - set NVIDIA_DRIVER_CAPABILITIES=all"
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

# 7. Launch Brave Origin Natively on Wayland (Direct Process Execution)
echo "[start-session] Starting Brave Origin with native Wayland Ozone backend..."

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
    --ozone-platform=wayland \
    --enable-features=UseOzonePlatform \
    --user-data-dir=/config/profile \
    --disk-cache-dir=/tmp/brave-cache \
    --no-first-run \
    --no-default-browser-check \
    --password-store=basic \
    --start-maximized \
    --force-dark-mode \
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
wait -n "$BRAVE_PID" "$LABWC_PID" "$SELKIES_PID"
