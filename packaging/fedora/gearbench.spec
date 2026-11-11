# The .rpm for Fedora. It holds what the .deb holds: packaging/install-files
# installs everything beside the Python package from the .deb's own lists, and
# the scriptlets run, in rpm's order, the steps of the .deb's postinst, prerm
# and postrm that an .rpm needs: rpm makes the group itself, and no .rpm of
# 1.2.1 or earlier exists to clean up after. Build it from the
# release's source archive:
#   rpmbuild -bb packaging/fedora/gearbench.spec
# with gearbench-VERSION.tar.gz in rpmbuild's SOURCES folder.

# The console script, which the unit, the desktop entry and D-Bus start, runs
# with -I: no PYTHON* variable and no user site-packages from the session,
# where an empty PYTHONPATH element names the working directory, $HOME.
%global py3_shebang_flags I

Name:           gearbench
Version:        1.3.1
Release:        1%{?dist}
Summary:        Lighting, G keys, DPI and macros for Logitech G keyboards, mice and headsets
License:        GPL-3.0-or-later AND CC0-1.0 AND Apache-2.0 AND (LGPL-3.0-only OR CC-BY-SA-3.0)
URL:            https://github.com/gearbench/gearbench
Source0:        %{url}/archive/v%{version}/gearbench-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools >= 77
BuildRequires:  systemd-rpm-macros
BuildRequires:  make

# systemctl, systemd-hwdb and udevadm, which the scriptlets run.
Requires:       systemd
Requires:       systemd-udev
Requires(post): systemd
Requires(post): systemd-udev
Requires(preun): systemd
Requires(postun): systemd-udev
# The window needs GTK 4 and libadwaita through GObject introspection, and
# `gearbench-ctl gui` says what is missing and exits 1 without them. Fedora's
# GTK 4 typelib needs gobject-introspection's cairo one, which nothing else
# pulls in. The daemon runs without them, its desktop session and login screen
# check off, so these are weak: dnf installs them by default.
Recommends:     python3-gobject
Recommends:     gtk4
Recommends:     libadwaita
Recommends:     gobject-introspection
# pipewire-utils holds the PipeWire programs Gearbench runs for the headsets'
# sound, spatial sound and Outputs: pw-dump, pw-cli, pw-metadata, pw-record and
# pw-play. It requires pipewire, the package with the pipewire program. Without
# them each of those says the program is not installed, and the keyboards and
# mice work as before.
Recommends:     pipewire-utils

%description
Gearbench drives Logitech G keyboards and mice over their HID++ interfaces,
on a cable or through a LIGHTSPEED receiver, and Logitech G and Astro
headsets: per-key lighting, G keys and the G-Shift layer, macros, DPI stages,
game profiles, equalizers and spatial sound. A user systemd service owns the
devices; the command line and the GTK 4 window talk to it over a unix socket.
Nothing runs as root. Its udev rules grant the user of the active seat each
supported device and /dev/uinput, and on some models a node that carries
what is typed: the guide in /usr/share/doc/gearbench, "The udev rules",
names each one.

%prep
%autosetup -n gearbench-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files gearbench
packaging/install-files %{buildroot}

# Not pyproject_check_import, which runs Python with py3_shebang_flags: -I
# ignores the PYTHONPATH that points it at the build's files. -P keeps the
# working directory, the source tree, off the path, so the import is the
# build's. A spec expands a macro in a comment too, so these name none.
%check
%{py3_test_envvars} %{python3} -P -c 'import gearbench.cli, gearbench.daemon'

# $1 is 1 on a first install and 2 or more on an upgrade. rpm makes the
# gearbench group from the package's sysusers.d file before it installs a file,
# so the udev rule that names the group finds it. A remove keeps the group, as
# install.sh's --uninstall does, so this script cannot tell a group made now
# from one an earlier install left: it gives the .deb's usermod note only while
# the group has no member. The .deb's postinst holds the rest of these steps'
# reasons.
%post
hwdb_update() {
    command -v systemd-hwdb >/dev/null 2>&1 || return 0
    if [ -e /etc/udev/hwdb.bin ]; then
        systemd-hwdb update || true
    else
        systemd-hwdb --usr update || true
    fi
}
hwdb_update
if [ -d /run/udev ]; then
    udevadm control --reload || true
    udevadm trigger --subsystem-match=hidraw --action=add || true
    udevadm trigger --subsystem-match=input --action=add || true
    udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=add || true
fi
/usr/lib/gearbench/checkout-rules /usr/local/lib/udev/rules.d
if [ "$1" -eq 1 ]; then
    systemctl --global enable gearbench.service || true
    case "$(getent group gearbench)" in
        *:)
            echo "gearbench: with host mode off, the G502 X PLUS's G4 to G9 need your account" \
                "in the gearbench group: run sudo usermod -aG gearbench <you>, then log out of" \
                "every session and back in, or, with lingering on, restart the machine" >&2
            ;;
    esac
fi
if [ -d /run/systemd/system ]; then
    if [ "$1" -eq 1 ]; then
        /usr/lib/gearbench/user-daemons start
    else
        /usr/lib/gearbench/user-daemons restart
    fi
fi

# $1 is 0 on a remove and 1 or more on an upgrade, which leaves the daemons to
# the new version's restart.
%preun
if [ "$1" -eq 0 ]; then
    if [ -d /run/systemd/system ]; then
        /usr/lib/gearbench/user-daemons stop
    fi
    systemctl --global disable gearbench.service || true
fi

# After a remove's files have gone, the hwdb rebuilt without the mice's entry
# and the udev database refreshed, as the .deb's postrm does.
%postun
hwdb_update() {
    command -v systemd-hwdb >/dev/null 2>&1 || return 0
    if [ -e /etc/udev/hwdb.bin ]; then
        systemd-hwdb update || true
    else
        systemd-hwdb --usr update || true
    fi
}
if [ "$1" -eq 0 ]; then
    hwdb_update
    if [ -d /run/udev ]; then
        udevadm control --reload || true
        udevadm trigger --subsystem-match=hidraw --action=add || true
        udevadm trigger --subsystem-match=input --action=add || true
        udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=add || true
    fi
fi

%files -f %{pyproject_files}
%dir %{_datadir}/licenses/gearbench
%license %{_datadir}/licenses/gearbench/LICENSE
%doc %{_docdir}/gearbench/
%{_bindir}/gearbench-ctl
%{_prefix}/lib/gearbench/
%{_userunitdir}/gearbench.service
%{_userunitdir}/gearbench-spatial.service
%{_userunitdir}/gearbench-outputs.service
%{_udevrulesdir}/70-*.rules
%{_udevhwdbdir}/71-gearbench-mouse-dpi.hwdb
%{_sysusersdir}/gearbench.conf
%{_datadir}/applications/io.github.gearbench.Gearbench.desktop
%{_datadir}/dbus-1/services/io.github.gearbench.Gearbench.service
%{_metainfodir}/io.github.gearbench.Gearbench.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/io.github.gearbench.Gearbench*.svg
%{_datadir}/gnome-shell/extensions/gearbench-focus@gearbench.github.io/
%{_datadir}/alsa-card-profile/mixer/profile-sets/gearbench-*.conf
%{_datadir}/wireplumber/wireplumber.conf.d/60-gearbench-*.conf
%{_datadir}/gearbench/
%{_mandir}/man1/gearbench-ctl.1*
