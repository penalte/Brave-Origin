# Release notes

## 1.2.0-beta.45 (Pocket ID fork)

- Run the three-second sign-in countdown after the connection and desktop are both ready, with every step checked, instead of before the desktop starts. Sign-in takes the same time, and the stream still connects only after the countdown.

## 1.2.0-beta.44 (Pocket ID fork)

- Includes the 1.2.0-beta.43 changes below, which failed their desktop tests and were not published.
- A new user at the desktop limit now reaches the preparation page and a retryable error instead of being refused at sign-in; the limit is enforced when preparation launches the desktop.
- Update the desktop sizing and multi-user login tests so new sign-ins complete through the preparation request.

## 1.2.0-beta.43 (Pocket ID fork)

- Includes the 1.2.0-beta.42 changes below; that publication was cancelled before image publication to add these corrections.
- Run the sign-in WARP readiness check with a minimal environment, matching every other command started as a desktop account.
- Add regression coverage for the session-choice origin fix: only the handoff page uses a same-origin referrer policy, and null or foreign form origins stay rejected.

## 1.2.0-beta.42 (Pocket ID fork)

- Fix session-choice buttons rejected with `Invalid origin`: use a same-origin referrer policy on the handoff page so native form submissions retain their Origin header. Origin and CSRF validation remain enforced.

- Show animated preparation progress on every new login, followed by a green approval check and a three-second countdown. Connect the streaming client only after the countdown; refreshing an existing session still skips it.
- Add private startup progress at `/session/preparation-status`. Verify WARP through the user's relay and Unix UID, enforce three seconds before starting Brave/Selkies, then report desktop readiness. Direct users verify worker startup without a WARP check. Failed preparation keeps a retryable sign-in ticket; concurrent requests cannot duplicate a launch.

## 1.2.0-beta.41 (Pocket ID fork)

- Begin the three-second countdown only after private proxy/desktop startup completes, including rapid re-login. Drain the orange ring from full to empty in sync with the countdown.

- Fix the session-choice form token, match the login page styling, and send a second tab to takeover/disconnect choices before opening a competing stream. Cancel leaves the original desktop running.

## 1.2.0-beta.40 (Pocket ID fork)

- Keep rapid-login cleanup and stream connection on one preparation surface, skip the countdown when refreshing the same session, and finish the TV close on animation completion.

## 1.2.0-beta.39 (Pocket ID fork)

- Restyle administration and sharing to match the Selkies files menu, with clear user/status cards, permission choices, invitation controls, mobile layouts, and accessible close controls.

- Show a matching preparation modal on desktop connection, with a three-second countdown and a TV-style close after the stream starts. Connect Selkies behind the modal so screen sizing is not delayed; respect reduced motion. Rapid re-login also waits safely for cleanup.

- Let a quick sign-in wait for the same user's previous desktop to finish starting or stopping, then continue automatically without reopening a profile during cleanup or blocking other users.

## 1.2.0-beta.38 (Pocket ID fork)

- Add independent guest gamepad permission to sharing invitations and participant controls. Assign Players 2–4 per desktop, show waiting/assigned status, and release held inputs on revoke or disconnect.
- Restore the owner's gamepad forwarding toggle and fit all six media/WARP controls on one sidebar row, retaining tooltips and wrapping for additional touch controls.
- Widen the sidebar to 320px and retry WARP health checks every two seconds while unavailable, avoiding the previous 15-second pause after a failed startup probe.
- Use Selkies' existing translation for the sharing button.

## 1.2.0-beta.37 (Pocket ID fork)

- Add the matching Selkies V4L2 webcam adapter to private Brave sessions and isolate each user's camera socket, without passing through a host camera device.
- Create the private virtual microphone before Brave starts; select it as the default input and initialize microphone mixer stages at unity gain.
- Exercise camera pixels, browser media capture, microphone signal levels and cross-user media isolation alongside the existing real gamepad tests.

## 1.2.0-beta.36 (Pocket ID fork)

- Fix intermittent release-test navigation by opening gamepad and routing probe pages through Brave's running profile instead of racing address-bar keyboard input. Preserve real browser, controller press/release, video and routing assertions.
- Keep H.264 decoder state across integration-test stages and request a Selkies keyframe before checking the gamepad page, matching the stream's on-demand keyframe behavior.

