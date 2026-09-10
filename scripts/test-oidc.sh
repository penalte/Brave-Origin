#!/usr/bin/env bash
# Disposable test only; never attaches existing profile storage.
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:?Usage: test-oidc.sh IMAGE}"
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
    -e AUTO_UPDATE=false -e ENABLE_GPU=false "$image" >/dev/null
ready=false
for ((i=0; i<90; i++)); do
    if docker exec "$name" bash /usr/local/bin/healthcheck.sh >/dev/null 2>&1; then ready=true; break; fi
    sleep 1
done
[ "$ready" = true ]
docker exec "$name" sh -c '! pgrep -x brave'
identity=$(docker inspect --format '{{.State.StartedAt}}' "$name")
for test in oidc-tokens oidc-session; do
    docker cp "tests/$test.py" "$name:/tmp/$test.py"
    docker exec "$name" python3 "/tmp/$test.py"
done
[ "$(docker inspect --format '{{.State.StartedAt}}' "$name")" = "$identity" ]
docker exec "$name" bash /usr/local/bin/healthcheck.sh
docker exec "$name" sh -c '! pgrep -x brave'
echo 'OIDC tests passed; container stayed running throughout user transitions.'
