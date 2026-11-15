#!/usr/bin/env bash
# Runs as root inside the live appliance. Never fails: every finding is printed.
set +e
out=/trial-out; mkdir -p "$out"; chmod 777 "$out"
say() { printf '\n===== %s =====\n' "$*"; }
brave=$(cat /tmp/brave.pid)
tr '\0' '\n' < "/proc/$brave/environ" | grep -E '^(XDG_RUNTIME_DIR|WAYLAND_DISPLAY|PULSE_SERVER|DBUS_SESSION_BUS_ADDRESS|HOME|XDG_CONFIG_HOME|XDG_DATA_HOME|XDG_CACHE_HOME)=' > "$out/env"
say 'Session environment'; cat "$out/env"
as() { runuser -u braveuser -- env $(cat "$out/env") "$@"; }

say 'Versions'
dpkg-query -W nautilus gnome-sushi bubblewrap libgtk-4-1 'libgtk-3-0*' 'gvfs*' 'localsearch*' 'tracker*' 'libtinysparql*' 2>/dev/null
grep -E 'upgraded|After this operation|Need to get' /trial-apt-plan.txt
grep -c '^Inst ' /trial-apt-plan.txt | sed 's/^/packages added: /'
dpkg -L gnome-sushi | grep -E '/bin/|libexec'
grep -rhoE "(Gtk|GtkSource|EvinceView|WebKit2?|Gst): *'[0-9.]+'" /usr/share/sushi /usr/lib/*/sushi 2>/dev/null | sort -u
sysctl kernel.unprivileged_userns_clone user.max_user_namespaces 2>&1
grep -E 'NoNewPrivs|Seccomp:|CapEff' /proc/self/status

say 'Sample files'
home=$(grep '^HOME=' "$out/env" | cut -d= -f2); dl="$home/Downloads"
as mkdir -p "$dl/Folder"
as python3 - "$dl" <<'PY'
import sys, pathlib
d = pathlib.Path(sys.argv[1])
(d / 'notes.txt').write_text('Trial text file\n' * 20)
(d / 'script.py').write_text('def hello():\n    return "trial"\n')
PY
as ffmpeg -loglevel error -y -f lavfi -i testsrc=size=640x360:rate=25 -frames:v 1 "$dl/picture.png"
as ffmpeg -loglevel error -y -f lavfi -i testsrc=size=640x360:rate=25 -f lavfi -i sine=frequency=440 -t 20 -c:v libx264 -pix_fmt yuv420p -c:a aac "$dl/video.mp4"
as ffmpeg -loglevel error -y -f lavfi -i sine=frequency=660 -t 20 "$dl/sound.mp3"
as python3 - "$dl" <<'PY'
import sys, cairo
s = cairo.PDFSurface(sys.argv[1] + '/document.pdf', 595, 842)
c = cairo.Context(s)
for n in (1, 2, 3):
    c.set_font_size(48); c.move_to(80, 200); c.show_text(f'Trial page {n}'); c.show_page()
s.finish()
PY
ls -la "$dl"

shot() { as grim "$out/$1.png" 2>&1 | head -3; }
stop() { pkill -u braveuser -f "$1"; sleep 2; }
shot 00-before

say 'GNOME Files in the live desktop'
as sh -c "nautilus --new-window '$dl' > $out/nautilus.log 2>&1 &"
sleep 12; shot 01-files
pgrep -u braveuser -a nautilus || echo 'NAUTILUS NOT RUNNING'
head -30 "$out/nautilus.log"

say 'Space bar from GNOME Files'
as nautilus -s "$dl/picture.png" > /dev/null 2>&1 &
sleep 5; as wtype -k space 2>&1 | head -3; sleep 6; shot 02-files-space
pgrep -u braveuser -af sushi || echo 'sushi not started by the space bar'
stop sushi; stop nautilus

say 'Sushi on its own'
for f in picture.png document.pdf notes.txt script.py video.mp4 sound.mp3; do
    as sh -c "sushi '$dl/$f' > $out/sushi-$f.log 2>&1 &"
    sleep 9; shot "03-sushi-$f"
    pgrep -u braveuser -f sushi > /dev/null && echo "$f: sushi running" || echo "$f: SUSHI NOT RUNNING"
    case $f in video.mp4|sound.mp3)
        echo "$f: playback streams on the session audio server:"; as pactl list short sink-inputs 2>&1 | head -5 ;;
    esac
    head -8 "$out/sushi-$f.log"
    stop sushi
done

say 'Sandbox: can this container create one'
as bwrap --ro-bind / / true && echo 'basic sandbox: yes' || echo 'basic sandbox: NO'
as bwrap --ro-bind / / --proc /proc true && echo 'new /proc: yes' || echo 'new /proc: NO'
as bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev true && echo 'all namespaces: yes' || echo 'all namespaces: NO'

rt=$(grep '^XDG_RUNTIME_DIR=' "$out/env" | cut -d= -f2)
wl=$(grep '^WAYLAND_DISPLAY=' "$out/env" | cut -d= -f2); case $wl in /*) ;; *) wl="$rt/$wl" ;; esac
pulse=$(grep '^PULSE_SERVER=' "$out/env" | cut -d= -f2 | sed 's/^unix://')
box() {
    as bwrap --unshare-all --die-with-parent --new-session \
        --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/sbin /sbin --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
        --ro-bind /etc/fonts /etc/fonts --ro-bind /etc/alternatives /etc/alternatives --ro-bind-try /etc/ld.so.cache /etc/ld.so.cache \
        --proc /proc --dev /dev --tmpfs /tmp --dir /run/user --dir /home/user \
        --bind "$dl" /home/user/Downloads --ro-bind "$wl" /run/user/wayland-0 --ro-bind-try "$pulse" /run/user/pulse \
        --setenv HOME /home/user --setenv XDG_RUNTIME_DIR /run/user --setenv WAYLAND_DISPLAY wayland-0 \
        --setenv PULSE_SERVER unix:/run/user/pulse --unsetenv DBUS_SESSION_BUS_ADDRESS --chdir /home/user "$@"
}
say 'Sandbox: what is visible inside'
box sh -c 'echo "root:"; ls /; echo "etc:"; ls /etc; echo "home:"; ls -la /home/user /home/user/Downloads; echo "config:"; ls /config 2>&1; echo "processes:"; ls /proc | grep -c "^[0-9]"; id'
box sh -c 'cp /usr/bin/true /home/user/Downloads/prog && chmod +x /home/user/Downloads/prog && /home/user/Downloads/prog && echo "a file in Downloads CAN be executed" || echo "a file in Downloads cannot be executed"; rm -f /home/user/Downloads/prog'
box sh -c 'echo x > /usr/trial 2>&1 && echo "system is WRITABLE" || echo "system is read-only"'

say 'Sandbox: GNOME Files and Sushi inside it'
box dbus-run-session -- sh -c 'nautilus --new-window /home/user/Downloads > /home/user/Downloads/.boxed.log 2>&1 & sleep 14; sushi /home/user/Downloads/video.mp4 >> /home/user/Downloads/.boxed.log 2>&1 & sleep 3000' &
sleep 16; shot 04-boxed-files
pgrep -u braveuser -a nautilus || echo 'BOXED NAUTILUS NOT RUNNING'
sleep 12; shot 05-boxed-sushi
pgrep -u braveuser -af sushi || echo 'BOXED SUSHI NOT RUNNING'
echo 'boxed playback streams:'; as pactl list short sink-inputs 2>&1 | head -5
head -30 "$dl/.boxed.log"
pkill -u braveuser bwrap; stop sushi; stop nautilus

say 'What a session user can read outside its own storage'
useradd -m other 2>/dev/null
for who in braveuser other; do
    runuser -u "$who" -- find / -xdev \( -path /proc -o -path /sys -o -path /usr -o -path /dev -o -path /var/lib/dpkg -o -path /var/cache -o -path /var/lib/apt -o -path /trial-out \) -prune -o -type f -readable -print 2>/dev/null > "$out/readable-$who.txt"
    echo "--- $who: readable files outside /usr, by top folder"
    cut -d/ -f2 "$out/readable-$who.txt" | sort | uniq -c | sort -rn
    echo "--- $who: readable names that look sensitive"
    grep -iE 'secret|passw|token|credential|private|\.key$|\.pem$|\.env$|htpasswd|shadow|cookie' "$out/readable-$who.txt" | head -40
    echo "--- $who: writable folders outside its home"
    runuser -u "$who" -- find / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path "$home" -o -path /home/other \) -prune -o -type d -writable -print 2>/dev/null | head -30
done
echo '--- other: readable files under the session user storage'
grep -c "^$home/" "$out/readable-other.txt"; grep "^$home/" "$out/readable-other.txt" | head -40
echo '--- other: readable under /etc, /run, /tmp, /opt (non-Brave), /root, /defaults'
grep -E '^/(run|tmp|root|defaults|app|srv)/' "$out/readable-other.txt" | head -60
grep -E '^/etc/' "$out/readable-other.txt" | grep -vE '^/etc/(fonts|ssl/certs|alternatives|X11|dbus-1|pulse|gtk|xdg|apt|dpkg|ca-certificates|terminfo|perl|python|ld\.so|pam\.d|security|systemd|init\.d|rc|cron|logrotate|profile|skel|default|selinux|vulkan|glvnd|OpenCL|mime|emacs|bash_completion|sysctl|update-motd|kernel|modprobe|udev|sensors|libnl|gss|sv|runit|s6|cont|services\.d)' | head -80
echo '--- process command lines visible to another user'
runuser -u other -- ps -eo user:12,args --cols 400 | head -60
say 'Done'
