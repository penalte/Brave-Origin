#!/usr/bin/env bash
set -euo pipefail
if [ "${OIDC_ENABLED:-false}" = true ]; then
    curl -fsS --max-time 5 http://127.0.0.1:8084/health >/dev/null
    exit
fi
code=$(curl -k -s -o /dev/null -w '%{http_code}' --max-time 5 https://127.0.0.1:8443/)
[[ "$code" = 200 || "$code" = 401 ]] || exit 1
if [ -f /config/state/quiesce.flag ]; then
    [ "$(cat /config/state/status)" = QUIESCED ]
    exit
fi
[ ! -f /run/brave-origin/update-in-progress ] || exit 0
pid=$(cat /tmp/brave.pid)
[[ "$pid" =~ ^[1-9][0-9]*$ ]]
[ "$(cat "/proc/$pid/comm")" = brave ]
[ "$(awk '/^Uid:/ {print $2}' "/proc/$pid/status")" = "$(id -u braveuser)" ]
pgrep -x 'labwc|labwc-browser' >/dev/null
ss -ltn | awk '$4 == "127.0.0.1:8082" {found=1} END {exit !found}'
