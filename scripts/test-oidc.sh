#!/usr/bin/env bash
# Disposable test only; never attaches existing profile storage.
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:?Usage: test-oidc.sh IMAGE}"
# Separate disposable container: this test stubs drivers and never gets a GPU.
docker run --rm -i --entrypoint python3 "$image" - < tests/browser-gpu.py
docker run --rm -i --entrypoint python3 "$image" - < tests/file-picker.py
docker run --rm -i --entrypoint python3 "$image" - < tests/session-operations.py
docker run --rm -i --entrypoint python3 "$image" - < tests/admin-panel.py
docker run --rm -i --cap-add NET_ADMIN --entrypoint python3 "$image" - < tests/browser-network.py
name="brave-oidc-test-$$"
cleanup() {
    code=$?
    if (( code )); then docker logs --tail 80 "$name" 2>/dev/null || true; fi
    docker rm -fv "$name" >/dev/null 2>&1 || true
    exit "$code"
}
trap cleanup EXIT
docker run -d --name "$name" --shm-size=1g --security-opt seccomp=unconfined \
    --security-opt no-new-privileges=true -e OIDC_ENABLED=true \
    -e OIDC_ISSUER_URL=https://id.example.test \
    -e OIDC_CLIENT_ID=test -e OIDC_CLIENT_SECRET=test-only \
    -e PUID=99 -e PGID=100 -e UMASK=077 \
    -e AUTO_UPDATE=false -e ENABLE_GPU=false "$image" >/dev/null
ready=false
for ((i=0; i<90; i++)); do
    if docker exec "$name" bash /usr/local/bin/healthcheck.sh >/dev/null 2>&1; then ready=true; break; fi
    sleep 1
done
[ "$ready" = true ]
docker exec "$name" sh -c '! pgrep -x brave'
identity=$(docker inspect --format '{{.State.StartedAt}}' "$name")
docker cp tests/picker_browser.py "$name:/tmp/picker_browser.py"
docker cp tests/cursor_browser.py "$name:/tmp/cursor_browser.py"
for test in oidc-tokens oidc-recovery desktop-sizing multi-session; do
    docker cp "tests/$test.py" "$name:/tmp/$test.py"
    docker exec -e TEST_PRIVATE_PICKER=true "$name" python3 "/tmp/$test.py"
done
docker exec -e TEST_DPI=144 "$name" python3 /tmp/desktop-sizing.py
[ "$(docker inspect --format '{{.State.StartedAt}}' "$name")" = "$identity" ]
docker exec "$name" bash /usr/local/bin/healthcheck.sh
docker exec "$name" sh -c '! pgrep -x brave'
docker exec "$name" nginx -s stop
sleep 1
if docker exec "$name" bash /usr/local/bin/healthcheck.sh >/dev/null 2>&1; then
    echo 'Health check incorrectly accepted a stopped nginx' >&2
    exit 1
fi
echo 'OIDC tests passed; container stayed running throughout user transitions.'
