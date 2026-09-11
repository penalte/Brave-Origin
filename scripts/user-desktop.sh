#!/usr/bin/env bash
# One private desktop stack; invoked as the identity UID by the broker.
set -euo pipefail
umask 077
: "${HOME:?}" "${XDG_RUNTIME_DIR:?}"
export XDG_CONFIG_HOME="$HOME/.config" XDG_CACHE_HOME="$HOME/.cache"
export XDG_DATA_HOME="$HOME/.local/share"
export GTK_THEME=Adwaita:dark
export WLR_SCENE_DISABLE_DIRECT_SCANOUT=1
export PULSE_SERVER="unix:$XDG_RUNTIME_DIR/pulse/native"
export PIXELFLUX_WAYLAND=true SELKIES_ENABLE_BASIC_AUTH=false SELKIES_ENABLE_DUAL_MODE=false
export SELKIES_UNIX_SOCKET="$XDG_RUNTIME_DIR/stream.sock"
export SELKIES_JS_SOCKET_PATH="$XDG_RUNTIME_DIR"
export SELKIES_GAMEPAD_ENABLED='false|locked'
export SELKIES_WEB_ROOT=/usr/share/selkies/web FILE_MANAGER_PATH="$HOME/Downloads"
export SELKIES_FILE_TRANSFERS=upload,download SELKIES_COMMAND_ENABLED='false|locked'
export SELKIES_ENABLE_SHARING='false|locked' SELKIES_ENABLE_COLLAB='false|locked'
export SELKIES_ENABLE_SHARED='false|locked' SELKIES_SECOND_SCREEN='false|locked'
export SELKIES_ENABLE_PLAYER2='false|locked' SELKIES_ENABLE_PLAYER3='false|locked' SELKIES_ENABLE_PLAYER4='false|locked'
export SELKIES_UI_SIDEBAR_SHOW_FILES=true SELKIES_UI_SIDEBAR_SHOW_APPS='false|locked'
export SELKIES_UI_SIDEBAR_SHOW_SHARING='false|locked'
export SELKIES_AUDIO_DEVICE_NAME=output.monitor
unset DISPLAY
mkdir -p "$HOME/Downloads" "$XDG_CONFIG_HOME/labwc" "$XDG_RUNTIME_DIR/pulse"
mkdir -p "$XDG_CONFIG_HOME/gtk-3.0" "$XDG_CONFIG_HOME/gtk-4.0"
for version in gtk-3.0 gtk-4.0; do
    printf '%s\n' '[Settings]' 'gtk-theme-name=Adwaita-dark' 'gtk-application-prefer-dark-theme=1' \
        > "$XDG_CONFIG_HOME/$version/settings.ini"
done
cleanup() {
    trap - EXIT TERM INT
    jobs -pr | xargs -r kill -TERM 2>/dev/null || true
    pulseaudio --kill 2>/dev/null || true
    wait || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT
export SELKIES_AUDIO_ENABLED=false
if [ "${ENABLE_AUDIO:-true}" = true ]; then
    pulseaudio --exit-idle-time=-1 --daemonize=true
    pactl load-module module-null-sink sink_name=output >/dev/null
    pactl set-default-sink output
    export SELKIES_AUDIO_ENABLED=true
fi
if [ "${DISPLAY_AUTO_RESIZE:-true}" = false ]; then
    export SELKIES_MANUAL_WIDTH="${DISPLAY_WIDTH:-1920}" SELKIES_MANUAL_HEIGHT="${DISPLAY_HEIGHT:-1080}"
fi
# Run a headless desktop and capture it directly; nesting compositors can lose
# shared-memory frames when no GPU is available.
unset WAYLAND_DISPLAY
cat > "$XDG_CONFIG_HOME/labwc/rc.xml" <<'XML'
<?xml version="1.0"?>
<labwc_config><windowRules><windowRule identifier="brave-origin" serverDecoration="no"><action name="Maximize" /></windowRule></windowRules><keyboard><keybind key="A-F4"><action name="None" /></keybind><keybind key="A-Tab"><action name="None" /></keybind></keyboard><mouse/></labwc_config>
XML
compositor=labwc-browser
if [ "${BROWSER_LOCK_MAXIMIZED:-true}" = false ]; then compositor=labwc; fi
WLR_BACKENDS=headless WLR_RENDERER=pixman "$compositor" -c "$XDG_CONFIG_HOME/labwc/rc.xml" > "$XDG_RUNTIME_DIR/labwc.log" 2>&1 &
compositor_pid=$!
for ((i=0; i<300; i++)); do
    [ ! -S "$XDG_RUNTIME_DIR/wayland-0" ] || break
    kill -0 "$compositor_pid"
    sleep 0.1
done
test -S "$XDG_RUNTIME_DIR/wayland-0"
export WAYLAND_DISPLAY=wayland-0
/usr/bin/python3 /usr/local/bin/file-picker.py > "$XDG_RUNTIME_DIR/picker.log" 2>&1 &
picker_pid=$!
for ((i=0; i<100; i++)); do
    [ ! -f "$XDG_RUNTIME_DIR/picker-ready" ] || break
    kill -0 "$picker_pid"
    sleep 0.1
done
test -f "$XDG_RUNTIME_DIR/picker-ready"
export GTK_USE_PORTAL=1
python3 -m selkies --addr=127.0.0.1 --mode=websockets --wayland=true \
    --wayland-host-display=wayland-0 --app-wayland-display=wayland-0 \
    --enable-basic-auth=false > "$XDG_RUNTIME_DIR/selkies.log" 2>&1 &
stream_pid=$!
/usr/local/bin/browser-session.sh > "$XDG_RUNTIME_DIR/browser.log" 2>&1 &
browser_pid=$!
touch "$XDG_RUNTIME_DIR/ready"
wait -n "$stream_pid" "$compositor_pid" "$browser_pid" "$picker_pid"
