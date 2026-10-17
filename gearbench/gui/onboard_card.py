"""The Mouse tab's Onboard profile 1 group: what the profile holds, and the flash writes that
change what the mouse does without Gearbench.

This card owns the group's rows, what the last reply said of the profile and the dialogs
before each write. Its page builds it, places the group, hands it each `mouse` reply and
sends what a dialog confirms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..config import shown_config_dir, word_list
from ..mouse import HostState, host_state, profile_words, runs_another_profile
from ..onboard import stage_dpi
from .style import explain

if TYPE_CHECKING:                       # pragma: no cover
    from .mouse_page import OfferWrite

#: The Programmable buttons row's first line in host mode with the spy armed. It says what
#: profile 1 is for without "fallback", which the row keeps for what Write fallback… writes.
HOST_BUTTONS = ("Host mode: G4 to G9 reach Gearbench directly; without Gearbench the mouse "
                "runs profile 1.")
#: What the row says of a profile 1 that holds the fallback write's entries.
FALLBACK_HELD = "Profile 1 holds the fallback: back, forward, DPI shift and steps, G9 nothing."
#: The Profile backup row's subtitle, and in host mode, where no profile runs until Gearbench
#: stops.
RESTORE_HELP = "Writes back the newest backup of profile 1. Takes effect at once"
RESTORE_HOST_HELP = ("Writes back the newest backup of profile 1. Gearbench uses its stages and "
                     "rate at once, the rest when Gearbench stops")
#: What the enable and restore dialogs say of when the write is in effect, outside host
#: mode, while the mouse runs profile 1 or has not been read running another.
AT_ONCE = "It takes effect at once."
#: What the enable and restore dialogs say in place of AT_ONCE outside host mode while the
#: mouse runs another profile, as the daemon's note after the write says
#: (mouse.written_note); mouse.profile_words goes in.
OTHER_PROFILE_NOTE = "The mouse runs {}, so it is not in effect until the mouse runs profile 1."
#: The Profile backup row's subtitle then.
RESTORE_OTHER_HELP = ("Writes back the newest backup of profile 1. Not in effect while the "
                      "mouse runs {}")
#: What the restore dialog says in place of AT_ONCE in host mode.
RESTORE_HOST_NOTE = ("Gearbench uses its stages and rate at once; the mouse runs the rest when "
                     "Gearbench stops.")
#: The row's subtitle and the dialog's note in host mode while a loaded game profile's own
#: stages or rate are in force, which stay over profile 1's (ProfileWrites.flash_note).
RESTORE_GAME_HELP = ("Writes back the newest backup of profile 1. Gearbench uses its stages and "
                     "rate at once, except {}'s own, the rest when Gearbench stops")
RESTORE_GAME_NOTE = ("Gearbench uses its stages and rate at once, except {}'s own while it is "
                     "loaded; the mouse runs the rest when Gearbench stops.")
#: What the enable dialog says in place of AT_ONCE for a mouse in host mode
#: with Enable… offered: left there, or its switch to onboard mode not taken.
ENABLE_HOST_NOTE = ("The mouse is in host mode, where no profile runs: G4 to G9 change once it "
                    "leaves host mode.")
#: The Programmable buttons row's state when profile 1 holds none of the six entries, nor the
#: fallback.
NOT_ENABLED = "Not enabled: profile 1 sends none of G4 to G9 as mouse buttons 11 to 16."
ONBOARD_HELP = "What the mouse runs without Gearbench, and in onboard mode."
#: What the Stages and rate row says before profile 1's stages when a write failed and the
#: sector could not be read back (the reply's `flash_unknown`).
PROFILE_UNKNOWN = ("Not known: a write failed and profile 1 could not be read back. Leave the "
                   "mouse on until Gearbench reads it again. Last read:")
#: ... and when profile 1 as read fails its CRC, which every edit refuses (mouse.BAD_CRC).
PROFILE_BAD_CRC = ("Profile 1 fails its CRC: write a backup over it with Restore profile "
                   "backup… before switching the mouse off. As read:")
#: The Programmable buttons row's state while either holds.
BUTTONS_IN_DOUBT = "Not known while profile 1 is in doubt: see Stages and rate."
#: A write dialog's word on the backup made first; the config folder goes in.
BACKED_UP = "Every profile sector is backed up to {}/backup/ first, and the write is verified."
#: The restore dialog's word on the backup it makes of profile 1 first.
RESTORE_UNDO = "The current profile is backed up first, so this can be undone."
#: ... and of a profile 1 that fails its CRC, which is kept as a .bad-read file that no
#: restore picks (OnboardProfiles.backup).
RESTORE_BAD_CRC_NOTE = ("The current profile is backed up first; it fails its CRC, so no "
                        "restore writes that copy back.")


def profile_summary(onboard: dict) -> str:
    """Profile 1's stages, default, shift and rate in one line, from the reply's `onboard`."""
    stages = onboard.get("dpi_stages") or []
    if not stages:
        return "—"
    default = stage_dpi(stages, onboard.get("default_dpi_index", 0))
    shift = stage_dpi(stages, onboard.get("shift_dpi_index", 0))
    listed = ", ".join(str(s) for s in stages)
    rate = onboard.get("report_rate")
    return (f"{listed} DPI, default {default}, shift {shift}"
            + (f"; {rate} Hz" if rate else ""))


def suggest(button: Gtk.Button, on: bool) -> None:
    """Style `button` as the next step, or not."""
    if on:
        button.add_css_class("suggested-action")
    else:
        button.remove_css_class("suggested-action")


class OnboardCard:
    """The Onboard profile 1 group, and the writes its buttons offer.

    `parent` is the window a dialog is presented over. `offer_write` is the page's: it puts
    Cancel and the write's button on a dialog, and sends the write to the mouse the dialog
    was opened for.
    """

    #: The last reply held no onboard profile, which keeps Enable… and
    #: Restore profile backup… off (hold_without_profile).
    _no_onboard = False
    #: The last reply's mouse, with an onboard profile, is in host mode, where no profile runs.
    _host = False
    #: The loaded game profile whose own stages or rate are in force over profile 1's, by
    #: name, or None.
    _game_own: str | None = None
    #: The profile other than 1 the last reply's mouse was read running, in words, or None.
    #: Host mode's notes, where no profile runs, come first.
    _other: str | None = None
    #: The Stages and rate row says the last reply's profile 1 fails its CRC (PROFILE_BAD_CRC);
    #: a profile unknown since a failed write (PROFILE_UNKNOWN) is said in its place.
    _bad_crc = False
    #: What a re-apply does, checked against this mouse's own profile: the image it writes is
    #: byte-identical to the one on the device.
    REAPPLY_NOTES = (
        "It changes nothing: profile 1 already holds these six entries, byte for byte.",
        "It still writes to the mouse's onboard memory.",
        ("If the mouse loses power or its link mid-write, profile 1 can be "
         "left corrupt; that is the only risk."),
        "Restore profile backup… recovers from that; the mouse's factory copy is the last resort.",
    )

    def __init__(self, parent: Adw.ApplicationWindow, offer_write: OfferWrite) -> None:
        self._parent = parent
        self._offer_write = offer_write
        #: Whether profile 1 already has all six entries written.
        self._already_enabled = False
        #: Whether profile 1 already holds the fallback.
        self._fallback_held = False

    def build(self) -> Adw.PreferencesGroup:
        """Onboard profile 1: what it holds, and the writes that change it."""
        onboard = self.group = Adw.PreferencesGroup(title="Onboard profile 1",
                                                    description=ONBOARD_HELP)
        # Profile 1's own, whatever a loaded game profile's rows above show. Not markup.
        self.profile_row = Adw.ActionRow(title="Stages and rate", subtitle="—",
                                         use_markup=False)
        onboard.add(self.profile_row)
        self.enable_row = Adw.ActionRow(title="Programmable buttons", subtitle="—",
                                        use_markup=False)
        # Stays once the buttons are enabled, its label and prominence then saying there is
        # nothing to do: it rewrites a profile already right, still a flash write.
        self.enable_button = Gtk.Button(label="Enable…", valign=Gtk.Align.CENTER,
                                        css_classes=["suggested-action"])
        self.enable_button.connect("clicked", self.on_enable_buttons)
        # In host mode in place of Enable…: the spy brings all six whatever
        # profile 1 holds, which is then what the mouse does without Gearbench.
        self.fallback_button = Gtk.Button(label="Write fallback…", valign=Gtk.Align.CENTER,
                                          visible=False)
        self.fallback_button.connect("clicked", self.on_fallback_buttons)
        # Shown while any of the page's writes is in flight, a loaded game profile's stages or
        # rate included. One to profile 1 backs up six sectors, then erases, rewrites and reads
        # one back, 0.4 to 0.9 s in the journal, far longer if the mouse stops answering part
        # way.
        self.write_spinner = Adw.Spinner(valign=Gtk.Align.CENTER, visible=False)
        self.write_spinner.update_property([Gtk.AccessibleProperty.LABEL],
                                           ["Writing to the mouse"])
        self.enable_row.add_suffix(self.write_spinner)
        self.enable_row.add_suffix(self.enable_button)
        self.enable_row.add_suffix(self.fallback_button)
        # A row of its own: it rewrites the flash, where Restore defaults
        # only resets bindings. Not markup: its subtitle can name a game profile.
        self.restore_row = Adw.ActionRow(title="Profile backup", subtitle=RESTORE_HELP,
                                         use_markup=False)
        restore = Gtk.Button(label="Restore profile backup…", valign=Gtk.Align.CENTER)
        restore.connect("clicked", self.on_restore_buttons)
        self.restore_row.add_suffix(restore)
        onboard.add(self.enable_row)
        onboard.add(self.restore_row)
        return onboard

    @property
    def flash_rows(self) -> tuple[Gtk.Widget, ...]:
        """The rows whose buttons write onboard profile 1."""
        return (self.enable_row, self.restore_row)

    def hold_without_profile(self) -> None:
        """Keep the rows off for a mouse with no onboard profile, which a reply or the end
        of a write turns back on with the page's other rows."""
        if self._no_onboard:
            for row in self.flash_rows:
                row.set_sensitive(False)

    def show(self, info: dict) -> None:
        """The Stages and rate row, always profile 1's own, and the Programmable buttons and
        Profile backup rows: what profile 1 holds of G4 to G9, with Write fallback… in place
        of Enable… in host mode with the spy armed, and both rows insensitive for a mouse
        with no onboard profile.

        A profile 1 in doubt, unknown since a write failed (`flash_unknown`) or failing its
        CRC, is said first on the Stages and rate row, and the Programmable buttons row then
        claims nothing of it, nor offers its writes as the next step."""
        onboard = info.get("onboard") or {}
        doubt = (PROFILE_UNKNOWN if onboard.get("flash_unknown")
                 else PROFILE_BAD_CRC if onboard.get("crc_ok") is False else None)
        summary = profile_summary(onboard)
        self.profile_row.set_subtitle(summary if doubt is None else f"{doubt} {summary}")
        self._bad_crc = doubt is PROFILE_BAD_CRC
        host = bool((info.get("host") or {}).get("active"))
        inputs = info.get("input") or []
        self._no_onboard = not onboard
        self._host = host and bool(onboard)
        game = info.get("game_profile") or {}
        self._game_own = (str(game.get("name")) if self._host and game.get("applies")
                          and (game.get("dpi_stages") or game.get("report_rate")) else None)
        running = onboard.get("running_profile")
        self._other = profile_words(running) if runs_another_profile(running) else None
        self.restore_row.set_subtitle(
            RESTORE_GAME_HELP.format(self._game_own) if self._game_own
            else RESTORE_HOST_HELP if self._host
            else RESTORE_OTHER_HELP.format(self._other) if self._other else RESTORE_HELP)
        if not onboard:
            self.enable_row.set_subtitle("This mouse has no onboard profile feature")
            self.hold_without_profile()
            return
        # Neither is said of a profile in doubt: Re-apply…'s dialog would say the write
        # changes nothing, and an edit over a bad CRC is refused.
        self._already_enabled = doubt is None and bool(info.get("buttons_enabled"))
        self._fallback_held = doubt is None and bool(info.get("buttons_fallback"))
        # G4 to G9 reach the daemon in host mode only through an armed spy. A mouse left
        # there (the banner's mouse_left_in_host) gets them through neither, and runs profile 1
        # once switched off and on: its row reads as outside host mode.
        armed = host_state(info.get("host") or {}, True) is HostState.DRIVEN
        if doubt is not None:
            state = BUTTONS_IN_DOUBT
        elif self._fallback_held:
            state = FALLBACK_HELD if armed else f"{FALLBACK_HELD} Enable… gives them their bindings."
        elif self._already_enabled and armed:
            state = ("Profile 1 has them as mouse buttons 11 to 16, which do nothing without "
                     "Gearbench.")
        elif self._already_enabled and info.get("input_denied"):
            state = f"Enabled in profile 1. Gearbench cannot read G4 to G9 from {info['input_denied']}."
        elif self._already_enabled:
            state = ("Enabled in profile 1, nothing to do. " +
                     (f"Reading {', '.join(inputs)}." if inputs
                      else "The mouse's event node is not open yet."))
        elif info.get("enabled_buttons"):
            names = [b.upper() for b in info["enabled_buttons"]]
            state = (f"Only {word_list(names, 'and')} {'is' if len(names) == 1 else 'are'} enabled "
                     f"(an earlier write)." + ("" if armed else " Enable again for all six."))
        else:
            state = NOT_ENABLED
        self.enable_row.set_subtitle(f"{HOST_BUTTONS} {state}" if armed else state)
        # In host mode the fallback write takes Enable…'s place: enabling
        # would change only what the mouse does without Gearbench.
        self.enable_button.set_visible(not armed)
        self.fallback_button.set_visible(armed)
        self.enable_button.set_label("Re-apply…" if self._already_enabled else "Enable…")
        suggest(self.enable_button, doubt is None and not self._already_enabled and not armed)
        suggest(self.fallback_button, doubt is None and not self._fallback_held)

    def on_enable_buttons(self, _b: Gtk.Button | None) -> None:
        if self._already_enabled:
            self._confirm_reapply()
            return
        dialog = Adw.AlertDialog(heading="Enable G4 to G9?")
        explain(dialog, paragraphs=(
            ("Onboard profile 1 is rewritten so G4 to G9 send mouse buttons "
             "11 to 16, which Gearbench reads."),),
            bullets=(
                BACKED_UP.format(shown_config_dir()),
                ENABLE_HOST_NOTE if self._host
                else OTHER_PROFILE_NOTE.format(self._other) if self._other else AT_ONCE,
                "The keyboard is not touched.",
            ))
        self._offer_write(dialog, "enable", "Enable", Adw.ResponseAppearance.SUGGESTED,
                          {"command": "mouse_enable_buttons"})
        dialog.present(self._parent)

    def _confirm_reapply(self) -> None:
        """Enabling again when it is already done. Say what that costs."""
        dialog = Adw.AlertDialog(heading="The buttons are already enabled")
        explain(dialog, paragraphs=("Re-applying is not needed:",),
                bullets=self.REAPPLY_NOTES)
        self._offer_write(dialog, "reapply", "Re-apply anyway",
                          Adw.ResponseAppearance.DESTRUCTIVE, {"command": "mouse_enable_buttons"})
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.present(self._parent)

    def on_fallback_buttons(self, _b: Gtk.Button | None) -> None:
        """The fallback write, which the row offers in host mode."""
        dialog = Adw.AlertDialog(heading="Write the fallback to profile 1?")
        explain(dialog, paragraphs=(
            ("Onboard profile 1 is what the mouse runs without Gearbench. This sets G4 and "
             "G5 to back and forward, G6 to DPI shift, and G7 and G8 to DPI down and up; G9 "
             "does nothing."),
            *(("Profile 1 holds it already; writing it again changes nothing.",)
              if self._fallback_held else ())),
            bullets=(
                BACKED_UP.format(shown_config_dir()),
                "In host mode, G4 to G9 keep their bindings.",
                "In onboard mode, G4 to G9 do these things instead of their bindings.",
            ))
        self._offer_write(dialog, "write", "Write", Adw.ResponseAppearance.SUGGESTED,
                          {"command": "mouse_fallback_buttons"})
        dialog.present(self._parent)

    def on_restore_buttons(self, _b: Gtk.Button | None) -> None:
        dialog = Adw.AlertDialog(heading="Restore the newest backup?")
        explain(dialog, paragraphs=(
            (f"The newest backup of profile 1 in {shown_config_dir()}/backup/ is "
             "written back to the mouse."),),
            bullets=(
                RESTORE_BAD_CRC_NOTE if self._bad_crc else RESTORE_UNDO,
                (RESTORE_GAME_NOTE.format(self._game_own) if self._game_own
                 else RESTORE_HOST_NOTE if self._host
                 else OTHER_PROFILE_NOTE.format(self._other) if self._other else AT_ONCE),
            ))
        self._offer_write(dialog, "restore", "Restore", Adw.ResponseAppearance.DESTRUCTIVE,
                          {"command": "mouse_restore"})
        dialog.present(self._parent)
