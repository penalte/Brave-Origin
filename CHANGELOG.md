# Release notes

## 1.1.0-beta.2 (Pocket ID fork)

- OIDC callbacks automatically detect the HTTPS hostname and port from the request; no application URL setting is needed. Login flows and sessions remain bound to their original address.

## 1.1.0-beta.1 (Pocket ID fork)

- Optional configurable OIDC login with a private persistent browser profile per identity.
- One active user at a time; Brave closes after logout, expiry or disconnection while the container remains running.
- Published Linux AMD64 testing image through GitHub Actions, with application tests and attached dependency and malware scan reports.

Testing only. See the prerelease for current dependency findings. Live Pocket ID credentials and GPU hardware remain untested.

## 1.0.1-beta.2

- Brave now stays maximized when you click minimize or restore, or drag the window.
- Added a setting to bring back the previous window controls.

Testing release only. Known dependency vulnerabilities remain; use a separate profile on a trusted network. Stable publication remains on hold.

## 1.0.1-beta.1

- Fixed black bars by automatically resizing the desktop to fit the browser window.
- Improved protection for saved browser data, login details, and certificates.
- Blocked remote-session requests from unrelated websites.
- Added security checks before publishing images.

Testing release only. Known dependency vulnerabilities remain; use a separate profile on a trusted network. Stable publication remains on hold.

## 1.0.0

First stable release.

- Run Brave Origin remotely with saved profiles, password protection, and audio.
- Fixed native copy and paste, and added a **Send to session** clipboard button.
- Fixed audio after stopping and restarting the stream.
- Improved shutdown so browser profiles are saved before the container stops.
- Corrected the Unraid template and kept development builds separate from stable updates.

## 1.0.0-beta.1

- Added password protection by default and improved password storage.
- Fixed copy and paste for more text formats, including Unicode and multiple lines.
- Fixed browser restarts after backups and updates.
- Added protection against opening a profile with an older browser.
- Corrected the Unraid setup template and simplified the setup instructions.
- Separated beta builds from stable releases.

This earlier prerelease has been superseded by 1.0.0.
