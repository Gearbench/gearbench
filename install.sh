#!/usr/bin/env bash
#
# Install Gearbench for the current user.
#
# Usage:  ./install.sh            install the udev rules, the gearbench group
#                                 with you in it, the user unit, the launcher,
#                                 the Shell extension, the headsets' audio profiles
#                                 (restarting WirePlumber if one changed), the
#                                 spatial sound service (left off) and, with GTK 4
#                                 and libadwaita for Python present, the window
#         ./install.sh --uninstall
#         ./install.sh --help
#
# Installs:
#   /usr/local/lib/udev/rules.d/                 every udev rule in udev/, /dev/uinput's
#                                                among them (via sudo)
#   the gearbench system group, with you in it   (via sudo)
#   ~/.config/systemd/user/gearbench.service
#   ~/.config/systemd/user/gearbench-spatial.service
#                                                the headsets' spatial output, off until switched on
#   ~/.config/alsa-card-profile/mixer/profile-sets/gearbench-astro-a50.conf
#                                                the A50's Game and Chat as two outputs
#   ~/.config/wireplumber/wireplumber.conf.d/60-gearbench-astro-a50.conf
#                                                gives the A50 that profile set
#   ~/.config/wireplumber/wireplumber.conf.d/60-gearbench-logitech-g432.conf
#                                                gives the G432 PipeWire's analog-only.conf
#   ~/.local/bin/gearbench-ctl                   launcher
#   ~/.local/share/applications/...desktop       app grid entry
#   ~/.local/share/dbus-1/services/...service    D-Bus starts the window
#   ~/.local/share/icons/hicolor/...             the app icon, SVG
#   ~/.local/share/gnome-shell/extensions/gearbench-focus@gearbench.github.io/
#                                                the focused window for game profiles, enabled
#                                                in GNOME's settings; loads at the next login
#
# The install and --uninstall both remove what an install from before 1.1.0
# put under the old app id and extension uuid, and the install moves the
# dock's pin from the old app id to the new one. --uninstall removes the files
# above and leaves the gearbench group, with you in it, and the settings,
# ~/.config/gearbench, where they are.
#
# The daemon runs as you, never as root. The udev rules grant uaccess on the HID++
# interface of each cabled keyboard and mouse, the vendor interface of the G915's
# and G915 TKL's receivers, the hidraw node of its own that a receiver gives each
# wireless mouse, the G515 LIGHTSPEED TKL and the G915 TKL, the A50 base station's
# and the G432 adapter's HID interfaces, and /dev/uinput, which bindings type
# through and systemd's rules do not grant, and let the gearbench group read the
# G502 X PLUS's event nodes. Some of those nodes carry keystrokes: the G815's
# interface 01, the G515 LIGHTSPEED TKL's node behind its receiver and the G915
# TKL's, on a kernel that gives it one, carry what is typed on the keyboard, and
# the cabled G502 HERO's and G502 X's interface 01 what a button bound to a key
# types; each rule's comment says what its node carries. A program that opened one
# of those keeps reading it after another account gets the seat, and so reads what
# that account types. A mouse's node behind its receiver carries its motion and
# buttons too and, going by the kernel's source, would carry the keystrokes of a
# keyboard paired to that receiver after it, so pair nothing else there. The
# uinput rule is the line steam-devices ships: with it, any program you run can
# create a virtual keyboard (see the rule's comment).
set -euo pipefail

