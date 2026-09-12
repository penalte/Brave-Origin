#!/usr/bin/env bash
# Sourced as the private UID, so access checks match the actual desktop.
if [ "${ENABLE_GPU:-true}" = false ]; then
    export AUTO_GPU=false
    unset DRI_NODE DRINODE
    echo '[desktop-gpu] GPU disabled; software rendering selected.'
else
    export AUTO_GPU="${AUTO_GPU:-true}"
    for key in DRI_NODE DRINODE; do
        node="${!key:-}"
        if [ -n "$node" ] && { [ ! -c "$node" ] || [ ! -r "$node" ] || [ ! -w "$node" ]; }; then
            echo "[desktop-gpu] $key=$node is not an accessible GPU character device for UID $(id -u)." >&2
            exit 1
        fi
    done
    echo "[desktop-gpu] UID=$(id -u) groups=$(id -G) AUTO_GPU=$AUTO_GPU renderer=${DRINODE:-auto} encoder=${DRI_NODE:-auto}"
    for node in /dev/dri/renderD* /dev/nvidiactl; do
        [ -e "$node" ] || continue
        if [ -r "$node" ] && [ -w "$node" ]; then access=rw; else access=denied; fi
        echo "[desktop-gpu] $node access=$access"
    done
    # Preserve an explicit driver choice. Probe only Intel, not NVIDIA/AMD.
    probe="${DRI_NODE:-${DRINODE:-}}"
    if [ -z "$probe" ] && [ "$AUTO_GPU" = true ]; then
        for node in /dev/dri/renderD*; do [ ! -c "$node" ] || { probe="$node"; break; }; done
    fi
    if [ -n "$probe" ] && [ -z "${LIBVA_DRIVER_NAME:-}" ] && command -v vainfo >/dev/null; then
        vendor=$(cat "/sys/class/drm/${probe##*/}/device/vendor" 2>/dev/null || true)
        if [ "$vendor" = 0x8086 ] && LIBVA_DRIVER_NAME=i965 timeout 5 vainfo --display drm --device "$probe" >/dev/null 2>&1; then
            export LIBVA_DRIVER_NAME=i965
            echo '[desktop-gpu] Intel i965 VA-API probe succeeded.'
        fi
    fi
    echo "[desktop-gpu] VA-API driver=${LIBVA_DRIVER_NAME:-auto}; selected encoder details follow in selkies.log."
fi
