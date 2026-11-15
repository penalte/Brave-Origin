# One-off trial: GNOME Files, Sushi and a sandbox tool on top of the beta image.
ARG BASE
FROM ${BASE}
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends -s nautilus gnome-sushi bubblewrap \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav \
        gstreamer1.0-pulseaudio gstreamer1.0-gtk3 > /trial-apt-plan.txt 2>&1 && \
    apt-get install -y --no-install-recommends nautilus gnome-sushi bubblewrap \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav \
        gstreamer1.0-pulseaudio gstreamer1.0-gtk3 grim wtype sway wlrctl && \
    rm -rf /var/lib/apt/lists/*