# An exported CDPATH makes cd print the directory it finds, a second line in CHECKOUT.
CHECKOUT="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
RULE_DIR="/usr/local/lib/udev/rules.d"
# Every rule in udev/, as the package installs them: one missing looks like the device
# being unsupported, and /dev/uinput's is among them, without which no binding types.
RULES=("$CHECKOUT"/udev/*.rules)
RULES=("${RULES[@]##*/}")
# XDG_CONFIG_HOME and XDG_DATA_HOME only if absolute, as the XDG spec has it and
# gearbench.config reads the first: a relative one names a folder under wherever this runs.
CONFIG_HOME="$HOME/.config"
if [[ "${XDG_CONFIG_HOME:-}" == /* ]]; then CONFIG_HOME=$XDG_CONFIG_HOME; fi
DATA_HOME="$HOME/.local/share"
if [[ "${XDG_DATA_HOME:-}" == /* ]]; then DATA_HOME=$XDG_DATA_HOME; fi
UNIT_SRC="$CHECKOUT/systemd/gearbench.service"
UNIT_DIR="$CONFIG_HOME/systemd/user"
UNIT_DST="$UNIT_DIR/gearbench.service"
SPATIAL_SRC="$CHECKOUT/spatial/gearbench-spatial.service.in"
SPATIAL_DST="$UNIT_DIR/gearbench-spatial.service"
# Written by an install from before Gearbench Spatial, which had a login unit: removed.
SPATIAL_LOGIN_OLD="$UNIT_DIR/gearbench-spatial-login.service"
# The headsets' outputs: the A50's two, from an ACP profile set and the WirePlumber rule
# giving it that set, and the G432's one, from the rule giving it PipeWire's analog-only.conf.
# The profile set comes before the rules.
ACP_DIR="$CONFIG_HOME/alsa-card-profile/mixer/profile-sets"
WP_DIR="$CONFIG_HOME/wireplumber/wireplumber.conf.d"
AUDIO_SRC=("$CHECKOUT/alsa-card-profile/gearbench-astro-a50.conf"
           "$CHECKOUT/wireplumber/60-gearbench-astro-a50.conf"
           "$CHECKOUT/wireplumber/60-gearbench-logitech-g432.conf")
AUDIO_DST=("$ACP_DIR/gearbench-astro-a50.conf" "$WP_DIR/60-gearbench-astro-a50.conf"
           "$WP_DIR/60-gearbench-logitech-g432.conf")
BIN_DIR="$HOME/.local/bin"
BIN_DST="$BIN_DIR/gearbench-ctl"
APP_DIR="$DATA_HOME/applications"
APP_ID="io.github.gearbench.Gearbench"
APP_DST="$APP_DIR/$APP_ID.desktop"
# The desktop entry is DBusActivatable: without this service file it opens nothing.
DBUS_DIR="$DATA_HOME/dbus-1/services"
DBUS_DST="$DBUS_DIR/$APP_ID.service"
ICON_DIR="$DATA_HOME/icons/hicolor"
# The GNOME Shell extension that tells the daemon the focused window.
EXT_UUID="gearbench-focus@gearbench.github.io"
EXT_DST="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
EXT_FILES=(metadata.json extension.js)
# The app id and the extension's uuid of an install from before 1.1.0, whose files
# old_ids_remove removes.
OLD_APP_ID="io.github.wmuschetto.Gearbench"
OLD_EXT_UUID="gearbench-focus@wmuschetto.github.io"
# The settings folder as gearbench.config reads it.
SETTINGS_DIR="$CONFIG_HOME/gearbench"
# The launcher and the unit run this one, not a pyenv or conda python3 first on PATH.
PYTHON=/usr/bin/python3
# Named in 70-g502x.rules, for the G502 X PLUS's event nodes.
GROUP=gearbench

# shell_settings UUID enable|disable, or ENTRY pin NEW: GNOME Shell's settings,
# through Gio. An enable or a disable takes UUID out of disabled-extensions,
# which wins over enabled-extensions. A pin puts desktop entry NEW in the place
# of ENTRY in the dock's favorite-apps, and leaves a list without ENTRY as it
# is. Status 3 without python3-gi, GNOME Shell's schema or a session bus:
# without a bus dconf drops the write with only a warning. After an enable it
# returns 4 if GNOME's Extensions switch is off and 5 if extensions in the home
# folder are not allowed; the uuid is written in both cases. It returns 6 when
# the machine's settings lock a list it has to change, which then takes no
# write.
shell_settings() {
    /usr/bin/python3 -I - "$@" <<'PY'
import sys

try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError):
    sys.exit(3)
try:
    Gio.bus_get_sync(Gio.BusType.SESSION, None)
except GLib.Error:
    sys.exit(3)
schemas = Gio.SettingsSchemaSource.get_default()
if schemas is None or schemas.lookup("org.gnome.shell", True) is None:
    sys.exit(3)
name, action, *new = sys.argv[1:]
settings = Gio.Settings.new("org.gnome.shell")
if action == "pin":
    pinned = settings.get_strv("favorite-apps")
    if name not in pinned:
        sys.exit(0)
    moved = list(dict.fromkeys(new[0] if entry == name else entry for entry in pinned))
    locked = not settings.set_strv("favorite-apps", moved)
    Gio.Settings.sync()
    sys.exit(6 if locked else 0)
locked = False
for key, wanted in (("enabled-extensions", action == "enable"),
                    ("disabled-extensions", False)):
    current = settings.get_strv(key)
    if (name in current) == wanted:
        continue
    found = [*current, name] if wanted else [other for other in current if other != name]
    # A locked key takes no write, and set_strv returns False.
    locked |= not settings.set_strv(key, found)
Gio.Settings.sync()
if locked:
    sys.exit(6)
# GNOME Shell loads no extension from the home folder while
# allow-extension-installation is off, and runs none the user enabled while the
# Extensions switch is off (disable-user-extensions is true).
if action == "enable":
    if not settings.get_boolean("allow-extension-installation"):
        sys.exit(5)
    if settings.get_boolean("disable-user-extensions"):
        sys.exit(4)
PY
}

# package_installed NAME: its files are on the machine, all or in part: installed (held or
# marked for removal included), or a dpkg run that stopped partway, as a failed postinst
# leaves it; config-files and not-installed are not.
package_installed() {
    case "$(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null)" in
        installed|unpacked|half-configured|half-installed|triggers-awaited|triggers-pending) return 0 ;;
        *) return 1 ;;
    esac
}

# The log-out advice, unless this user's gnome-shell started after the extension's files
# last changed and gives the extension's state as ACTIVE, or as INACTIVE or INITIALIZED,
# which the enable after this starts at once (read from GNOME Shell 50's source, not yet
# seen in a session): GNOME on Wayland loads a new or changed extension only at login.
extension_note() {
    local pid age newest
    pid=$(pgrep -u "$(id -u)" -x gnome-shell | head -n 1) || true
    # LC_ALL=C: gnome-extensions translates the "State" label (German "Status"), not the value.
    if [[ -n "$pid" && "$(LC_ALL=C gnome-extensions info "$EXT_UUID" 2>/dev/null)" =~ State:\ ((IN)?ACTIVE|INITIALIZED) ]] \
        && age=$(ps -o etimes= -p "$pid") && [[ "$age" =~ ^[[:space:]]*[0-9]+$ ]] \
        && newest=$(CDPATH= cd "$EXT_DST" && stat -c %Y "${EXT_FILES[@]}" | sort -n | tail -n 1) \
        && (( newest < EPOCHSECONDS - age )); then
        return 0
    fi
    echo "Log out and back in: GNOME on Wayland loads the focus extension only at the"
    echo "next login, and until then game profiles follow only X11 windows, a Proton game's"
    echo "among them unless it runs Wine's Wayland driver."
}

# Per user: as root it would install for root. It calls sudo itself for the rules and
# the group.
refuse_root() {
    [[ "${1:-}" == -h || "${1:-}" == --help ]] && return 0
    if [[ "$(id -u)" == 0 ]]; then
        echo "run ./install.sh as yourself, not with sudo: it asks for your password when it needs root" >&2
        exit 1
    fi
}

# Beside the package, the checkout's copy silently wins: ~/.config/systemd/user shadows
# the packaged unit and ~/.local/bin usually comes first on PATH.
refuse_packages() {
    if package_installed gearbench; then
        echo "the gearbench package is installed, and this script would install a second copy beside it." >&2
        echo "run 'sudo apt remove gearbench' first, or use the gearbench package instead." >&2
        exit 1
    fi
}

# others_can_write: the first of the checkout and its folders up to /, then of what the
# checkout holds, that another account owns or may write: one owned by neither root nor
# this account, one with o+w, and one with g+w or the setgid bit whose group is not the
# account's own, taken to be a group of its own as Debian and Ubuntu make one per account.
# What is made in a setgid folder takes its group, and under umask 002 or a default ACL
# that group's write. Where the primary group is shared, as `users` can be, g+w for it
# passes. Then the first of them whose ACL lets another account write (acl_writer). Links
# are left out: theirs is 0777.
others_can_write() {
    local uid gid dir=$CHECKOUT folders=("$CHECKOUT") others unsearchable found
    uid=$(id -u) gid=$(id -g)
    while [[ "$dir" != / ]]; do
        dir=$(dirname -- "$dir")
        folders+=("$dir")
    done
    others=(! -type l \( ! -uid 0 ! -uid "$uid" -o -perm -0002
                         -o \( -perm -0020 -o -perm -2000 \) ! -gid "$gid" \) -print -quit)
    # The service cannot reach what is in a folder it may not search, and find cannot stat
    # what it lists in one it may read but not search: a failed stat passes ! -uid. So find
    # checks such a folder and stays out of it (GNU find's , operator).
    unsearchable=(\( -type d ! -executable -prune \) ,)
    found=$(find "${folders[@]}" -maxdepth 0 "${others[@]}")
    if [[ -z "$found" ]]; then
        found=$(find "$CHECKOUT" "${unsearchable[@]}" "${others[@]}" 2>/dev/null) || true
    fi
    if [[ -z "$found" ]]; then
        # An ACL's entries for other accounts show in the mode only as its mask, in the
        # group bits, which pass above for the account's own group. Root may write whatever
        # its entry says, as it may a path it owns. Bash clears set -e in the caller's
        # command substitution, so a failure returns, or the check would pass.
        found=$({ find "${folders[@]}" -maxdepth 0 ! -type l -print0
                  find "$CHECKOUT" "${unsearchable[@]}" ! -type l -print0 2>/dev/null || true
                } | acl_writer "$gid" 0 "$uid") || return
    fi
    printf '%s' "$found"
}

# acl_writer GID UID...: the first of the NUL-separated paths on stdin whose ACL, or the
# default ACL a folder gives what is made in it, lets a user other than the UIDs, or a
# group other than GID, write; status 1, with a message, for an ACL it cannot read. The
# script takes stdin, so Python reads the paths from fd 3.
acl_writer() {
    /usr/bin/python3 -I - "$@" 3<&0 <<'PY'
import errno
import os
import struct
import sys

# <linux/posix_acl.h> and <linux/posix_acl_xattr.h>: a 4-byte version, then entries of a
# little-endian u16 tag, u16 permissions and u32 id. A named user or group writes only
# where the ACL's mask has write too. Others' entry has no mask: a default ACL's gives
# every account write on what is made in the folder, whatever the umask.
ACL_USER, ACL_GROUP, ACL_MASK, ACL_OTHER, WRITE = 0x02, 0x08, 0x10, 0x20, 0x02
group, users = int(sys.argv[1]), {int(user) for user in sys.argv[2:]}


def lets_others_write(path: bytes) -> bool:
    for name in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            acl = os.getxattr(path, name, follow_symlinks=False)
        except OSError as error:
            # No ACL, or a filesystem without them.
            if error.errno in (errno.ENODATA, errno.EOPNOTSUPP):
                continue
            sys.exit(f"cannot read the ACL of {os.fsdecode(path)}: {error.strerror}. "
                     "Check that it is still there, and run this again.")
        entries = list(struct.iter_unpack("<HHI", acl[4:]))
        if any(tag == ACL_OTHER and allowed & WRITE for tag, allowed, _who in entries):
            return True
        if any(tag == ACL_MASK and allowed & WRITE for tag, allowed, _who in entries) and any(
                allowed & WRITE and (tag == ACL_USER and who not in users
                                     or tag == ACL_GROUP and who != group)
                for tag, allowed, who in entries):
            return True
    return False


with open(3, "rb") as listed:
    paths = listed.read().split(b"\0")[:-1]
sys.stdout.buffer.write(next((path for path in paths if lets_others_write(path)), b""))
PY
}

# Run before the rules go in: udev looks the group up when it reads them and drops a line
# naming a group that is not there; it applies one that is not a system group, with a
# warning. `id -un`, since USER can be unset.
group_install() {
    getent group "$GROUP" >/dev/null || sudo groupadd --system "$GROUP"
    sudo usermod -aG "$GROUP" "$(id -un)"
}

# The daemon has its user manager's groups, as this shell does. The manager outlives a
# logout while another session is open, for logind's UserStopDelaySec (10 s) after the
# last, and with lingering on until a restart.
group_note() {
    id -nG | tr ' ' '\n' | grep -qx "$GROUP" && return 0
    echo "Your account is in the $GROUP group, which the service needs with host mode off to"
    if [[ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null)" == yes ]]; then
        echo "read G4 to G9 on a G502 X PLUS. It gets the group when you restart the machine:"
        echo "lingering is on, so logging out leaves the service's groups as they are."
    else
        echo "read G4 to G9 on a G502 X PLUS. It gets the group once you log out of every"
        echo "session and back in."
    fi
}

icons_install() {
    local dir
    for dir in "$CHECKOUT"/desktop/icons/*/; do
        dir="${dir%/}"
        mkdir -p "$ICON_DIR/${dir##*/}/apps"
        install -m 0644 "$dir"/*.svg "$ICON_DIR/${dir##*/}/apps/"
    done
}

