"""The About dialog, and the debug information behind its Troubleshooting page.

The debug information is the version, the daemon's `status` reply and its problems, whether
this user may open each device's hidraw node and /dev/uinput, what systemd and PipeWire say
of spatial sound, and the daemon's last 30 journal lines, with the home folder written ~.
`gather` reads it on the worker thread, since a socket round trip or a process can take
seconds; the dialog opens at once and takes the text when it lands, which libadwaita shows
even on an open page. A source that fails says so in its own section.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from gi.repository import Adw, Gtk

from .. import __version__, pipewire_client, spatial
from ..a50x import PRODUCTS as CENTURION_MODELS, find_each_a50x
from ..astro import PRODUCTS as HEADSET_MODELS, VENDOR_ID as HEADSET_VENDOR, find_base
from ..daemon import send_command
from ..desktop import APP_NAME, APP_SUMMARY, DESKTOP_ENTRY
from ..g432 import PRODUCTS as G432_MODELS, find_g432
from ..hidpp import NO_RULE, VENDOR_ID as LOGITECH_VENDOR
from ..keyboard import MODELS as KEYBOARD_MODELS, attached_keyboards
from ..mouse import MODELS as MOUSE_MODELS, find_attached_mice
from ..uinput import UINPUT as UINPUT_NODE
from ..wording import SERVICE, ended, escaped, home_shortened, reason

#: The journal section's command: the daemon's last lines and the user manager's about it.
#: The sender is matched by fields journald sets; USER_UNIT, which any program of this user can
#: set (a Flatpak with no permissions included), counts only on init.scope's lines, unlike -u.
_UID = f"_UID={os.getuid()}"
JOURNAL = ["journalctl", "--user", f"_SYSTEMD_USER_UNIT={SERVICE}.service", _UID, "+",
           "_SYSTEMD_USER_UNIT=init.scope", f"USER_UNIT={SERVICE}.service", _UID,
           "-n", "30", "--no-pager", "-o", "cat"]
#: How long journalctl gets. It answers in well under a second.
JOURNAL_TIMEOUT = 10.0
#: Where the virtual keyboard and mouse are made.
UINPUT = Path(UINPUT_NODE)
#: What the dialog shows until `gather` lands, on libadwaita's Debugging Information page.
GATHERING = "Gathering the debugging information…"
#: The Problems section's line when the daemon sent no list: "none" would say nothing is
#: wrong.
PROBLEMS_NOT_KNOWN = "not known: the daemon sent no problems list"
#: Under the description on the dialog's Details page, and word for word the
#: README's last section.
NOT_AFFILIATED = ("Gearbench is an independent project, not affiliated with or endorsed by "
                  "Logitech. Logitech, Logitech G, LIGHTSPEED, G HUB and ASTRO are "
                  "trademarks of Logitech. Dolby is a trademark of Dolby Laboratories.")
#: SADIE II's legal section under its attribution, markup: its own words, since libadwaita's
#: Apache-2.0 line says "no warranty" a second time (the owner's decision of 2026-09-29).
SADIE_LICENSE = ('Used under the <a href="https://www.apache.org/licenses/LICENSE-2.0">'
                 "Apache License, Version 2.0</a>. Its text ships with the data: "
                 "spatial/sadie-ii/LICENSE.txt in a checkout, "
                 "/usr/share/gearbench/spatial/sadie-ii/LICENSE.txt from the package.")


def read_journal(run: pipewire_client.Run = subprocess.run) -> str:
    """The daemon's last journal lines, or a sentence saying why not."""
    try:
        # A byte that is not UTF-8 becomes U+FFFD; raising would blank the page.
        done = run(JOURNAL, check=False, capture_output=True, encoding="utf-8",
                   errors="replace", timeout=JOURNAL_TIMEOUT)
    except FileNotFoundError:
        return "The journal could not be read: journalctl is not installed."
    except subprocess.TimeoutExpired:
        return f"The journal could not be read: journalctl took over {JOURNAL_TIMEOUT:.0f} s."
    except OSError as exc:
        return f"The journal could not be read: {reason(exc)}."
    if done.returncode != 0:
        said = " ".join(done.stderr.split())
        if said:
            return f"The journal could not be read: journalctl said {said}"
        return f"The journal could not be read: {ended('journalctl', done.returncode)}."
    return done.stdout.rstrip() or "The journal has no lines for Gearbench."


