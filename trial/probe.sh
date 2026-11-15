#!/usr/bin/env bash
# Runs as root inside the live appliance. Never fails: every finding is printed.
set +e
out=/trial-out; mkdir -p "$out"; chmod 777 "$out"
say() { printf '\n===== %s =====\n' "$*"; }
brave=$(cat /tmp/brave.pid)
runuser -u braveuser -- cat "/proc/$brave/environ" | tr '\0' '\n' | grep -E '^(XDG_RUNTIME_DIR|WAYLAND_DISPLAY|PULSE_SERVER|DBUS_SESSION_BUS_ADDRESS|HOME|XDG_CONFIG_HOME|XDG_DATA_HOME|XDG_CACHE_HOME)=' > "$out/env"
[ -s "$out/env" ] || printf '%s\n' HOME=/config XDG_RUNTIME_DIR=/tmp/runtime-braveuser WAYLAND_DISPLAY=wayland-1 PULSE_SERVER=unix:/tmp/runtime-braveuser/pulse/native DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/runtime-braveuser/dbus/session_bus_socket > "$out/env"
echo XDG_SESSION_TYPE=wayland >> "$out/env"; ls -la /tmp/runtime-braveuser
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
home=$(grep '^HOME=' "$out/env" | cut -d= -f2); dl="$home/downloads"
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
pgrep -u braveuser -af 'sushi|NautilusPreviewer' || echo 'sushi not started by the space bar'
stop sushi; stop NautilusPreviewer; stop nautilus

say 'Sushi on its own'
for f in picture.png document.pdf notes.txt script.py video.mp4 sound.mp3; do
    as sh -c "sushi '$dl/$f' > $out/sushi-$f.log 2>&1 &"
    sleep 9; shot "03-sushi-$f"
    pgrep -u braveuser -f 'sushi|NautilusPreviewer' > /dev/null && echo "$f: sushi running" || echo "$f: SUSHI NOT RUNNING"
    case $f in video.mp4|sound.mp3)
        echo "$f: playback streams on the session audio server:"; as pactl list short sink-inputs 2>&1 | head -5 ;;
    esac
    head -8 "$out/sushi-$f.log"
    stop sushi; stop NautilusPreviewer
done

say 'Sandbox: can this container create one'
as bwrap --ro-bind / / true && echo 'basic sandbox: yes' || echo 'basic sandbox: NO'
as bwrap --ro-bind / / --proc /proc true && echo 'new /proc: yes' || echo 'new /proc: NO'
as bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev true && echo 'all namespaces: yes' || echo 'all namespaces: NO'

rt=$(grep '^XDG_RUNTIME_DIR=' "$out/env" | cut -d= -f2)
scratch="$rt/files-app"; as mkdir -p "$scratch/config" "$scratch/cache" "$scratch/data" "$scratch/tmp"
box() {
    as env HOME="$dl" XDG_CONFIG_HOME="$scratch/config" XDG_CACHE_HOME="$scratch/cache" XDG_DATA_HOME="$scratch/data" \
        TMPDIR="$scratch/tmp" python3 /trial-confine.py "$dl" "$rt" -- "$@"
}
say 'Landlock sandbox: what is reachable inside'
box sh -c 'id; for p in / /etc /config /config/profile /home /tmp /run /opt /root; do ls "$p" > /dev/null 2>&1 && echo "CAN list $p" || echo "cannot list $p"; done
cat /etc/passwd > /dev/null 2>&1 && echo "CAN read /etc/passwd" || echo "cannot read /etc/passwd"
cat /etc/brave/policies/managed/policies.json > /dev/null 2>&1 && echo "CAN read browser policy" || echo "cannot read browser policy"
echo "own storage:"; ls "$HOME"
echo hello > "$HOME/written.txt" && echo "can write own storage"; rm -f "$HOME/written.txt"
cp /usr/bin/true "$HOME/prog" && chmod +x "$HOME/prog"; "$HOME/prog" && echo "a file in storage CAN be executed" || echo "a file in storage cannot be executed"; rm -f "$HOME/prog"
echo x > /usr/trial 2>/dev/null && echo "system is WRITABLE" || echo "system is read-only"'

say 'Landlock sandbox: GNOME Files and Sushi inside it'
box sh -c "dbus-daemon --session --address=unix:path=$scratch/bus --nofork > $scratch/bus.log 2>&1 &
sleep 2; export DBUS_SESSION_BUS_ADDRESS=unix:path=$scratch/bus
nautilus --new-window \"\$HOME\" > $scratch/boxed-files.log 2>&1 &
sleep 14; nautilus -s \"\$HOME/picture.png\" > /dev/null 2>&1; sleep 4; wtype -k space; sleep 12
pkill -f NautilusPreviewer; sushi \"\$HOME/video.mp4\" > $scratch/boxed-sushi.log 2>&1 & sleep 3000" > "$out/boxed.log" 2>&1 &
sleep 14; shot 04-boxed-files
pgrep -u braveuser -a nautilus || echo 'BOXED NAUTILUS NOT RUNNING'
sleep 12; shot 05-boxed-files-space
pgrep -u braveuser -af 'sushi|NautilusPreviewer' || echo 'boxed sushi not started by the space bar'
sleep 16; shot 06-boxed-sushi-video
pgrep -u braveuser -af 'sushi|NautilusPreviewer' || echo 'BOXED SUSHI NOT RUNNING'
echo 'boxed playback streams:'; as pactl list short sink-inputs 2>&1 | head -5
cat "$out/boxed.log"; head -25 "$scratch/boxed-files.log"; head -15 "$scratch/boxed-sushi.log"; head -5 "$scratch/bus.log"
pkill -u braveuser -f 'sleep 3000'; pkill -u braveuser dbus-daemon -n; stop sushi; stop NautilusPreviewer; stop nautilus

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
echo '--- two files other users can read'
head -c 500 /run/brave-network/config.json; echo; head -c 400 /etc/nginx/conf.d/auth.conf; echo
say 'Done'
