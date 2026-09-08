# syntax=docker/dockerfile:1
FROM debian:trixie-slim

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=C.UTF-8 \
    DISPLAY=:1 \
    HOME=/config

# Build arguments
ARG BRAVE_ORIGIN_VERSION=""
ARG KASMVNC_VERSION="1.5.0"
# This release is validated for linux/amd64; the package checksum pins that build.

# 1. Install prerequisites, desktop dependencies, and fonts
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        apache2-utils \
        iproute2 \
        python3 \
        python3-websockets \
        tzdata \
        util-linux \
        tini \
        procps \
        openssl \
        ssl-cert \
        xdg-utils \
        openbox \
        nginx-light \
        dbus-x11 \
        pulseaudio \
        pulseaudio-utils \
        libasound2 \
        libasound2-plugins \
        fonts-liberation \
        fonts-dejavu-core \
        fonts-noto-color-emoji \
        xauth \
        xclip \
        x11-utils \
        x11-xkb-utils \
        xkb-data \
        libswitch-perl \
        libyaml-tiny-perl \
        libhash-merge-simple-perl \
        libdatetime-perl \
        libdatetime-timezone-perl \
        libtry-tiny-perl \
    ; \
    # 2. Add official Brave APT repository
    curl -fsSLo /usr/share/keyrings/brave-browser-archive-keyring.gpg \
        https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg; \
    curl -fsSLo /etc/apt/sources.list.d/brave-browser-release.sources \
        https://brave-browser-apt-release.s3.brave.com/brave-browser.sources; \
    apt-get update; \
    if [ -n "${BRAVE_ORIGIN_VERSION}" ] && [ "${BRAVE_ORIGIN_VERSION}" != "latest" ]; then \
        apt-get install -y --no-install-recommends "brave-origin=${BRAVE_ORIGIN_VERSION}"; \
    else \
        apt-get install -y --no-install-recommends brave-origin; \
    fi; \
    if [ -f /opt/brave.com/brave-origin/chrome-sandbox ]; then \
        chown root:root /opt/brave.com/brave-origin/chrome-sandbox; \
        chmod 4755 /opt/brave.com/brave-origin/chrome-sandbox; \
    fi; \
    if [ -f /opt/brave.com/brave-origin/apparmor.d/brave-origin-stable ]; then \
        mkdir -p /etc/apparmor.d; \
        cp /opt/brave.com/brave-origin/apparmor.d/brave-origin-stable /etc/apparmor.d/brave-origin; \
    fi; \
    # 3. Download, validate, and install verified official KasmVNC release asset for Debian Trixie
    ARCH="$(dpkg --print-architecture)"; \
    KASMVNC_DEB="kasmvncserver_trixie_${KASMVNC_VERSION}_${ARCH}.deb"; \
    KASMVNC_URL="https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/${KASMVNC_DEB}"; \
    echo "Downloading verified KasmVNC release asset from: ${KASMVNC_URL}"; \
    if ! curl -fsSL -o /tmp/kasmvnc.deb "${KASMVNC_URL}"; then \
        echo "ERROR: Failed to download official KasmVNC release asset '${KASMVNC_DEB}' for architecture '${ARCH}' from '${KASMVNC_URL}'!" >&2; \
        exit 1; \
    fi; \
    if [ ! -s /tmp/kasmvnc.deb ]; then \
        echo "ERROR: Downloaded KasmVNC file is empty (0 bytes)!" >&2; \
        exit 1; \
    fi; \
    echo "80b241de7dfe53bba2b7e1cc5ac8c5246d72271efa16be2d4f76607f30fab1c4  /tmp/kasmvnc.deb" | sha256sum -c -; \
    # Validate package integrity and metadata using dpkg-deb
    if ! dpkg-deb -I /tmp/kasmvnc.deb >/dev/null 2>&1; then \
        echo "ERROR: Downloaded file is not a valid Debian package archive!" >&2; \
        exit 1; \
    fi; \
    PKG_NAME="$(dpkg-deb -f /tmp/kasmvnc.deb Package 2>/dev/null || echo "")"; \
    if [ "${PKG_NAME}" != "kasmvncserver" ]; then \
        echo "ERROR: Unexpected package name '${PKG_NAME}' (expected 'kasmvncserver')!" >&2; \
        exit 1; \
    fi; \
    PKG_VER="$(dpkg-deb -f /tmp/kasmvnc.deb Version 2>/dev/null || echo "")"; \
    case "${PKG_VER}" in \
        "${KASMVNC_VERSION}"*) ;; \
        *) \
            echo "ERROR: Package version '${PKG_VER}' does not match expected version '${KASMVNC_VERSION}'!" >&2; \
            exit 1; \
            ;; \
    esac; \
    PKG_ARCH="$(dpkg-deb -f /tmp/kasmvnc.deb Architecture 2>/dev/null || echo "")"; \
    if [ "${PKG_ARCH}" != "${ARCH}" ]; then \
        echo "ERROR: Package architecture '${PKG_ARCH}' does not match target architecture '${ARCH}'!" >&2; \
        exit 1; \
    fi; \
    echo "Successfully validated Debian package: ${PKG_NAME} (${PKG_VER}) [${PKG_ARCH}]"; \
    apt-get install -y --no-install-recommends /tmp/kasmvnc.deb; \
    rm -f /tmp/kasmvnc.deb; \
    # 4. Strict Build-Time Package Verification
    dpkg-query -W -f='${Package} ${Version}\n' brave-origin; \
    if dpkg -s brave-browser 2>/dev/null || dpkg -s brave-browser-beta 2>/dev/null || dpkg -s brave-browser-nightly 2>/dev/null || dpkg -s brave-browser-dev 2>/dev/null; then \
        echo "ERROR: Standard Brave Browser packages detected in image build!" >&2; \
        exit 1; \
    fi; \
    echo "========================================================"; \
    echo " Build Verification Summary:"; \
    echo " Base OS: Debian 13 Trixie Slim"; \
    echo " Browser package: $(dpkg-query -W -f='${Package} (${Version})' brave-origin)"; \
    echo " Browser channel/product: Brave Origin Release"; \
    echo " Standard Brave Browser installed: No"; \
    echo " KasmVNC installed: Yes (${PKG_NAME} ${PKG_VER} [${PKG_ARCH}])"; \
    echo " Persistent directory: /config"; \
    echo "========================================================"; \
    # 5. Clean APT caches to minimize final image size
    apt-get purge -y --auto-remove gnupg; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-cjk && rm -rf /var/lib/apt/lists/*

# Configure Openbox minimal window management defaults (auto-maximize browser, no terminal menu)
RUN set -eux; \
    mkdir -p /etc/xdg/openbox; \
    cat <<'EOF' > /etc/xdg/openbox/rc.xml
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <applications>
    <application class="*">
      <maximized>yes</maximized>
      <decor>no</decor>
      <focus>yes</focus>
    </application>
  </applications>
  <margins>
    <top>0</top>
    <bottom>0</bottom>
    <left>0</left>
    <right>0</right>
  </margins>
  <resistance>
    <strength>10</strength>
    <screen_edge_strength>20</screen_edge_strength>
  </resistance>
  <focus>
    <focusNew>yes</focusNew>
    <followMouse>no</followMouse>
  </focus>
</openbox_config>
EOF

# Setup standard user, group, and persistent mount points
RUN set -eux; \
    groupadd -g 1000 braveuser; \
    useradd -u 1000 -g braveuser -G ssl-cert -d /config -s /bin/bash -m braveuser; \
    mkdir -p /config/profile \
             /config/downloads \
             /config/kasmvnc/certs \
             /config/state \
             /tmp/brave-cache \
             /tmp/.X11-unix \
             /tmp/runtime-braveuser \
             /run/lock; \
    chmod 1777 /tmp/.X11-unix; chmod 755 /run/lock; \
    chmod 700 /tmp/runtime-braveuser /tmp/brave-cache; \
    chown -R braveuser:braveuser /config /tmp/runtime-braveuser /tmp/brave-cache

# Copy configuration and scripts
COPY config/kasmvnc.yaml /etc/kasmvnc/kasmvnc.yaml
COPY config/nginx.conf /etc/nginx/nginx.conf
COPY scripts/prepare-storage.py /usr/local/bin/prepare-storage.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY scripts/start-session.sh /usr/local/bin/start-session.sh
COPY scripts/update-brave.sh /usr/local/bin/update-brave.sh
COPY scripts/profile-control.sh /usr/local/bin/profile-control.sh
COPY scripts/reset-password.sh /usr/local/bin/reset-password.sh
COPY scripts/audio-server.py /usr/local/bin/audio-server.py
COPY config/audio-client.js /etc/kasmvnc/audio-client.js

RUN mkdir -p /etc/brave/policies/managed && \
    printf '%s\n' '{"BookmarkBarEnabled": true, "DownloadDirectory": "/config/downloads"}' > /etc/brave/policies/managed/policies.json
COPY scripts/healthcheck.sh /usr/local/bin/healthcheck.sh
RUN chmod +x /usr/local/bin/*.sh /usr/local/bin/audio-server.py
COPY config/clipboard-client.js /usr/share/kasmvnc/www/clipboard-client.js
COPY scripts/patch-client.py /tmp/patch-client.py
RUN python3 /tmp/patch-client.py && rm /tmp/patch-client.py
ARG VERSION=1.0.0-x11
ARG BUILD_COMMIT=dev
LABEL org.opencontainers.image.title="Brave Origin X11" \
      org.opencontainers.image.description="Brave Origin with X11 and KasmVNC remote access" \
      org.opencontainers.image.source="https://github.com/shoyrock/Brave-Origin" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${BUILD_COMMIT}"
RUN echo "${BUILD_COMMIT}" > /etc/brave-origin-build
ENV AUTH_ENABLED="" ENABLE_AUDIO=true ENABLE_GPU=true AUTO_UPDATE=true
VOLUME ["/config"]
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["bash", "/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