# Rebuilds only an existing cache: GTK checks a cache against hicolor/'s mtime alone, so a
# new one would hide icons others add to a size folder later. The touch marks a cache that
# cannot be rebuilt out of date, so GTK ignores it; -c, or a missing hicolor/ becomes a file.
icon_cache_update() {
    touch -c "$ICON_DIR" 2>/dev/null || true
    [[ -f "$ICON_DIR/icon-theme.cache" ]] \
        && gtk-update-icon-cache -qtf "$ICON_DIR" 2>/dev/null || true
}

# ids_remove APP_ID EXT_UUID PACKAGED [WORDS]: the desktop entry, D-Bus service file and
# icons under APP_ID and, when its folder is there, the extension under EXT_UUID, with a
# line that says so (WORDS end it) and the uuid out of GNOME's lists, which stops it in the
# running Shell. PACKAGED is 1 beside the package, whose extension has EXT_UUID (the old
# one in a version from before 1.1.0) and stays enabled.
ids_remove() {
    local ext="$DATA_HOME/gnome-shell/extensions/$2" file
    rm -f "$APP_DIR/$1.desktop" "$DBUS_DIR/$1.service"
    # -H: hicolor/ itself may be a link, which find would not enter.
    find -H "$ICON_DIR" \( -name "$1.svg" -o -name "$1-symbolic.svg" \) -delete 2>/dev/null || true
    [[ -d "$ext" ]] || return 0
    echo "removing Gearbench's GNOME Shell extension${4:+ $4}"
    (( $3 )) || shell_settings "$2" disable || true
    for file in "${EXT_FILES[@]}"; do rm -f "$ext/$file"; done
    rmdir "$ext" 2>/dev/null || true
}

