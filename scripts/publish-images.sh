#!/usr/bin/env bash
# Called only after the image has passed scripts/smoke-test.sh.
set -euo pipefail
image="${1:?image required}"
ref="${2:?git ref required}"
sha="${3:?commit required}"
case "$ref" in
    refs/heads/x11-beta) tags=(x11-beta "x11-sha-$sha") ;;
    refs/tags/x11-v*)
        version=${ref#refs/tags/x11-v}
        if [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            git merge-base --is-ancestor "$sha" origin/x11
            tags=("$version-x11" x11 "x11-sha-$sha")
        elif [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+$ ]]; then
            tags=("$version-x11" x11-beta "x11-sha-$sha")
        else
            echo 'Unsupported X11 release version.' >&2; exit 1
        fi ;;
    *) echo 'This ref does not publish X11 images.'; exit 0 ;;
esac
: "${REGISTRY_TOKEN:?Forgejo registry token is required}"
: "${GHCR_TOKEN:?GitHub registry token is required}"
# Keep login files outside the workspace and remove them after publishing.
registry_config=$(mktemp -d)
trap 'rm -rf "$registry_config"' EXIT
export DOCKER_CONFIG="$registry_config"
printf '%s' "$REGISTRY_TOKEN" | docker login forgejo.foss.homes -u "${REGISTRY_USERNAME:-shoy}" --password-stdin
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USERNAME:-shoyrock}" --password-stdin
for registry in forgejo.foss.homes/shoy/brave-origin ghcr.io/shoyrock/brave-origin; do
    for tag in "${tags[@]}"; do
        docker tag "$image" "$registry:$tag"
        docker push "$registry:$tag"
    done
done
