# Release notes

## 1.2.0-beta.12 (Pocket ID fork)

- Set private desktops to a close-only GTK button layout and Brave's own frame, retaining the tab-search arrow and X. Existing profiles receive the corrected frame default once; later manual preference changes are preserved.

## 1.2.0-beta.11 (Pocket ID fork)

- Moved the signed-in name and End session control into the Selkies sidebar, removing the outer top bar so the desktop fills the viewport. Logout retains its authenticated gateway flow with visible failure and retry handling.

## 1.2.0-beta.10 (Pocket ID fork)

- Moved file deletion into a dedicated Delete column with accessible trash-can buttons. Confirmation and file-only deletion are unchanged.

## 1.2.0-beta.9 (Pocket ID fork)

- Added confirmed file deletion to Selkies My files, restricted to the signed-in identity's Downloads tree. Directories, links and outside paths cannot be deleted.
- Includes the borderless picker and one-time Brave system-titlebar preference changes described in beta.7.

## 1.2.0-beta.8 (Pocket ID fork)

- Blocked the ways a session could leave its managed profile: Incognito windows, private windows with Tor, guest profiles and adding a second profile. Each of them would otherwise escape the per-identity download folder.
- Turned off casting and the cast button. Device discovery reached the host's network from inside a remote session.
- Added `BROWSER_POLICY` for extra Chromium or Brave managed policies as a JSON object. The private download directory is applied last and cannot be overridden; malformed input stops startup instead of being ignored.
- Rewrote the README around private multi-user sessions, including the locked browser features and the OIDC settings.

## 1.2.0-beta.7 (Pocket ID fork)

- Removed the private picker title bar and window-manager menu, including its Alt+Space shortcut. The picker retains its own Open/Save, Cancel and Escape controls.
- Set Brave's system-titlebar preference once for new and existing identity profiles, preserving other preferences and later manual changes.

## 1.2.0-beta.6 (Pocket ID fork)

- Fixed duplicate pointers and missing CSS cursor-shape updates in private desktops. Each user's application compositor now forwards its cursor to a private Selkies capture compositor, keeping the pointer out of video unless native cursor rendering is enabled.
- Verified distinct text/hand cursor messages, cursor-free video in CSS mode, native cursor toggling, resizing, and the private picker and two-user lifecycle tests.

## 1.2.0-beta.5 (Pocket ID fork)

- Website uploads and Save dialogs now use a compact dark My files picker in each private desktop. It browses only that user's Downloads tree, rejects symlinks and outside paths, and supports multiple files and filename filters. Folder uploads are rejected.
- The picker shares the desktop's private D-Bus session and is supervised with Brave. A picker failure closes that user's desktop; the container and other users stay running.
- Verified real website uploads, saves, cancellation, and picker-failure cleanup alongside the existing two-user isolation tests. Picker restrictions do not constitute a complete filesystem sandbox for Brave.

## 1.2.0-beta.4 (Pocket ID fork)

- Replaced internal profile paths in the file manager with a compact “My files” heading and relative folder names. Existing storage and download links are unchanged.

## 1.2.0-beta.3 (Pocket ID fork)

- Slimmed the session bar to 32px and made the desktop fill the remaining viewport. Removed the inline iframe baseline gap and outer-page scrollbar.

## 1.2.0-beta.2 (Pocket ID fork)

- Includes the private-desktop changes below and enforces upload size limits across successive chunks. The beta.1 publication was cancelled before image publication to include this correction.

## 1.2.0-beta.1 (Pocket ID fork)

- Each identity now gets a private Wayland desktop, streaming server and audio session. Logout closes that user's stack while other users and the container stay running.
- Restored Selkies upload/download access using each identity's private Downloads folder. Added configurable session capacity and upload limits.
- Captures private headless Labwc sessions directly with a pinned matching Pixelflux library; GTK and Brave use dark mode.
- Waits for desktop descendants to exit before releasing a session. GPU hardware and live Pocket ID deployment still require host verification.

## 1.1.0-beta.4 (Pocket ID fork)

- Fixed NVIDIA detection under the browser account's restricted PATH. NVIDIA sessions no longer accidentally select generic render-node zero-copy flags; missing NVIDIA graphics drivers use software rendering.
- Failed package installations keep admission closed until recovery verifies that Brave is fully installed.
- Added regression coverage for GPU flag selection and interrupted package recovery. Actual NVIDIA rendering still requires hardware verification.

## 1.1.0-beta.3 (Pocket ID fork)

- NVIDIA graphics now work. The image requests the driver's full capabilities, ships the NVIDIA Wayland EGL library, and installs the loader files the container toolkit leaves out. Previously a GPU container gave Selkies hardware encoding while the browser had no driver to render with. Added a `compose.nvidia.yaml` override.
- The gateway now takes the desktop's reported window-manager socket instead of assuming a fixed name, so browsers keep the kiosk window rules even if compositor startup order changes.
- Signing in no longer fails when the container runs with a restrictive `UMASK`.
- A failed update or cleanup no longer locks the gateway permanently: only a genuinely interrupted package installation holds it closed, and the gateway retries reconciliation until the container is clean again.
- The sign-in page can no longer be blocked by unauthenticated requests filling the pending-login table.
- The streaming proxy rejects relative path segments, which could otherwise reach streaming-server endpoints it does not publish.
- The OIDC client secret is kept out of every process that drops privileges.

Testing only. See the prerelease for current dependency findings. Live Pocket ID credentials remain untested, and NVIDIA hardware was not available on the development host.

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
