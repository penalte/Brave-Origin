#!/usr/bin/env bash
set -eo pipefail

# ==============================================================================
# Web Authentication Password Initialization & Reset Utility
# ==============================================================================

AUTH_USER="${AUTH_USER:-${KASM_USER:-brave}}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
PASSWD_FILE="/config/.passwd"

ARG="${1:-}"

if [ -z "${ARG}" ] || [ "${ARG}" = "--generate" ]; then
    PASSWORD="$(openssl rand -base64 12)"
    GENERATED=true
else
    PASSWORD="${ARG}"
    GENERATED=false
fi

# Replace credentials atomically, preserving the old password if creation fails.
tmp=$(mktemp /config/.passwd.XXXXXX)
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$PASSWORD" | htpasswd -iBc "$tmp" "$AUTH_USER" >/dev/null 2>&1
chmod 640 "$tmp"
chown root:www-data "$tmp"
mv -f "$tmp" "$PASSWD_FILE"
rm -f /config/.kasmpasswd

# Reload Nginx if running
if pgrep -x nginx >/dev/null 2>&1; then
    nginx -s reload 2>/dev/null || true
fi

echo "================================================================================"
echo "[auth] Credentials configured for user '${AUTH_USER}'"
if [ "${GENERATED}" = "true" ]; then
    echo " Generated Password: ${PASSWORD}"
    echo ""
    echo " NOTICE: This password will NOT be displayed in container logs."
    echo " Store it securely. To change it later, run:"
    echo "   docker exec brave-origin /usr/local/bin/reset-password.sh <new_password>"
else
    echo " Password successfully updated to user-provided value."
fi
echo "================================================================================"

