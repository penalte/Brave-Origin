# Included projects

This repository's MIT license covers its own configuration, scripts, and tests. Components in the image retain their upstream licenses and notices.

| Component | Source |
| --- | --- |
| Brave Origin | [Brave](https://github.com/brave/brave-browser), installed from the official stable APT repository. |
| KasmVNC | [Release 1.5.0](https://github.com/kasmtech/KasmVNC/releases/tag/v1.5.0), pinned by checksum in the Dockerfile. The clipboard hook is recorded in `scripts/patch-client.py` and `config/clipboard-client.js`. |
| Openbox, PulseAudio, Python websockets, and other Debian packages | Debian 13 Trixie repositories; installed notices are under `/usr/share/doc/`. |

KasmVNC's corresponding source is available with its pinned upstream release. This repository adds an audio client and relay and a small native-paste integration using KasmVNC’s existing clipboard transport. The build checks the upstream client checksum before applying the hook.