def device_nodes() -> list[tuple[str, Path]]:
    """The hidraw node of each device this build drives, with what it is, and /dev/uinput.
    From sysfs; nothing is opened. The G502 X PLUS's event node is left out: the daemon reads
    it with its user manager's groups, which can differ from this process's
    (evdev.group_denied), and reports a refusal as mouse_buttons_no_permission. Cabled,
    find_event_nodes also returns interface 01's node, which no rule grants."""
    # Each finder looks only for the ids its module's names table holds. Each keyboard model
    # plugged in: the daemon drives one of each.
    nodes = [_node(KEYBOARD_MODELS, LOGITECH_VENDOR, path, product)
             for path, product, _index in attached_keyboards()]
    nodes += [_node(MOUSE_MODELS, LOGITECH_VENDOR, path, product)
              for path, product, _index in find_attached_mice()]
    if (base := find_base()) is not None:
        nodes.append(_node(HEADSET_MODELS, HEADSET_VENDOR, *base))
    if (adapter := find_g432()) is not None:
        nodes.append(_node(G432_MODELS, LOGITECH_VENDOR, *adapter))
    # Each node the daemon tries in turn until one attaches.
    nodes += [_node(CENTURION_MODELS, LOGITECH_VENDOR, *found) for found in find_each_a50x()]
    nodes.append(("virtual keyboard and mouse", UINPUT))
    return nodes


def _node(names: Mapping[int, str], vendor: int, path: Path, product: int) -> tuple[str, Path]:
    """`path` with what it is: its model's name in `names`, then its vendor and product ids."""
    return f"{names[product]} {vendor:04x}:{product:04x}", path


def access_lines(nodes: list[tuple[str, Path]],
                 can: Callable[[Path, int], bool] = os.access) -> list[str]:
    """Whether this user may open each node the way the daemon does, which is
    what the udev rules' ACLs grant: a hidraw node read and write, /dev/uinput
    write only (uinput.py opens it O_WRONLY)."""
    lines = []
    for what, path in nodes:
        need, granted = ((os.W_OK, "write") if path == UINPUT
                         else (os.R_OK | os.W_OK, "read and write"))
        if not path.exists():
            state = "not there"
        elif can(path, need):
            state = granted
        else:
            state = f"no access: {NO_RULE}"
        lines.append(f"{path} ({what}): {state}")
    return lines


def debug_info(status: dict | Exception, access: list[str],
               spatial_rows: list[tuple[str, str]], journal: str) -> str:
    """The text of the Troubleshooting page, the home folder written ~. The
    daemon's status or its error, the problems, the spatial rows and the journal go through
    `wording.escaped`: a PipeWire client names its own stream, the saved
    gearbench-debug.txt is read in a terminal, and a newline in a name would
    start a section of its own."""
    gtk = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    adw = f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"
    # The problems first, as the tray's menu and the window put them.
    parts = [f"Gearbench {__version__}",
             f"Python {platform.python_version()}, GTK {gtk}, libadwaita {adw}",
             "", "== Problems =="]
    listed = None if isinstance(status, Exception) else status.get("problems")
    if isinstance(listed, list):
        parts += [escaped(f"- {p.get('message')} ({p.get('code')})")
                  for p in listed if isinstance(p, dict)] or ["none"]
    else:
        parts.append(PROBLEMS_NOT_KNOWN)
    parts += ["", "== Daemon status =="]
    if isinstance(status, Exception):
        parts.append(f"The daemon's status could not be read: {escaped(reason(status))}")
    else:
        # Not ensure_ascii, which writes each letter of a home folder outside ASCII as a JSON
        # escape, where home_shortened does not find it. A line break in a string is JSON's
        # escape too, so each line split here is one of the dump's own.
        parts += [escaped(line) for line in json.dumps(status, indent=2, sort_keys=True,
                                                       ensure_ascii=False).split("\n")]
    parts += ["", "== Device access =="] + access
    parts += ["", "== Spatial sound =="] + [escaped(f"{label}: {value}")
                                             for label, value in spatial_rows]
    parts += ["", f"== Journal ({' '.join(JOURNAL)}) =="]
    parts += [escaped(line) for line in journal.split("\n")]
    # The page is pasted into a public issue: a path in the home folder would name the login.
    return home_shortened("\n".join(parts))


def gather(send: Callable[..., dict] = send_command,
           run: pipewire_client.Run = subprocess.run,
           nodes: Callable[[], list[tuple[str, Path]]] = device_nodes) -> str:
    """Everything the page shows. For the worker thread: a round trip,
    processes and a walk of sysfs."""
    try:
        status: dict | Exception = send({"command": "status"})
    except (OSError, ValueError) as exc:
        status = exc
    return debug_info(status, access_lines(nodes()),
                      spatial.describe(spatial.read_state(run)), read_journal(run))


def about_dialog() -> Adw.AboutDialog:
    """The dialog, with the debug information still to come."""
    dialog = Adw.AboutDialog(
        application_name=APP_NAME, application_icon=DESKTOP_ENTRY, version=__version__,
        developer_name="wmuschetto", license_type=Gtk.License.GPL_3_0,
        website="https://github.com/gearbench/gearbench",
        comments=f"{APP_SUMMARY}.\n\n{NOT_AFFILIATED}",
        debug_info=GATHERING, debug_info_filename="gearbench-debug.txt")
    # Apache-2.0 asks for the attribution wherever the files go.
    dialog.add_legal_section(
        "SADIE II Database",
        "SADIE II Database v2-2, subjects D1 and D2, The Audio Lab, University of York. "
        "Copyright 2018 University of York. https://doi.org/10.5281/zenodo.12092466",
        Gtk.License.CUSTOM, SADIE_LICENSE)
    return dialog
