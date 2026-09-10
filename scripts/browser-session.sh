#!/usr/bin/env bash
# Executed as the selected profile's unprivileged account, never as root.
set -euo pipefail
umask 077
export XDG_CONFIG_HOME="$HOME/.config" XDG_CACHE_HOME="$HOME/.cache"
export XDG_DATA_HOME="$HOME/.local/share"
mkdir -p "$HOME/profile" "$HOME/.cache" "$HOME/.config" "$HOME/.local/share"
read -r -a extra <<< "${BRAVE_FLAGS:-}"
for flag in "${extra[@]}"; do
    case "$flag" in
        --*sandbox*|--test-type*|--single-process*|--user-data-dir*|--disk-cache-dir*|--remote-debugging*)
            echo 'Unsafe browser flag rejected.' >&2; exit 1 ;;
    esac
done
gpu=(--disable-gpu --disable-gpu-compositing)
if [ "${ENABLE_GPU:-true}" = true ] && [ -e "${DRI_NODE:-/dev/dri/renderD128}" ]; then
    gpu=(--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist --disable-features=Vulkan)
fi
exec dbus-run-session -- /opt/brave.com/brave-origin/brave \
    --ozone-platform=wayland --user-data-dir="$HOME/profile" \
    --disk-cache-dir="$HOME/.cache/brave" --password-store=basic \
    --no-first-run --no-default-browser-check --start-maximized \
    "${gpu[@]}" "${extra[@]}"
