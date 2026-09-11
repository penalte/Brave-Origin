#!/usr/bin/env bash
# Executed as the selected profile's unprivileged account, never as root.
set -euo pipefail
umask 077
export XDG_CONFIG_HOME="$HOME/.config" XDG_CACHE_HOME="$HOME/.cache"
export XDG_DATA_HOME="$HOME/.local/share"
export GTK_THEME=Adwaita:dark
mkdir -p "$HOME/profile" "$HOME/.cache" "$HOME/.config" "$HOME/.local/share"
# Match the standard launcher's first-run behavior for new identity profiles.
if [ ! -f "$HOME/profile/Local State" ]; then
    printf '%s\n' '{"brave":{"has_seen_brave_welcome_page":true,"origin":{"free_tier_accepted":true}}}' > "$HOME/profile/Local State"
fi
python3 - <<'PY'
import json, os
from pathlib import Path
profile = Path(os.environ['HOME']) / 'profile'
marker = profile / '.system-titlebar-default-v1'
if not marker.exists():
    directory = profile / 'Default'
    directory.mkdir(exist_ok=True)
    path = directory / 'Preferences'
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault('browser', {})['custom_chrome_frame'] = False
    temporary = directory / '.Preferences.titlebar.tmp'
    temporary.write_text(json.dumps(data))
    os.replace(temporary, path)
    marker.touch()
PY
read -r -a extra <<< "${BRAVE_FLAGS:-}"
for flag in "${extra[@]}"; do
    case "$flag" in
        --*sandbox*|--test-type*|--single-process*|--user-data-dir*|--disk-cache-dir*|--remote-debugging*)
            echo 'Unsafe browser flag rejected.' >&2; exit 1 ;;
    esac
done
# Pick a rendering path. An NVIDIA container exposes no Mesa render node, so
# keying acceleration on /dev/dri alone left every NVIDIA host software-rendered
# or failing in the GPU process while Selkies encoded happily on the same card.
gpu=(--disable-gpu --disable-gpu-compositing)
if [ "${ENABLE_GPU:-true}" = true ]; then
    if [ -e /dev/nvidiactl ] && /sbin/ldconfig -p | grep libEGL_nvidia >/dev/null; then
        # Let Chromium select its own GL backend, as the working LinuxServer
        # image does. Once the vendor's loader files are present it finds EGL by
        # itself. Zero-copy stays off: it wants GBM buffer sharing this driver
        # does not offer reliably under a nested compositor.
        echo '[browser-session] NVIDIA GPU detected - hardware acceleration enabled.' >&2
        gpu=(--enable-gpu-rasterization --ignore-gpu-blocklist --disable-features=Vulkan)
    elif [ -e /dev/nvidiactl ]; then
        echo '[browser-session] NVIDIA device present but its EGL driver is missing.' >&2
        echo '[browser-session] Start the container with NVIDIA_DRIVER_CAPABILITIES=all.' >&2
    elif [ -e "${DRI_NODE:-/dev/dri/renderD128}" ]; then
        echo "[browser-session] Render node ${DRI_NODE:-/dev/dri/renderD128} detected." >&2
        gpu=(--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist --disable-features=Vulkan)
    else
        echo '[browser-session] No GPU device found - software rasterization.' >&2
    fi
fi
bus=()
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then bus=(dbus-run-session --); fi
exec "${bus[@]}" /opt/brave.com/brave-origin/brave \
    --ozone-platform=wayland --user-data-dir="$HOME/profile" \
    --disk-cache-dir="$HOME/.cache/brave" --password-store=basic \
    --no-first-run --no-default-browser-check --start-maximized \
    --force-dark-mode \
    "${gpu[@]}" "${extra[@]}"
