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