## 1.2.0-beta.35 (Pocket ID fork)

- Add a private proxy worker and UID-specific firewall grant for each desktop. Route changes preserve desktops and affect only that user's connections; WARP users never fall back to direct.
- Persist user routing preferences and admin-granted permission to disable WARP. List offline registered users and add persistent force-WARP enforcement.
- Make the Selkies WARP tile toggle the user's route with a blue breathing transition and descriptive tooltip.
- Remove external proxy settings. Private OIDC sessions require NET_ADMIN for per-user port isolation.

## 1.2.0-beta.34 (Pocket ID fork)

- Restore the private desktop owner's gamepad panel and controller input using the matching Selkies joystick and device-discovery adapters. Each desktop uses private Unix sockets; kernel uinput devices remain disabled.
- Assign the authenticated owner player slot 1. Guest gamepad input remains denied, independently of mouse/keyboard grants.

## 1.2.0-beta.33 (Pocket ID fork)

- Upgrade the locked compositor to Labwc 0.9.7 with pinned wlroots 0.19.3, retaining private Wayland sessions and adapting the maximization policy to the new API.
- Reduce the one-time window sizing recovery delay from 2.5 to 1.2 seconds.

## 1.2.0-beta.32 (Pocket ID fork)

- Label the login button "Sign in with PENALTE ID" to match the identity provider branding.

## 1.2.0-beta.31 (Pocket ID fork)

- Upgrade Selkies backend and dashboard together to v2.0.0rc0, with matching Pixelflux and pcmflux 2.1.0rc0 wheels pinned by checksum. Preserve private desktop integration and nested window sizing patches.
- Match the Pocket ID login page to the shared-session ending screen.
- Correct a late-starting browser sooner. The screen configuration that repairs a window which came up after its session was sized now runs 2.5 seconds in rather than 5, which is about as long as the wrong size stayed visible. Verified on a GPU host as a rare remaining fault; the earlier moment still lands after the browser has finished starting.

## 1.2.0-beta.30 (Pocket ID fork)

- Replace frozen guest frames after sharing ends with a styled thank-you screen.
- Match Share desktop and Admin panel to the Selkies section design and place them above Shortcuts.

## 1.2.0-beta.29 (Pocket ID fork)

- Add expiring guest links in Selkies, with optional mouse and keyboard control selected by the owner. Guests do not need Pocket ID. Owners can grant/revoke control and stop sharing.
- Add admin View session buttons that open online desktops in a new tab, bound to the active admin login.
- Enforce guest media/input permissions at the gateway and Selkies token layer; revoke access on logout, takeover, expiry, or stop sharing.

## 1.2.0-beta.28 (Pocket ID fork)

- Update capacity tests for authenticated session takeover: permit login at capacity, reject new identities, and verify existing-user confirmation, CSRF rejection, cancellation, and replay protection. Beta.27 failed the old admission test and was not published.

## 1.2.0-beta.27 (Pocket ID fork)

- Hide the sidebar scrollbar while preserving scrolling.
- After signing in to an already-open desktop, offer Disconnect, Take over, or Cancel. Disconnect closes the previous desktop without restarting; takeover preserves tabs and revokes the previous session credentials.
- Handoff is bound to a fresh verified identity, an expiring confirmation cookie, CSRF protection, and the original session generation. Runtime handoff validation remains pending.

## 1.2.0-beta.26 (Pocket ID fork)

- Remove the sidebar theme toggle.
- Prevent expanded sidebar sections from shrinking and clipping when the session buttons sit at the bottom; overflow scrolls normally.
- Replace the Selkies header logo and title with the signed-in user avatar and name. Show WARP as a colored tile beside the media controls, clickable by admins to open WARP controls. Keep session buttons at the bottom.

## 1.2.0-beta.24 (Pocket ID fork)

- Show the Pocket ID avatar and user name at the top of Selkies, with a live WARP status indicator. Missing avatars fall back to the user initial.
- Move Admin panel and End session to the bottom of the sidebar with matching orange buttons.

## 1.2.0-beta.23 (Pocket ID fork)

- Add optional shared WARP and generic browser proxy routing with browser-UID egress enforcement. WARP is disabled by default and requires explicit terms acceptance plus NET_ADMIN.
- Add a Pocket ID admin-group panel in Selkies with online users and a WARP toggle. Routing changes close existing desktops safely before new logins; Docker variables remain the startup defaults.

