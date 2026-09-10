# Releasing Brave Origin

Develop on `beta`. Use a separate appdata folder and host port for development containers. Do not test with a stable user's profile.

The publishing runner builds images and publishes to GHCR and a separately configured registry. Configure the runner label `docker-build` and repository secrets `REGISTRY_IMAGE`, `REGISTRY_TOKEN`, `REGISTRY_USERNAME`, `GHCR_TOKEN`, and `GHCR_USERNAME`. Set `REGISTRY_IMAGE` to the full image path without a tag or URL scheme, such as `registry.example.test/team/brave-origin`. Keep non-public registry addresses and credentials in CI secrets. Both registry credentials are required for publishing. Pull requests do not run on the publishing runner because it has host Docker access.

Release builds refresh the base image and package installation instead of reusing cached packages.

The image scan uses a pinned Grype container with no Docker socket or credentials. High and critical findings block publishing until investigated and resolved. Scan the final image for malware with current definitions as well; dependency scanning does not detect all malicious code. See [security guidance](SECURITY.md).

An explicitly authorized testing beta may be published manually with known dependency findings. This is a recorded risk decision, not a passing security check: keep the scanner's failure and attach its report to the prerelease, disclose the unresolved issues, and publish only numbered beta, `beta`, and commit tags. Scanner errors, detected malware, and failing application tests still block it. The automatic workflow and the stable release gate remain unchanged. The `1.0.1-beta.1` and `1.0.1-beta.2` testing releases use this exception for the documented September 8 dependency findings.

Before tagging a release:

```bash
bash scripts/check.sh
python3 scripts/audit-web-dependencies.py
docker build --pull --no-cache --build-arg VERSION=1.0.0 --build-arg BUILD_COMMIT="$(git rev-parse HEAD)" -t brave-origin:release-test .
bash scripts/smoke-test.sh brave-origin:release-test
python3 scripts/security-scan.py brave-origin:release-test
```

For a stable release, verify a clean install, profile-preserving upgrades, software rendering, representative GPU hardware, audio restart, reconnects, and clipboard copy/paste through the web client. Check the Unraid template for unique settings, correct storage and ports, and safe defaults; test its container settings with UID 99 and GID 100. Validate HTTPS separately and use a secure browser context for clipboard tests. Document hardware and client combinations that have not been exercised. A missing platform-specific test is a compatibility limitation; a failing core browsing, storage, authentication, clipboard, audio, or recovery test blocks release. Add brief user-facing changes to `CHANGELOG.md`.

A beta version uses a tag such as `v1.0.0-beta.1` and a release marked **pre-release** on each release host. Push the same commit and tag to both remotes. The workflow publishes the numbered version and `beta`; it leaves `latest` unchanged.

For stable releases, merge verified changes into `main` and tag the release commit, starting with `v1.0.0`. Keep the documented default image on `latest`. Stable tags must point to a commit on `main`. Publish matching release notes on each release host after the image tests and registry pushes succeed. Keep non-public registry addresses out of public release notes and attachments. Confirm the public GHCR image can be pulled without signing in and that the other registry works with its configured access controls.

Keep releases short: what users can do now, what was fixed, and any remaining limitations. Detailed implementation and test output belong in commits and development records.

## Dependency updates

The final image is based on `debian:trixie-slim`. Brave comes from the official stable `brave-origin` package. Selkies is pinned to a source commit; the LinuxServer library donor is pinned by digest. JavaScript dependency versions are recorded in `dependencies/` and installed with `npm ci`. Supplemental Python packages are pinned with hashes in `dependencies/runtime.txt`; the build runs `pip check`.

When updating Selkies, review the patches, regenerate both lockfiles from the matching upstream package files, and rerun the clipboard and container tests. The host needs only Bash, Python 3, Git, and Docker/Compose for the repository checks; ShellCheck runs too when installed.
