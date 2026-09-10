#!/usr/bin/env bash
# Called only after the image has passed scripts/smoke-test.sh.
set -euo pipefail
image="${1:?image required}"
ref="${2:?git ref required}"
sha="${3:?commit required}"
case "$ref" in
    refs/heads/beta) tags=(beta "sha-$sha") ;;
    refs/tags/v*)
        version=${ref#refs/tags/v}
        if [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            git merge-base --is-ancestor "$sha" origin/main
            tags=("$version" latest "sha-$sha")
        elif [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+$ ]]; then
            tags=("$version" beta "sha-$sha")
        else
            echo 'Unsupported release version.' >&2; exit 1
        fi ;;
    *) echo 'This ref does not publish images.'; exit 0 ;;
esac
: "${REGISTRY_IMAGE:?Additional registry image path is required}"
: "${REGISTRY_USERNAME:?Additional registry username is required}"
: "${REGISTRY_TOKEN:?Additional registry token is required}"
: "${GHCR_TOKEN:?GitHub registry token is required}"
if [[ ! "$REGISTRY_IMAGE" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*(:[0-9]+)?/[a-z0-9][a-z0-9._/-]*$ ]]; then
    echo 'Registry image must contain a host and repository path, without a scheme or tag.' >&2
    exit 1
fi
# Keep login files outside the workspace and remove them after publishing.
registry_config=$(mktemp -d)
trap 'rm -rf "$registry_config"' EXIT
export DOCKER_CONFIG="$registry_config"
printf '%s' "$REGISTRY_TOKEN" | docker login "${REGISTRY_IMAGE%%/*}" -u "$REGISTRY_USERNAME" --password-stdin
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USERNAME:-shoyrock}" --password-stdin
for registry in "$REGISTRY_IMAGE" ghcr.io/shoyrock/brave-origin; do
    for tag in "${tags[@]}"; do
        docker tag "$image" "$registry:$tag"
        docker push "$registry:$tag"
    done
done