## 1.2.0-beta.22 (Pocket ID fork)

- Preserve the single five-second startup recovery, using the latest dimensions and desktop DPI. Serialize application-screen writes, skip recovery for disconnected clients, and finish the recovery task at shutdown.
- Add application-screen recovery readback and Labwc window geometry diagnostics without changing window policy.
- Run five recovery regressions against patched Selkies during image builds, covering resize, DPI, reconnect, disconnect and shutdown.

## 1.2.0-beta.21 (Pocket ID fork)

- The browser now fills its desktop when it starts slowly. The previous release configured the inner session's screen at the moment the resolution was decided, which for a session's first sizing is while the browser is still starting: the session accepted the mode and the window that came up afterwards kept the size it opened with. Switching away from the streamed tab and back corrected it by accident, because that re-ran the same step. That configuration is now applied once more five seconds into a session, and recorded in the session log. Later resizes are unchanged: they already reach a running browser.
- Requires host verification: the reported fault appears only on a GPU host, which no test environment here provides.
- The 1.2.0-beta.20 publication failed its own sizing test and published nothing. It re-applied the configuration three times, and laying the desktop out repeatedly while a session was still filling its first frames cost the test client its connection. One application is enough.

## 1.2.0-beta.19 (Pocket ID fork)

- The browser should now fill its desktop every time, not most times. Each private desktop runs its window manager inside the capture compositor, and that inner session was left to follow a resize on its own; when it did not lay out again it kept its previous geometry, so the desktop carried the new size while the browser window kept the old one. Its screen is now configured explicitly on every resolution change, and the result is recorded in the session log.
- Requires host verification: the reported fault appears only on a GPU host, which no test environment here provides.

## 1.2.0-beta.18 (Pocket ID fork)

- The session page no longer reports a refused request while loading. The client reads the streaming mode from the server before connecting, and the gateway was declining that one read, leaving the choice to a fallback. Token and control endpoints stay refused.
- The sign-in and session pages now carry an icon instead of requesting one that does not exist.

## 1.2.0-beta.17 (Pocket ID fork)

- Completed validation of the coordinated Selkies/Pixelflux/pcmflux upgrade. Updated regression tests to use the renamed manual-resolution setting and native clipboard API.
- Corrected the rebased file manager to create one Delete column rather than one per file.
- Added 150% DPI coverage to the full-viewport sizing test. Software-rendered fresh login, resize and reconnect pass; the reported GPU-host startup defect still requires host verification.

## 1.2.0-beta.16 (Pocket ID fork)

- The browser now fills its desktop on the first connection. Earlier releases sized the capture and the desktop correctly while the browser window kept the size it started with, so a session could open small until the viewer was resized twice.
- Updated Selkies, Pixelflux and pcmflux to matching tested revisions. The private-desktop changes previously carried across eight separate patches are now one rebased patch against that revision, and both web dependency lockfiles were regenerated for it.
- Added a sizing test that checks the browser's lower corners reach the streamed desktop's corners, on first connection and after each resize, rather than only checking the encoded frame size.

## 1.2.0-beta.15 (Pocket ID fork)

- Forward supported streaming quality, cursor, clipboard, GPU, locale and keyboard settings into private desktops while excluding broker credentials and session routing overrides.
- Validate explicit GPU node access as the private UID, support separate compositor and encoder selection, probe Intel i965 compatibility, and record GPU and component startup diagnostics.
- Check both the broker and HTTPS proxy for readiness, report aggregate failed-session health, and retain bounded root-only diagnostic log tails after session failure.

## 1.2.0-beta.14 (Pocket ID fork)

- Completed the previous release's fix. The screen is now sized on the paths a new session actually takes, ahead of the capture start that sizes the browser's view over it, and the result is recorded in the session log. In 1.2.0-beta.13 the new step never ran.

## 1.2.0-beta.13 (Pocket ID fork)

- The browser no longer sometimes opens at a small size inside a correctly sized stream. Starting a capture sizes the browser's view but not the screen holding it, so a desktop created before its first client kept the geometry it started with, and no later resize to that same size corrected it. The screen is now sized along with its display, backported from upstream Selkies.

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
