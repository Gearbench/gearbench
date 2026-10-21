"""The About dialog, and the debug information behind its Troubleshooting page.

The debug information is the version, the daemon's `status` reply and its problems, whether
this user may open each device's hidraw node and /dev/uinput, what systemd and PipeWire say
of spatial sound, and the daemon's last 30 journal lines. `gather` reads it on the worker
thread, since a socket round trip or a process can take seconds; the dialog opens at once
and takes the text when it lands, which libadwaita shows even on an open page. A source that
fails says so in its own section.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gtk

from .. import __version__, spatial
from ..astro import PRODUCTS as HEADSET_MODELS, VENDOR_ID as HEADSET_VENDOR, find_base
from ..daemon import send_command
from ..desktop import APP_NAME, DESKTOP_ENTRY
from ..g432 import PRODUCTS as G432_MODELS, find_g432
from ..hidpp import NO_RULE, VENDOR_ID as LOGITECH_VENDOR
from ..keyboard import find_attached_keyboard
from ..mouse import MODELS as MOUSE_MODELS, find_attached_mice
from ..uinput import UINPUT as UINPUT_NODE
from ..wording import ended, escaped, reason

#: The journal section's command: the daemon's last lines and the user manager's about it.
#: The sender is matched by fields journald sets; USER_UNIT, which any program of this user can
#: set (a Flatpak with no permissions included), counts only on init.scope's lines, unlike -u.
_UID = f"_UID={os.getuid()}"
JOURNAL = ["journalctl", "--user", "_SYSTEMD_USER_UNIT=gearbench.service", _UID, "+",
           "_SYSTEMD_USER_UNIT=init.scope", "USER_UNIT=gearbench.service", _UID,
           "-n", "30", "--no-pager", "-o", "cat"]
#: How long journalctl gets. It answers in well under a second.
JOURNAL_TIMEOUT = 10.0
#: Where the virtual keyboard and mouse are made.
UINPUT = Path(UINPUT_NODE)
#: What the dialog shows until `gather` lands.
GATHERING = "Gathering the debug information…"
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


def read_journal(run: spatial.Run = subprocess.run) -> str:
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
    nodes: list[tuple[str, Path]] = []
    found = find_attached_keyboard()
    if found is not None:
        nodes.append((f"keyboard {LOGITECH_VENDOR:04x}:{found[1]:04x}", found[0]))
    for path, product, _index in find_attached_mice():
        model = MOUSE_MODELS.get(product, "mouse")
        nodes.append((f"{model} {LOGITECH_VENDOR:04x}:{product:04x}", path))
    base = find_base()
    if base is not None:
        model = HEADSET_MODELS.get(base[1], "headset")
        nodes.append((f"{model} {HEADSET_VENDOR:04x}:{base[1]:04x}", base[0]))
    adapter = find_g432()
    if adapter is not None:
        # find_g432 finds only the ids PRODUCTS names.
        model = G432_MODELS[adapter[1]]
        nodes.append((f"{model} {LOGITECH_VENDOR:04x}:{adapter[1]:04x}", adapter[0]))
    nodes.append(("virtual keyboard and mouse", UINPUT))
    return nodes


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
    """The text of the Troubleshooting page. The daemon's error, the
    problems, the spatial rows and the journal go through `wording.escaped`:
    a PipeWire client names its own stream, the saved gearbench-debug.txt is
    read in a terminal, and a newline in a name would start a section of its
    own."""
    gtk = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    adw = f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"
    parts = [f"Gearbench {__version__}",
             f"Python {platform.python_version()}, GTK {gtk}, libadwaita {adw}",
             "", "== Daemon status =="]
    if isinstance(status, Exception):
        parts.append(f"The daemon's status could not be read: {escaped(reason(status))}")
        listed = None
    else:
        parts.append(json.dumps(status, indent=2, sort_keys=True))
        listed = status.get("problems")
    parts += ["", "== Problems =="]
    if isinstance(listed, list):
        parts += [escaped(f"- {p.get('message')} ({p.get('code')})")
                  for p in listed if isinstance(p, dict)] or ["none"]
    else:
        parts.append(PROBLEMS_NOT_KNOWN)
    parts += ["", "== Device access =="] + access
    parts += ["", "== Spatial sound =="] + [escaped(f"{label}: {value}")
                                             for label, value in spatial_rows]
    parts += ["", f"== Journal ({' '.join(JOURNAL)}) =="]
    parts += [escaped(line) for line in journal.split("\n")]
    return "\n".join(parts)


def gather(send: Callable[..., dict] = send_command,
           run: spatial.Run = subprocess.run,
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
        comments="G and M keys, lighting, DPI and macros for Logitech G keyboards and "
                 "mice, and the settings of the Logitech G432 and Astro A50 Gen 4 "
                 "headsets, through the Gearbench daemon."
                 f"\n\n{NOT_AFFILIATED}",
        debug_info=GATHERING, debug_info_filename="gearbench-debug.txt")
    # Apache-2.0 asks for the attribution wherever the files go.
    dialog.add_legal_section(
        "SADIE II Database",
        "SADIE II Database v2-2, subjects D1 and D2, The Audio Lab, University of York. "
        "Copyright 2018 University of York. https://doi.org/10.5281/zenodo.12092466",
        Gtk.License.CUSTOM, SADIE_LICENSE)
    return dialog