# old_ids_remove PACKAGED: what an install from before 1.1.0 put under the old ids.
old_ids_remove() {
    ids_remove "$OLD_APP_ID" "$OLD_EXT_UUID" "$1" "under its name before 1.1.0, $OLD_EXT_UUID"
}

# The profile set before the rules: the A50's rule falls back to default.conf (no Game
# output) without it. WirePlumber reads them only at start, and a restart stops all audio
# for a moment, so it restarts only when a file changed.
audio_install() {
    local changed=0 i
    mkdir -p "$ACP_DIR" "$WP_DIR"
    for i in "${!AUDIO_SRC[@]}"; do
        if ! cmp -s "${AUDIO_SRC[i]}" "${AUDIO_DST[i]}"; then
            install -m 0644 "${AUDIO_SRC[i]}" "${AUDIO_DST[i]}"
            changed=1
        fi
    done
    (( changed )) || return 0
    echo "restarting WirePlumber for the headsets' outputs: audio stops for a moment"
    systemctl --user restart wireplumber.service ||
        echo "WirePlumber did not restart; the headsets' outputs apply when it next starts: systemctl --user restart wireplumber"
}

# The reverse order: the rules, then the profile set. $1 is 1 beside the gearbench package.
audio_uninstall() {
    local packaged=$1 had=0 i
    for (( i = ${#AUDIO_DST[@]} - 1; i >= 0; i-- )); do
        [[ -e "${AUDIO_DST[i]}" ]] && had=1
        rm -f "${AUDIO_DST[i]}"
    done
    (( had )) || return 0
    if (( packaged )); then
        echo "restarting WirePlumber for the gearbench package's headset outputs: audio stops for a moment"
    else
        echo "restarting WirePlumber without Gearbench's headset outputs: audio stops for a moment"
    fi
    systemctl --user restart wireplumber.service 2>/dev/null ||
        echo "WirePlumber did not restart: run systemctl --user restart wireplumber in your desktop session"
}

# Written, never enabled: the Headset tab or `headset-spatial on` enables it.
# SPATIAL_CHANGED: whether a running spatial unit needs a restart.
spatial_unit_install() {
    sed "s|@CHECKOUT@|$CHECKOUT|g" "$SPATIAL_SRC" > "$SPATIAL_DST.new"
    if cmp -s "$SPATIAL_DST.new" "$SPATIAL_DST"; then
        rm -f "$SPATIAL_DST.new"
        SPATIAL_CHANGED=0
    else
        mv -f "$SPATIAL_DST.new" "$SPATIAL_DST"
        SPATIAL_CHANGED=1
    fi
}

# After the reload. Before Gearbench Spatial the unit was the A50's, enabled by its base
# station's device unit and by a login unit, which is gone: its link goes with it, and a
# unit that was on is enabled again by its own [Install] (reenable: systemctl's disable
# removes every link named after the unit). One that was off stays off.
# SPATIAL_MIGRATED: whether one was enabled again, which the install then starts: stopped
# while its base station was away, nothing else starts it before the next login.
spatial_unit_migrate() {
    SPATIAL_MIGRATED=0
    [[ -e "$SPATIAL_LOGIN_OLD" ]] || return 0
    systemctl --user disable gearbench-spatial-login.service 2>/dev/null || true
    rm -f "$SPATIAL_LOGIN_OLD"
    if systemctl --user -q is-enabled gearbench-spatial.service 2>/dev/null \
        && systemctl --user reenable gearbench-spatial.service; then
        SPATIAL_MIGRATED=1
    fi
}

# A usb trigger does not re-run hidraw rules, which leaves the node root-owned; input is
# for the G502 X PLUS's event nodes (G4 to G9), misc for /dev/uinput. A uinput module not
# loaded yet has no device to trigger: static_node tags the node at the next boot.
udev_reload() {
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw --action=add
    sudo udevadm trigger --subsystem-match=input --action=add
    sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=add
}

uninstall() {
    local had_unit=0 stopped=1 packaged=0
    [[ -f "$UNIT_DST" ]] && had_unit=1
    package_installed gearbench && packaged=1
    # This checkout's spatial unit shadows the package's of the same name, and its enable
    # links point at it, so they go with it; an older install's unit file carries Also=,
    # which disables its login unit too. Beside the package with no unit of this
    # checkout's, the links are the package's: left alone.
    if [[ -e "$SPATIAL_DST" ]] || (( ! packaged )); then
        echo "switching spatial sound off"
        systemctl --user disable --now gearbench-spatial.service 2>/dev/null || true
    fi
    rm -f "$SPATIAL_DST" "$SPATIAL_LOGIN_OLD"
    audio_uninstall "$packaged"
    # Beside the package, no --now: that would stop the package's daemon. The disable
    # only removes the links to this checkout's unit, which would dangle once it goes.
    if (( packaged )); then
        echo "the gearbench package is installed; removing this checkout's files and leaving its daemon"
        if (( had_unit )) && ! systemctl --user disable gearbench.service 2>/dev/null; then
            stopped=0
        fi
    else
        echo "stopping and disabling the user unit"
        # With no user manager reached, the one in the desktop session keeps running it.
        if ! systemctl --user disable --now gearbench.service 2>/dev/null && (( had_unit )); then
            stopped=0
        fi
    fi
    rm -f "$UNIT_DST" "$BIN_DST"
    ids_remove "$APP_ID" "$EXT_UUID" "$packaged"
    old_ids_remove "$packaged"
    icon_cache_update
    # A shell may reach no user manager; failing here would leave the rules behind.
    systemctl --user daemon-reload 2>/dev/null || true
    update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "removing the udev rules (needs root)"
    for rule in "${RULES[@]}"; do sudo rm -f "$RULE_DIR/$rule"; done
    udev_reload
    # Other accounts may be in it, and the package's rule names it too.
    if getent group "$GROUP" >/dev/null; then
        echo "left in place: the $GROUP group; once nothing uses it, sudo groupdel $GROUP removes it"
    fi
    # Never removed: the settings hold the mouse's flash backups.
    if [[ -e "$SETTINGS_DIR" ]]; then
        echo "left in place: $SETTINGS_DIR"
    fi
    if (( packaged )); then
        if (( ! had_unit )); then
            echo "done. The packaged daemon was not this checkout's and is as it was."
        elif systemctl --user restart gearbench.service; then
            # The checkout's unit shadowed the package's.
            echo "done. The packaged daemon is running."
        elif (( ! stopped )); then
            # The desktop session's manager keeps this checkout's unit loaded until a reload.
            echo "done, but this shell reaches no user manager, so the packaged daemon was not" >&2
            echo "started in place of this checkout's: in your desktop session, run systemctl --user" >&2
            echo "daemon-reload, then systemctl --user restart gearbench.service." >&2
        else
            echo "done, but the packaged daemon did not start: systemctl --user status gearbench" >&2
        fi
    else
        if (( stopped )); then
            echo "done. The keyboard goes back to its onboard profile."
        else
            echo "done, but this shell reaches no user manager, so a daemon still running was not" >&2
            echo "stopped: run systemctl --user stop gearbench.service in your desktop session, and" >&2
            echo "systemctl --user stop gearbench-spatial.service too if spatial sound was on." >&2
        fi
        # The triggers above leave the ACLs already granted on the nodes.
        echo "Your account keeps its access to the devices plugged in now until they are replugged,"
        echo "and to /dev/uinput until the machine restarts. The G502 X PLUS's event nodes go back"
        echo "to root:input at once."
    fi
    exit 0
}

python_check() {
    [[ -x "$PYTHON" ]] || { echo "$PYTHON not found" >&2; exit 1; }
}

checkout_path_check() {
    # The checkout's path goes into the units and the launcher. A space is fine except at
    # the end, where systemd trims it off WorkingDirectory=; a newline breaks them all, and
    # spatial.render refuses any control character in the chain's conf, which names the
    # checkout's spatial/ folder.
    case "$CHECKOUT" in
        *[\"\$\`\\%\&\|:]*|*[[:cntrl:]]*|*[[:space:]])
            echo "the checkout's path, $CHECKOUT, has a character the units, the launcher or the" >&2
            echo "spatial sound conf cannot hold (\" \$ \` \\ % & | :, a control character such as" >&2
            echo "a tab or a newline, or a space at the end);" >&2
            echo "move it and run this again." >&2
            exit 1 ;;
    esac
    # systemd will not load a unit with a non-UTF-8 path.
    if ! iconv -f UTF-8 -t UTF-8 <<< "$CHECKOUT" >/dev/null 2>&1; then
        echo "the checkout's path, $CHECKOUT, is not UTF-8, which the unit cannot hold;" >&2
        echo "move it and run this again." >&2
        exit 1
    fi
}

home_check() {
    case "$HOME" in
        *$'\n'*) echo "HOME has a newline in it, which a desktop entry cannot hold." >&2; exit 1 ;;
    esac
}

writers_check() {
    # The units and the launcher run the checkout's code at every start: under a folder others
    # can write, as /tmp, another account could put its own code at the path once it is gone.
    local writable
    writable=$(others_can_write)
    if [[ -n "$writable" ]]; then
        echo "another account owns or can write $writable, or would share what is made in it," >&2
        echo "so it could put its own code where Gearbench runs it as you." >&2
        # A move keeps the owner, mode and ACL of the checkout and of what it holds.
        if [[ "$writable" == "$CHECKOUT" || "$writable" == "$CHECKOUT"/* ]]; then
            echo "Make the checkout and all it holds yours and writable by you alone" >&2
            echo "(sudo chown -R, chmod -R go-w,g-s, setfacl -R -b), or clone it again into your" >&2
            echo "home folder, and run this again." >&2
        else
            echo "Move the checkout into a folder only you can write, such as your home" >&2
            echo "folder, and run this again." >&2
        fi
        exit 1
    fi
}

# Sets HAVE_GUI: 1 where GTK 4 and libadwaita for Python import, which the window needs.
gui_check() {
    HAVE_GUI=1
    if ! "$PYTHON" -I - 2>/dev/null <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
PY
    then
        HAVE_GUI=0
        echo "GTK 4 and libadwaita for Python not found; installing the daemon and the CLI" >&2
        echo "without the window. For the window: sudo apt install python3-gi gir1.2-gtk-4.0" >&2
        echo "gir1.2-adw-1, then run ./install.sh again." >&2
    fi
}

# What is checked before the install writes anything: its first refusals, and whether
# the window can be installed. Later steps refuse too (units_install).
preflight() {
    python_check
    checkout_path_check
    home_check
    writers_check
    refuse_packages
    gui_check
}

rules_install() {
    local rule
    echo "installing the $GROUP group and the udev rules (needs root)"
    group_install
    # /usr/local/lib/udev may not exist. mkdir, not install -d, which would chmod a shared
    # directory already there.
    sudo mkdir -p "$RULE_DIR"
    for rule in "${RULES[@]}"; do
        sudo install -m 0644 -o root -g root "$CHECKOUT/udev/$rule" "$RULE_DIR/$rule"
    done
    udev_reload
    udevadm settle
}

units_install() {
    local loaded
    echo "installing the user unit"
    # Asked before a unit is written: a spatial unit file a refused run wrote reads as
    # unchanged to the next run, which then restarts none. show with no unit reads the
    # manager's own properties (systemctl(1)). With no user manager reached, systemctl's
    # own hint names an option this script does not take.
    if ! systemctl --user show --property=Version --value >/dev/null; then
        echo "this shell reaches no user manager, which runs the service; run ./install.sh" >&2
        echo "again from a terminal in your desktop session." >&2
        exit 1
    fi
    mkdir -p "$UNIT_DIR"
    # Renamed over the unit: a write that fails partway leaves the installed one whole.
    sed "s|@CHECKOUT@|$CHECKOUT|g" "$UNIT_SRC" > "$UNIT_DST.new"
    mv -f "$UNIT_DST.new" "$UNIT_DST"
    spatial_unit_install
    systemctl --user daemon-reload
    spatial_unit_migrate
    # The user manager reads units from its own XDG_CONFIG_HOME, not this shell's: one set in
    # environment.d reaches the shell and not the manager. A unit written anywhere else is
    # never loaded, and the enable below would stop the install halfway with no reason given.
    # FragmentPath is empty for a unit the manager cannot find; a failed query counts as one.
    loaded=$(systemctl --user show --property=FragmentPath --value gearbench.service) || loaded=
    if [[ ! "$loaded" -ef "$UNIT_DST" ]]; then
        echo "systemd's user manager does not read $UNIT_DST${loaded:+; it loads $loaded}." >&2
        echo "XDG_CONFIG_HOME here is not the manager's: set the same absolute path for the" >&2
        echo "manager with ManagerEnvironment= in ~/.config/systemd/user.conf, or leave it" >&2
        echo "unset, then run ./install.sh again." >&2
        exit 1
    fi
    # A daemon that failed at every start is left failed, and its start limit refuses a manual
    # start too until StartLimitIntervalSec= has passed (systemd.unit(5)). Unchecked: systemd
    # refuses it for a unit it has not loaded, which has nothing to clear.
    systemctl --user reset-failed gearbench.service 2>/dev/null || true
    systemctl --user enable gearbench.service
    # Started, or restarted on the new code (the owner's decision of 2026-10-02). A daemon
    # started by hand with gearbench-ctl daemon runs outside the unit and is not restarted:
    # until it stops, a window talks to it, and the unit's daemon finds the lock taken and
    # exits.
    systemctl --user restart gearbench.service
}

spatial_restart() {
    # After audio_install, so the spatial unit links to its headset's output from the new files.
    if (( SPATIAL_MIGRATED )); then
        systemctl --user restart gearbench-spatial.service || true
    elif (( SPATIAL_CHANGED )); then
        systemctl --user try-restart gearbench-spatial.service || true
    fi
}

launcher_install() {
    echo "installing the launcher"
    mkdir -p "$BIN_DIR"
    # Renamed over the launcher: a write that fails partway leaves the installed one whole.
    sed "s|@CHECKOUT@|$CHECKOUT|g" "$CHECKOUT/desktop/gearbench-ctl" > "$BIN_DST.new"
    chmod 0755 "$BIN_DST.new"
    mv -f "$BIN_DST.new" "$BIN_DST"
}

old_ids_migrate() {
    # Before the icon cache update and the extension's enable; refuse_packages leaves no
    # package beside it.
    old_ids_remove 0
    # The dock's pin under the old app id, in its place under the new one.
    local pinned=0
    shell_settings "$OLD_APP_ID.desktop" pin "$APP_ID.desktop" || pinned=$?
    if (( pinned == 6 )); then
        echo "this machine's GNOME settings lock the dock's favorites, so Gearbench's pin stays"
        echo "under its app id before 1.1.0, which shows nothing"
    fi
}

window_install() {
    local shown
    # A % in the launcher's path cannot go in Exec: the spec writes it %%, and GLib looks the
    # program up before expanding that and drops the entry (checked with GLib 2.88).
    if [[ "$HAVE_GUI" == 1 && "$BIN_DST" == *%* ]]; then
        printf -v shown '%q' "$BIN_DST"
        echo "$BIN_DST has a % in it, which a desktop entry cannot run; the window is" >&2
        echo "installed without one: run it with $shown gui" >&2
        HAVE_GUI=0
    fi
    if [[ "$HAVE_GUI" == 1 ]]; then
        echo "installing the desktop entry and its D-Bus service file"
        mkdir -p "$APP_DIR"
        # The launcher by full path: GLib drops an entry whose Exec is not on the session's
        # PATH, and ~/.local/bin often is not (the package's /usr/bin always is). Quoted per
        # the desktop entry spec, for a space in HOME; Python, so no path is read as sed syntax.
        "$PYTHON" -I - "$CHECKOUT/desktop/$APP_ID.desktop" "$APP_DST" "$BIN_DST" <<'PY'
import re
import sys
from pathlib import Path

source, target, launcher = sys.argv[1:]


def quoted(arg: str) -> str:
    """One Exec argument in double quotes, with ", `, $ and backslash escaped, then
    every backslash written twice: the file's own string escapes apply first."""
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg).replace("\\", "\\\\") + '"'


text, count = re.subn(r"^Exec=gearbench-ctl ", lambda _m: f"Exec={quoted(launcher)} ",
                      Path(source).read_text(), flags=re.M)
if count != 1:
    sys.exit(f"{source}: expected one 'Exec=gearbench-ctl ' line, found {count}")
Path(target).write_text(text)
PY
        chmod 0644 "$APP_DST"
        mkdir -p "$DBUS_DIR"
        # D-Bus unescapes the file's strings, then splits Exec as a shell would: the launcher
        # is quoted as one word and each backslash written twice.
        "$PYTHON" -I - "$CHECKOUT/desktop/$APP_ID.service" "$DBUS_DST" "$BIN_DST" <<'PY'
import re
import shlex
import sys
from pathlib import Path

source, target, launcher = sys.argv[1:]
text, count = re.subn(r"^Exec=/usr/bin/gearbench-ctl ",
                      lambda _m: "Exec=" + shlex.quote(launcher).replace("\\", "\\\\") + " ",
                      Path(source).read_text(), flags=re.M)
if count != 1:
    sys.exit(f"{source}: expected one 'Exec=/usr/bin/gearbench-ctl ' line, found {count}")
Path(target).write_text(text)
PY
        chmod 0644 "$DBUS_DST"
        update-desktop-database "$APP_DIR" 2>/dev/null || true
    elif [[ -e "$APP_DST" || -e "$DBUS_DST" ]]; then
        # An earlier run's, from when the window could start: the app grid would list it.
        rm -f "$APP_DST" "$DBUS_DST"
        update-desktop-database "$APP_DIR" 2>/dev/null || true
    fi
}

# Sets EXT_NOTE, what the closing lines say of the extension.
extension_install() {
    local file
    echo "installing Gearbench's GNOME Shell extension"
    mkdir -p "$EXT_DST"
    for file in "${EXT_FILES[@]}"; do
        # -C keeps an unchanged file's mtime, which extension_note reads.
        install -C -m 0644 "$CHECKOUT/gnome-shell/$EXT_UUID/$file" "$EXT_DST/$file"
    done
    EXT_NOTE=$(extension_note)
    local ext_status=0
    shell_settings "$EXT_UUID" enable || ext_status=$?
    case "$ext_status" in
        0) ;;
        4) EXT_NOTE="The focus extension is enabled, but GNOME's Extensions switch is off, so it
does not run. Turn the switch on in the Extensions app, or with the command
below, then log out and back in. That starts your other extensions too.
gsettings set org.gnome.shell disable-user-extensions false" ;;
        5) EXT_NOTE="The focus extension is installed, but this machine's GNOME settings allow no
extensions from your home folder (org.gnome.shell allow-extension-installation),
so it does not run." ;;
        6) EXT_NOTE="The focus extension is installed, but this machine's GNOME settings lock
which extensions run (org.gnome.shell enabled-extensions and disabled-extensions),
so it is not enabled and does not run." ;;
        *) EXT_NOTE="The focus extension is installed but not enabled (no python3-gi, GNOME
Shell settings or session bus). After your next login: gnome-extensions enable $EXT_UUID" ;;
    esac
}

closing_notes() {
    COMMAND=gearbench-ctl
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) printf -v COMMAND '%q' "$BIN_DST"
           echo "note: $BIN_DIR is not on your PATH, so in a terminal the command is $COMMAND" ;;
    esac

    echo
    systemctl --user --no-pager status gearbench.service | head -12 || true
    echo
    echo "installed. Try:  $COMMAND status"
    if [[ -n "$EXT_NOTE" ]]; then
        echo "$EXT_NOTE"
    fi
    group_note
    if [[ "$HAVE_GUI" == 1 ]]; then
        echo "The window is in the app grid as \"Gearbench\", or run: $COMMAND gui"
    fi
    if ! systemctl --user -q is-enabled gearbench-spatial.service 2>/dev/null; then
        if [[ "$HAVE_GUI" == 1 ]]; then
            echo "Spatial sound is off; switch it on in the Headset tab, or: $COMMAND headset-spatial on"
        else
            echo "Spatial sound is off; switch it on with: $COMMAND headset-spatial on"
        fi
    fi
}

usage() {
    sed -n '3,12{s/^# \{0,1\}//;p;}' "${BASH_SOURCE[0]}"
}

# Refuse an unknown option or a second one: a mistyped --uninstall would run a full
# install, and `--uninstall --dry-run` a real uninstall.
if (( $# > 1 )); then
    echo "one option at most" >&2; usage >&2; exit 2
fi
if (( $# == 1 )) && [[ -z "$1" ]]; then
    echo "unknown option: an empty argument" >&2; usage >&2; exit 2
fi
refuse_root "$@"
case "${1:-}" in
    "") ;;
    --uninstall) uninstall ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
esac

preflight
rules_install
units_install
audio_install
spatial_restart
launcher_install
old_ids_migrate
# Even without the window: the tray icon and notices use them.
echo "installing the icons"
icons_install
icon_cache_update
window_install
extension_install
closing_notes
