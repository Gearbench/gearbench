"""The window's banners for a problem in the daemon's status: the uinput banner, the
other-tools banner and the config banner.

Each part owns its banner and the rule for which of the status's `problems` it says. The
window builds them, places each banner and hands each `status` reply to `take`.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw

from ..config import unusable
from ..settings_files import FileChanged, FileUnreadable
from ..wording import RELOAD_LABEL, capitalized, home_shortened
from .style import displayable

#: What the uinput banner adds after the daemon's no_virtual_keyboard or no_virtual_mouse
#: problem, after "; ": a one-line text the window shows ends with no period (the user's
#: decision of 2026-10-27). It names the GUIDE's section on /dev/uinput, both problems' cause.
SEE_GUIDE = "see No virtual keyboard under Troubleshooting in the user guide"
#: The uinput banner's problems: with both in a status, it says the first.
UINPUT_PROBLEMS = ("no_virtual_keyboard", "no_virtual_mouse")
#: The refusals that are a problem with a file (ConfigError.kind), for the config banner.
CONFIG_KINDS = (FileChanged.kind, FileUnreadable.kind)
#: The config banner's title while the window itself could not read the file.
NOT_READ_TITLE = ("config.json could not be read ({}), so no binding, keyboard color or "
                  "effect, profile save or M profile name is sent; fix it, then reload it")


class UinputBanner:
    """Up while the daemon has no virtual keyboard or mouse. No button: the daemon retries
    every 30 s (VIRTUAL_RETRY) and at the next bind, and the fix (the udev rule or the
    uinput module) is outside the window."""

    def __init__(self) -> None:
        self.banner = Adw.Banner(use_markup=False)

    def take(self, status: dict) -> None:
        """Up while the daemon lacks a virtual device, in its problem's words: the virtual
        keyboard's while both are missing."""
        found = {p["code"]: p["message"] for p in status.get("problems") or ()
                 if p["code"] in UINPUT_PROBLEMS}
        message = next((found[code] for code in UINPUT_PROBLEMS if code in found), None)
        if message is not None:
            self.banner.set_title(f"{capitalized(message)}; {SEE_GUIDE}")
        self.banner.set_revealed(message is not None)


class ToolsBanner:
    """Up while another program that drives these devices runs (apps.OTHER_TOOLS), a line
    each in the daemon's words: no markup. No button: each is quit outside the window."""

    def __init__(self) -> None:
        self.banner = Adw.Banner(use_markup=False)

    def take(self, status: dict) -> None:
        """A line each for the `other_hid_tool` problems, and down when there are none."""
        found = [p["message"] for p in status.get("problems") or ()
                 if p["code"] == "other_hid_tool"]
        if found:
            self.banner.set_title("\n".join(found))
        self.banner.set_revealed(bool(found))


class ConfigBanner:
    """Up while config.json or the loaded game profile's file cannot be read, or one of them
    or a macro's was edited on disk: nothing is saved over it until a reload, which its
    button runs through `reload`. Not markup: the title carries the file's path and the
    error's words."""

    def __init__(self, reload: Callable[[], None]) -> None:
        self.banner = Adw.Banner(button_label=RELOAD_LABEL, use_markup=False)
        self.banner.connect("button-clicked", lambda _b: reload())

    def show(self, text: str) -> None:
        """Up with `text`, capitalized, the home folder written ~ and lone surrogates made
        displayable: a key or a link's target in an error can hold one."""
        self.banner.set_title(displayable(capitalized(home_shortened(text))))
        self.banner.set_revealed(True)

    def take(self, status: dict, own_error: Exception | None) -> None:
        """The window's own failed read of config.json (`own_error`) first, then what the
        daemon's `problems` say of config.json, the loaded game profile's file or a macro's,
        and down when nothing does."""
        if own_error is not None:
            self.show(NOT_READ_TITLE.format(unusable(own_error)))
            return
        problem = next((p for p in status.get("problems") or () if p["code"] in CONFIG_KINDS), None)
        if problem is None:
            self.banner.set_revealed(False)
        else:
            self.show(problem["message"])
