# syntax=docker/dockerfile:1
FROM ghcr.io/linuxserver/baseimage-selkies:debiantrixie@sha256:7f4f69e5184e3e1876e96ca0c5d66bc3ef5ffe3d47a910cbf6366fe59db3e972 AS selkies-upstream

# Selkies backend and dashboard are built from the same tested revision.
FROM node:22-trixie-slim AS selkies-build
RUN apt-get update && apt-get install -y --no-install-recommends patch python3 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:2c2fd6e9e43356d18d2919f016a70d7e9a099832aaef18999d105f459d154712 \
    https://codeload.github.com/selkies-project/selkies/tar.gz/9762dd8c21af0292e069b05cdc49ad449fa04f54 /tmp/selkies.tar.gz
RUN mkdir /selkies-src && tar -xzf /tmp/selkies.tar.gz -C /selkies-src --strip-components=1 && rm /tmp/selkies.tar.gz
COPY patches/*.patch /selkies-src/patches/
COPY dependencies/selkies-web-core.package-lock.json /selkies-src/addons/selkies-web-core/package-lock.json
COPY dependencies/selkies-dashboard.package-lock.json /selkies-src/addons/selkies-dashboard/package-lock.json
COPY tests/client-clipboard.mjs /tmp/client-clipboard.mjs
COPY tests/client-audio.mjs /tmp/client-audio.mjs
COPY tests/app-screen-recovery.py /tmp/app-screen-recovery.py
RUN cd /selkies-src && \
    for p in patches/*.patch; do [ -f "$p" ] && patch -p1 < "$p"; done && \
    python3 /tmp/app-screen-recovery.py /selkies-src/src/selkies/selkies.py && \
    node /tmp/client-clipboard.mjs /selkies-src/addons/selkies-web-core/lib/clipboard-sync.js && \
    node /tmp/client-audio.mjs /selkies-src/addons/selkies-web-core/selkies-ws-core.js && \
    cd /selkies-src/addons/selkies-web-core && \
    npm ci && \
    npm run build && \
    cd /selkies-src/addons/selkies-dashboard && \
    npm ci && \
    npm run build && \
    mkdir /selkies-package && \
    cp /selkies-src/pyproject.toml /selkies-src/README.md /selkies-src/LICENSE /selkies-package/ && \
    cp -r /selkies-src/src /selkies-package/ && \
    rm -rf /selkies-src/.git /selkies-src/patches

# Build only the window manager; Brave remains the official, unmodified package.
FROM debian:trixie-slim AS labwc-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential meson ninja-build pkg-config patch xz-utils ca-certificates \
    libwlroots-0.18-dev libxml2-dev libglib2.0-dev libcairo2-dev \
    libpango1.0-dev libpng-dev librsvg2-dev libcmocka-dev wayland-protocols \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:746be2ff2d0c0c0b795c97fa24c7058f75586685c88a1194c243b6a846f938a5 \
    https://codeload.github.com/labwc/labwc/tar.gz/refs/tags/0.8.3 /tmp/labwc.tar.gz
RUN mkdir /labwc && tar -xzf /tmp/labwc.tar.gz -C /labwc --strip-components=1 && rm /tmp/labwc.tar.gz
COPY patches/labwc/lock-maximized.patch /tmp/lock-maximized.patch
COPY patches/labwc/README.md /labwc/BRAVE-ORIGIN-CHANGES.md
COPY Dockerfile /labwc/Brave-Origin.Dockerfile
RUN cd /labwc && patch -p1 < /tmp/lock-maximized.patch && \
    meson setup /labwc-build --buildtype=release --wrap-mode=nofallback \
        -Dxwayland=disabled -Dicon=disabled -Dman-pages=disabled -Dtest=enabled \
        -Db_pie=true -Db_lto=true -Dc_args='-fstack-protector-strong -D_FORTIFY_SOURCE=3' \
        -Dc_link_args='-Wl,-z,relro,-z,now' && \
    meson compile -C /labwc-build -j 2 && meson test -C /labwc-build --print-errorlogs && \
    strip /labwc-build/labwc && tar -cJf /labwc-source.tar.xz -C / labwc

FROM debian:trixie-slim


ENV DEBIAN_FRONTEND=noninteractive \
    PUID=1000 \
    PGID=1000 \
    UMASK=022 \
    TZ=Etc/UTC \
    AUTO_UPDATE=true \
    ENABLE_AUDIO=true \
    AUTH_ENABLED=false \
    PIXELFLUX_WAYLAND=true \
    SELKIES_ENABLE_BASIC_AUTH=false \
    SELKIES_ENABLE_DUAL_MODE=false \
    SELKIES_PORT=8082 \
    CUSTOM_WS_PORT=8082 \
    SELKIES_ADDR=127.0.0.1 \
    XDG_RUNTIME_DIR=/tmp/runtime-braveuser \
    WAYLAND_DISPLAY=wayland-1 \
    PULSE_SERVER=unix:/tmp/runtime-braveuser/pulse/native

# Without this the NVIDIA container runtime injects compute support only: Selkies
# encodes happily on the card while the browser has no EGL/GL driver to render
# with. Device selection stays with the operator's --gpus flag.
ENV NVIDIA_DRIVER_CAPABILITIES=all

# 1. Add Official Brave Origin Apt Repository (Release Channel)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg \
    -o /etc/apt/keyrings/brave-browser-archive-keyring.gpg && \
    chmod 644 /etc/apt/keyrings/brave-browser-archive-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/brave-browser-archive-keyring.gpg arch=amd64] https://brave-browser-apt-release.s3.brave.com/ stable main" \
    > /etc/apt/sources.list.d/brave-browser-release.list && \
    rm -rf /var/lib/apt/lists/*

# 2. Base Utilities, Wayland Compositor, Audio, Graphics, Python & Brave Origin
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    procps \
    iproute2 \
    nftables \
    openssl \
    nginx \
    apache2-utils \
    pulseaudio \
    pulseaudio-utils \
    dbus \
    python3-gi \
    python3-dbus \
    gir1.2-gtk-3.0 \
    labwc \
    libwlroots-0.18 \
    wtype \
    wl-clipboard \
    wayland-protocols \
    libwayland-client0 \
    libwayland-server0 \
    libwayland-cursor0 \
    libwayland-egl1 \
    libdrm2 \
    libdrm-intel1 \
    libdrm-amdgpu1 \
    libdrm-radeon1 \
    libdrm-nouveau2 \
    libgbm1 \
    libpixman-1-0 \
    libcairo2 \
    libcairo-gobject2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgl1-mesa-dri \
    libglx-mesa0 \
    libegl1 \
    libgl1 \
    libgles2 \
    libvulkan1 \
    libnvidia-egl-wayland1 \
    mesa-vulkan-drivers \
    mesa-va-drivers \
    intel-media-va-driver \
    i965-va-driver \
    vainfo \
    libva2 \
    libva-drm2 \
    libva-wayland2 \
    python3 \
    python3-pip \
    python3-pil \
    python3-websockets \
    python3-aiohttp \
    python3-aiofiles \
    python3-msgpack \
    fonts-liberation \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    xdg-utils \
    desktop-file-utils \
    shared-mime-info \
    hicolor-icon-theme \
    libnss3 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libcups2t64 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libasound2t64 \
    brave-origin \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends tini tzdata util-linux && rm -rf /var/lib/apt/lists/*

# Optional official WARP client. No registration or tunnel during image build.
ARG INSTALL_WARP=true
RUN if [ "$INSTALL_WARP" = true ]; then \
      curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg -o /etc/apt/keyrings/cloudflare-warp.asc && \
      echo 'deb [signed-by=/etc/apt/keyrings/cloudflare-warp.asc] https://pkg.cloudflareclient.com/ trixie main' > /etc/apt/sources.list.d/cloudflare-warp.list && \
      apt-get update && apt-get install -y --no-install-recommends cloudflare-warp=2026.7.1377.0 && \
      rm -rf /var/lib/apt/lists/*; \
    elif [ "$INSTALL_WARP" != false ]; then exit 1; fi

# 2.5 Brave managed policy: bookmarks bar always visible in the locked session
RUN mkdir -p /etc/brave/policies/managed && \
    printf '%s\n' '{"BookmarkBarEnabled": true, "DownloadDirectory": "/config/downloads", "IncognitoModeAvailability": 1, "TorDisabled": true, "BrowserGuestModeEnabled": false, "BrowserAddPersonEnabled": false, "EnableMediaRouter": false, "ShowCastIconInToolbar": false}' > /etc/brave/policies/managed/policies.json && \
    chmod 644 /etc/brave/policies/managed/policies.json

# 3. Use the donor's supporting libraries and override both capture components.
COPY --from=selkies-upstream /lsiopy/lib/python3.13/site-packages/ /usr/local/lib/python3.13/dist-packages/
COPY --from=selkies-upstream /usr/bin/wtype /usr/local/bin/wtype
ADD --checksum=sha256:67deb00f5ad2fc3c05fa04896827432cb6a330d7f8b6cb5db7df06d157441887 \
    https://github.com/selkies-project/pixelflux/releases/download/f23caf4/pixelflux-2.1.0-cp313-cp313-manylinux_2_28_x86_64.whl /tmp/pixelflux-2.1.0-cp313-cp313-manylinux_2_28_x86_64.whl
ADD --checksum=sha256:e6d5c339a057b59d7fedae55698d04d39dd4dfbb4221d2071e231c496dabb180 \
    https://github.com/selkies-project/pcmflux/releases/download/584f875/pcmflux-2.1.0-cp313-cp313-manylinux_2_28_x86_64.whl /tmp/pcmflux-2.1.0-cp313-cp313-manylinux_2_28_x86_64.whl
RUN pip install --no-deps --break-system-packages /tmp/pixelflux-*.whl /tmp/pcmflux-*.whl && \
    rm /tmp/pixelflux-*.whl /tmp/pcmflux-*.whl
COPY dependencies/runtime.txt /tmp/runtime-requirements.txt
# Pelorus is the donor desktop's launcher; it is not used by this browser image.
RUN rm -rf /usr/local/lib/python3.13/dist-packages/pelorus /usr/local/lib/python3.13/dist-packages/pelorus-*.dist-info && \
    pip install --break-system-packages --no-cache-dir --no-deps --require-hashes -r /tmp/runtime-requirements.txt && \
    rm /tmp/runtime-requirements.txt
# Install the matching Selkies Python backend and web dashboard.
COPY --from=selkies-build /selkies-package /tmp/selkies-src
COPY --from=selkies-build /selkies-src/addons/selkies-dashboard/dist/ /usr/share/selkies/web/
RUN pip install --no-deps --no-build-isolation /tmp/selkies-src --break-system-packages && \
    mkdir -p /usr/share/selkies/web && \
    cp /opt/brave.com/brave-origin/product_logo_256.png /usr/share/selkies/web/icon.png && \
    cp /opt/brave.com/brave-origin/product_logo_256.png /usr/share/selkies/web/icon-512.png && \
    grep -rl "Selkies" /usr/share/selkies/web/index.html /usr/share/selkies/web/assets/ /usr/share/selkies/web/manifest.json 2>/dev/null | \
        xargs -r sed -i 's/Selkies/Brave Origin/g' && \
    rm -rf /tmp/selkies-src /root/.cache && \
    python3 -m pip check

# 4. Create Unprivileged Non-Root User (braveuser)
RUN groupadd -r render 2>/dev/null || true && \
    groupadd -g 1000 braveuser && \
    useradd -u 1000 -g braveuser -G audio,video,render -m -s /bin/bash braveuser && \
    mkdir -p /config /tmp/runtime-braveuser /tmp/brave-cache /etc/nginx/ssl /usr/share/selkies/web && \
    chmod 700 /tmp/runtime-braveuser && \
    chown -R braveuser:braveuser /config /tmp/runtime-braveuser /tmp/brave-cache

# 5. Copy Configuration and Session Scripts
COPY --from=labwc-build /labwc-build/labwc /usr/local/bin/labwc-browser
# Corresponding GPL source accompanies the modified compositor binary.
COPY --from=labwc-build /labwc-source.tar.xz /usr/local/share/brave-origin/labwc-source.tar.xz
COPY config/nginx.conf /etc/nginx/nginx.conf
COPY scripts/prepare-storage.py /usr/local/bin/prepare-storage.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY scripts/start-session.sh /usr/local/bin/start-session.sh
COPY scripts/update-brave.sh /usr/local/bin/update-brave.sh
COPY scripts/profile-control.sh /usr/local/bin/profile-control.sh
COPY scripts/reset-password.sh /usr/local/bin/reset-password.sh
COPY scripts/session-manager.py /usr/local/bin/session-manager.py
COPY scripts/multi-session.py /usr/local/bin/multi-session.py
COPY scripts/browser-network.py /usr/local/bin/browser-network.py
COPY scripts/user-desktop.sh /usr/local/bin/user-desktop.sh
COPY scripts/session-gpu.sh /usr/local/bin/session-gpu.sh
RUN chmod 755 /usr/local/bin/user-desktop.sh
COPY scripts/browser-session.sh /usr/local/bin/browser-session.sh
COPY scripts/file-picker.py /usr/local/bin/file-picker.py
COPY config/nginx-oidc.conf /etc/nginx/nginx-oidc.conf
COPY config/portal.html config/portal.js /usr/local/share/brave-origin/
RUN pip install --break-system-packages --no-cache-dir 'PyJWT[crypto]==2.13.0' && \
    chmod 755 /usr/local/bin/browser-session.sh

# Keep release metadata after dependency installation to reuse build layers.
ARG VERSION=1.0.0-beta.1
ARG BUILD_COMMIT=dev
LABEL org.opencontainers.image.title="Brave Origin" \
      org.opencontainers.image.description="Brave Origin browser with remote HTTPS access" \
      org.opencontainers.image.source="https://github.com/shoyrock/Brave-Origin" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${BUILD_COMMIT}"

# Entrypoint supplies the secure default while honoring legacy auth variables.
ENV AUTH_ENABLED=""
RUN echo "${BUILD_COMMIT}" > /etc/brave-origin-build

RUN chmod +x /usr/local/bin/entrypoint.sh \
             /usr/local/bin/start-session.sh \
             /usr/local/bin/update-brave.sh \
             /usr/local/bin/profile-control.sh \
             /usr/local/bin/reset-password.sh

VOLUME ["/config"]
EXPOSE 8443
COPY scripts/healthcheck.sh /usr/local/bin/healthcheck.sh
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["bash", "/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
