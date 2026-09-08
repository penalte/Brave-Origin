# Releasing Brave Origin

Develop on `beta`. Use a separate appdata folder and host port for development containers. Do not test with a stable user's profile.

Forgejo builds images and publishes to both registries. Configure the runner label `docker-build` and repository secrets `REGISTRY_TOKEN`, `REGISTRY_USERNAME`, `GHCR_TOKEN`, and `GHCR_USERNAME`. Both registry credentials are required for publishing. Pull requests do not run on the publishing runner because it has host Docker access.

Before tagging a release:

```bash
bash scripts/check.sh
docker build --build-arg VERSION=1.0.0 --build-arg BUILD_COMMIT="$(git rev-parse HEAD)" -t brave-origin:release-test .
bash scripts/smoke-test.sh brave-origin:release-test
```

For a stable release, verify a clean install, profile-preserving upgrades, software rendering, representative GPU hardware, audio restart, reconnects, and clipboard copy/paste through the web client. Check the Unraid template for unique settings, correct storage and ports, and safe defaults; test its container settings with UID 99 and GID 100. Validate HTTPS separately and use a secure browser context for clipboard tests. Document hardware and client combinations that have not been exercised. A missing platform-specific test is a compatibility limitation; a failing core browsing, storage, authentication, clipboard, audio, or recovery test blocks release. Add brief user-facing changes to `CHANGELOG.md`.

A beta version uses a tag such as `v1.0.0-beta.1` and a release marked **pre-release** on GitHub and Forgejo. Push the same commit and tag to both remotes. Forgejo publishes the numbered version and `beta`; it leaves `latest` unchanged.

For stable releases, merge verified changes into `main` and tag the release commit, starting with `v1.0.0`. Keep the documented default image on `latest`. Stable tags must point to a commit on `main`. Publish matching release notes on GitHub and Forgejo after the image tests and registry pushes succeed. Confirm both registries can be pulled without signing in.

Keep releases short: what users can do now, what was fixed, and any remaining limitations. Detailed implementation and test output belong in commits and development records.

## Dependency updates

The final image is based on `debian:trixie-slim`. Brave comes from the official stable `brave-origin` package. Selkies is pinned to a source commit; the LinuxServer library donor is pinned by digest. JavaScript dependency versions are recorded in `dependencies/` and installed with `npm ci`. Supplemental Python packages are pinned with hashes in `dependencies/runtime.txt`; the build runs `pip check`.

When updating Selkies, review the patches, regenerate both lockfiles from the matching upstream package files, and rerun the clipboard and container tests. The host needs only Bash, Python 3, Git, and Docker/Compose for the repository checks; ShellCheck runs too when installed.
