# Releasing Brave Origin

Develop on `x11-beta`. Use a separate appdata folder and host port for development containers. Do not test with a stable user's profile.

Forgejo builds images and publishes to both registries. Configure the runner label `docker-build` and repository secrets `REGISTRY_TOKEN`, `REGISTRY_USERNAME`, `GHCR_TOKEN`, and `GHCR_USERNAME`. Both registry credentials are required for publishing. Pull requests do not run on the publishing runner because it has host Docker access.

Before tagging a release:

```bash
bash scripts/check.sh
docker build --build-arg VERSION=1.0.0-x11 --build-arg BUILD_COMMIT="$(git rev-parse HEAD)" -t brave-origin:release-test .
bash scripts/smoke-test.sh brave-origin:release-test
```

For a stable release, verify a clean install, profile-preserving upgrades, software rendering, representative GPU hardware, audio restart, reconnects, and clipboard copy/paste through the web client. Check the Unraid template for unique settings, correct storage and ports, and safe defaults; test its container settings with UID 99 and GID 100. Validate HTTPS separately and use a secure browser context for clipboard tests. Document hardware and client combinations that have not been exercised. A missing platform-specific test is a compatibility limitation; a failing core browsing, storage, authentication, clipboard, audio, or recovery test blocks release. Add brief user-facing changes to `CHANGELOG.md`.

A beta version uses a tag such as `x11-v1.0.0-beta.1` and a release marked **pre-release** on GitHub and Forgejo. Push the same commit and tag to both remotes. Forgejo publishes the numbered version and `x11-beta`; it leaves `latest` unchanged.

For stable releases, merge verified changes into `x11` and tag the release commit, starting with `x11-v1.0.0`. Keep the documented default image on `x11`. Stable tags must point to a commit on `x11`. Publish matching release notes on GitHub and Forgejo after the image tests and registry pushes succeed. Confirm both registries can be pulled without signing in.

Keep releases short: what users can do now, what was fixed, and any remaining limitations. Detailed implementation and test output belong in commits and development records.

## Dependency updates

Use `debian:trixie-slim` and the official stable `brave-origin` package. KasmVNC's Debian Trixie amd64 package is pinned by version and SHA-256 in the Dockerfile. Verify the official release asset and update its checksum together. The audio relay uses Debian's packaged Python websockets library. The native-paste hook checks the upstream bundle checksum; review it when updating KasmVNC. Run `node tests/client-clipboard.mjs` as well as the container and interactive clipboard checks.

X11 publishing must never write `latest` or `beta`. Keep the corresponding Wayland workflow on `main` and `beta` separate. Test channel routing with `python3 tests/release.py`.
