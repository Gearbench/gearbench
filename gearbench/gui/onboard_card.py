"""The Mouse tab's Onboard profile 1 group: what the profile holds, and the flash writes that
change what the mouse does without Gearbench.

This card owns the group's rows, what the last reply said of the profile and the dialogs
before each write. Its page builds it, places the group, hands it each `mouse` reply and
sends what a dialog confirms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..mouse import NO_ONBOARD, HostState, host_state, other_profile, profile_jobs
from ..onboard import stage_dpi, stages_words
from ..settings_files import backup_dir, shown_path
from ..wording import capitalized, word_list
from .style import PROFILE_WRITES_WAIT, explain, header_spinner, show_header_spinner

if TYPE_CHECKING:                       # pragma: no cover
    from .mouse_page import OfferWrite

#: The Programmable buttons row's first line in host mode with the spy armed. The group's
#: description (ONBOARD_HELP) says what the mouse runs without Gearbench.
HOST_BUTTONS = "Host mode: G4 to G9 reach Gearbench directly."
#: HOST_BUTTONS on a model whose writes the daemon refuses (`flash_refused`): it is never
#: switched to profile 1 (mouse.not_switched_untested), runs the profile it ran before once
#: Gearbench stops, and its buttons need not be G4 to G9.
REFUSED_HOST_BUTTONS = "Host mode: the buttons reach Gearbench directly."
#: What the row says of a profile 1 that holds the fallback write's entries.
FALLBACK_HELD = "Profile 1 holds the fallback: back, forward, DPI shift and steps, G9 nothing."
#: The fallback dialog's word over a profile 1 that holds it: the write changes nothing, and
#: still risks what Re-apply anyway's does (OnboardCard.REAPPLY_NOTES).
FALLBACK_AGAIN = ("Profile 1 holds it already: writing it again changes nothing, and a power or "
                  "link loss mid-write can leave it corrupt.")
#: The Profile backup row's subtitle in every state but a mouse whose writes the daemon
#: refuses (hold_refused): its dialog says when the write is in effect (AT_ONCE,
#: OTHER_PROFILE_NOTE, RESTORE_HOST_NOTE, RESTORE_GAME_NOTE).
RESTORE_HELP = "Writes back the newest backup of profile 1"
#: What the enable and restore dialogs say of when the write is in effect, outside host
#: mode, while the mouse runs profile 1 or has not been read running another.
AT_ONCE = "It takes effect at once."
#: What the enable and restore dialogs say in place of AT_ONCE outside host mode while the
#: mouse runs another profile, as the daemon's note after the write says
#: (mouse.written_note); mouse.other_profile's words go in.
OTHER_PROFILE_NOTE = "The mouse runs {}, so it is not in effect until the mouse runs profile 1."
#: What the restore dialog says in place of AT_ONCE in host mode.
RESTORE_HOST_NOTE = ("Gearbench uses its stages and rate at once; the mouse runs the rest when "
                     "Gearbench stops.")
#: The dialog's note in host mode while a loaded game profile's own stages or rate are in
#: effect, which stay over profile 1's (ProfileWrites.flash_note).
RESTORE_GAME_NOTE = ("Gearbench uses its stages and rate at once, except {}'s own while it is "
                     "loaded; the mouse runs the rest when Gearbench stops.")
#: What the enable dialog says in place of AT_ONCE for a mouse in host mode
#: with Enable… offered: left there, or its switch to onboard mode not taken.
ENABLE_HOST_NOTE = ("The mouse is in host mode, where no profile runs: G4 to G9 change once it "
                    "leaves host mode.")
#: The Programmable buttons row's state when profile 1 holds none of the six entries, nor the
#: fallback.
NOT_ENABLED = "Not enabled: profile 1 sends none of G4 to G9 as mouse buttons 11 to 16."
ONBOARD_HELP = "What the mouse runs without Gearbench, and in onboard mode"
#: What the Stages and rate row says before profile 1's stages when a write failed and the
#: sector could not be read back (the reply's `flash_unknown`).
PROFILE_UNKNOWN = ("Not known: a write failed and profile 1 could not be read back. Leave the "
                   "mouse on until Gearbench reads it again. Last read:")
#: ... and when profile 1 as read fails its CRC, which every edit refuses (mouse.BAD_CRC).
PROFILE_BAD_CRC = ("Profile 1 fails its CRC: write a backup over it with Restore profile "
                   "backup… before switching the mouse off. As read:")
#: ... and in its place on a model whose writes the daemon refuses (`flash_refused`), offered
#: no restore, where a blank profile 1 fails it too (mouse.not_switched_untested).
PROFILE_BAD_CRC_REFUSED = ("Profile 1 fails its CRC, as a blank one does; nothing needs doing. "
                           "As read:")
#: The Programmable buttons row's state while the Stages and rate row says any of these.
BUTTONS_IN_DOUBT = "Not known while profile 1 is in doubt: see Stages and rate."
#: A write dialog's word on the backup made first; the backup folder goes in.
BACKED_UP = "Every profile sector is backed up to {}/ first, and the write is verified."
#: The restore dialog's word on the backup it makes of profile 1 first.
RESTORE_UNDO = "The current profile is backed up first, so this can be undone."
#: ... and of a profile 1 that fails its CRC, which is kept as a .bad-read file that no
#: restore picks (OnboardProfiles.backup).
RESTORE_BAD_CRC_NOTE = ("The current profile is backed up first; it fails its CRC, so no "
                        "restore writes that copy back.")
#: ... and of a profile 1 not known since a failed write (PROFILE_UNKNOWN): a sector that
#: fails its CRC when the restore reads it is kept as a .bad-read too.
RESTORE_UNKNOWN_NOTE = ("The current profile is backed up first; if it fails its CRC, no "
                        "restore writes that copy back.")


def profile_summary(onboard: dict) -> str:
    """Profile 1's stages, default, shift and rate in one line, from the reply's `onboard`."""
    stages = onboard.get("dpi_stages") or []
    if not stages:
        return "—"
    default = stage_dpi(stages, onboard.get("default_dpi_index", 0))
    shift = stage_dpi(stages, onboard.get("shift_dpi_index", 0))
    rate = onboard.get("report_rate")
    return stages_words(stages, default, shift) + (f"; {rate} Hz" if rate else "")


def profile_doubt(onboard: dict, refused: bool) -> str | None:
    """What the Stages and rate row says before profile 1's stages, from the reply's `onboard`,
    or None for a profile 1 in no doubt. `refused` is True for a model whose writes the daemon
    refuses, which is offered no restore."""
    if onboard.get("flash_unknown"):
        return PROFILE_UNKNOWN
    if onboard.get("crc_ok") is not False:
        return None
    return PROFILE_BAD_CRC_REFUSED if refused else PROFILE_BAD_CRC


def buttons_state(info: dict, doubt: str | None, armed: bool) -> str:
    """The Programmable buttons row's state, from a `mouse` reply: nothing claimed of a
    profile 1 in `doubt` (profile_doubt), the model's own buttons' jobs where the daemon
    refuses its writes (`flash_refused`) or the enable write (`enable_refused`), and
    otherwise what profile 1 holds of the G502 X PLUS's G4 to G9. `armed`: the spy brings
    the buttons in host mode, and show puts HOST_BUTTONS or REFUSED_HOST_BUTTONS before the
    state."""
    if doubt is not None:
        return BUTTONS_IN_DOUBT
    if any(isinstance(info.get(key), str) for key in ("flash_refused", "enable_refused")):
        # Onboard: on the same tab "Profile 1" is an M profile's name, "Editing M1 · Profile 1".
        return f"Onboard profile 1: {profile_jobs(info.get('button_table'))}."
    if info.get("buttons_fallback"):
        return FALLBACK_HELD if armed else f"{FALLBACK_HELD} Enable… gives them their bindings."
    if info.get("buttons_enabled"):
        if armed:
            return ("Profile 1 has them as mouse buttons 11 to 16, which do nothing without "
                    "Gearbench.")
        if info.get("input_denied"):
            return f"Enabled in profile 1. Gearbench cannot read G4 to G9 from {info['input_denied']}."
        inputs = info.get("input") or []
        return ("Enabled in profile 1, nothing to do. "
                + (f"Reading {', '.join(inputs)}." if inputs
                   else "The mouse's event node is not open yet."))
    if info.get("enabled_buttons"):
        names = [b.upper() for b in info["enabled_buttons"]]
        return (f"Only {word_list(names, 'and')} {'is' if len(names) == 1 else 'are'} enabled "
                f"(an earlier write)."
                + ("" if armed else " Enable again for all six."))
    return NOT_ENABLED


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
    #: Why the daemon refuses every write to the last reply's mouse's profile 1
    #: (`flash_refused`), or None: it keeps those rows off too (hold_refused).
    _refused: str | None = None
    #: The last reply's mouse, with an onboard profile, is in host mode, where no profile runs.
    _host = False
    #: The loaded game profile whose own stages or rate are in force over profile 1's, by
    #: name, or None.
    _game_own: str | None = None
    #: The profile other than 1 the last reply's mouse was read running, in words, or None.
    #: Host mode's notes, where no profile runs, come first.
    _other: str | None = None
    #: What the Stages and rate row says of the last reply's profile 1 in doubt
    #: (profile_doubt), or None: the restore dialog's word on its backup follows it.
    _doubt: str | None = None
    #: What a re-apply does, checked against this mouse's own profile: the image it writes is
    #: byte-identical to the one on the device. Its risk and the way back follow.
    REAPPLY_LEAD = "Re-applying changes nothing, but still writes the mouse's onboard memory:"
    REAPPLY_NOTES = (
        "If the mouse loses power or its link mid-write, profile 1 can be left corrupt.",
        ("Restore profile backup… recovers profile 1; failing that, RECOVERY in "
         "gearbench-ctl(1) says how to write the factory copy back."),
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
        # Shown while a write one of its rows started is in flight (MousePage._write). One to
        # profile 1 backs up the directory and each profile sector (six on the G502 X PLUS),
        # then erases, rewrites and reads one back, 0.4 to 0.9 s in the journal, far longer if
        # the mouse stops answering part way. On the group, not a row: show_able hides the
        # Programmable buttons row for a mouse without the button table.
        self.write_spinner = header_spinner(onboard)
        self.enable_row.add_suffix(self.enable_button)
        self.enable_row.add_suffix(self.fallback_button)
        # A row of its own: it rewrites the flash, where Use factory bindings… only resets
        # bindings. Not markup: hold_refused puts the daemon's reason, which names the mouse,
        # in its subtitle.
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

    def show_able(self, able: set[str]) -> None:
        """Show the group for a mouse with `onboard`, and the Programmable buttons row for one
        with `buttons`: Enable… and Write fallback… write its model's button table's entries
        to profile 1, which the daemon refuses for a model without one
        (ProfileWrites.no_button_table)."""
        self.group.set_visible("onboard" in able)
        self.enable_row.set_visible("buttons" in able)

    def show_spinner(self, label: str | None) -> None:
        """The group's spinner, named `label` while a write its rows started is in flight,
        hidden for None."""
        show_header_spinner(self.write_spinner, label)

    def show_writing(self, usable: bool) -> None:
        """The rows on while `usable`, off again where hold_without_profile and hold_refused
        keep them."""
        for row in self.flash_rows:
            row.set_sensitive(usable)
        self.hold_without_profile()
        self.hold_refused()

    def hold_without_profile(self) -> None:
        """Keep the rows off for a mouse with no onboard profile, which a reply or the end
        of a write turns back on (show_writing)."""
        if self._no_onboard:
            for row in self.flash_rows:
                row.set_sensitive(False)

    def hold_refused(self) -> None:
        """Keep the rows off for a mouse whose every write to profile 1 the daemon refuses
        (`flash_refused`), the group's description saying so (show): said before a click, not
        after it. A reply or the end of a write turns the rows back on (show_writing), and
        this off again."""
        if self._refused is None:
            return
        for row in self.flash_rows:
            row.set_sensitive(False)

    def show(self, info: dict) -> None:
        """Put a `mouse` reply on the group: its description (PROFILE_WRITES_WAIT on a mouse
        whose writes the daemon refuses), the Stages and rate row, always profile 1's own,
        with a profile 1 in doubt (profile_doubt) said first; the Programmable buttons row's
        state (buttons_state) and its write, the next step only for a profile 1 in no doubt
        on a mouse whose writes the daemon takes. show_writing, which the page calls after
        this (_set_writing), keeps the Programmable buttons and Profile backup rows off for a
        mouse with no onboard profile or one whose writes the daemon refuses."""
        onboard = info.get("onboard") or {}
        refused = info.get("flash_refused")
        self._refused = refused if isinstance(refused, str) else None
        self.group.set_description(ONBOARD_HELP if self._refused is None else PROFILE_WRITES_WAIT)
        doubt = profile_doubt(onboard, self._refused is not None)
        summary = profile_summary(onboard)
        self.profile_row.set_subtitle(summary if doubt is None else f"{doubt} {summary}")
        self._doubt = doubt
        host = bool((info.get("host") or {}).get("active"))
        self._no_onboard = not onboard
        self._host = host and bool(onboard)
        game = info.get("game_profile") or {}
        self._game_own = (str(game.get("name")) if self._host and game.get("applies")
                          and (game.get("dpi_stages") or game.get("report_rate")) else None)
        self._other = other_profile(onboard.get("running_profile"))
        self.restore_row.set_subtitle(RESTORE_HELP)
        if not onboard:
            self.enable_row.set_subtitle(
                capitalized(NO_ONBOARD.format(info.get("name") or "mouse")))
            return
        # Neither is said of a profile in doubt: Re-apply…'s dialog would say the write
        # changes nothing, and an edit over a bad CRC is refused.
        self._already_enabled = doubt is None and bool(info.get("buttons_enabled"))
        self._fallback_held = doubt is None and bool(info.get("buttons_fallback"))
        # G4 to G9 reach the daemon in host mode only through an armed spy. A mouse left
        # there (the banner's mouse_left_in_host) gets them through neither, and runs profile 1
        # once switched off and on: its row reads as outside host mode.
        armed = host_state(info.get("host") or {}, True) is HostState.DRIVEN
        # Whether the daemon takes this mouse's writes to profile 1.
        writes = self._refused is None
        state = buttons_state(info, doubt, armed)
        first = HOST_BUTTONS if writes else REFUSED_HOST_BUTTONS
        self.enable_row.set_subtitle(f"{first} {state}" if armed else state)
        # In host mode the fallback write takes Enable…'s place: enabling
        # would change only what the mouse does without Gearbench. A mouse the daemon refuses
        # it is never offered it: its buttons would then do nothing outside host mode.
        enables = not armed and not isinstance(info.get("enable_refused"), str)
        self.enable_button.set_visible(enables)
        self.fallback_button.set_visible(armed)
        self.enable_button.set_label("Re-apply…" if self._already_enabled else "Enable…")
        settled = doubt is None and writes
        suggest(self.enable_button, settled and not self._already_enabled and enables)
        suggest(self.fallback_button, settled and not self._fallback_held)

    def on_enable_buttons(self, _b: Gtk.Button | None) -> None:
        if self._already_enabled:
            self._confirm_reapply()
            return
        dialog = Adw.AlertDialog(heading="Enable G4 to G9?")
        explain(dialog, paragraphs=(
            ("Onboard profile 1 is rewritten so G4 to G9 send mouse buttons "
             "11 to 16, which Gearbench reads."),),
            bullets=(
                BACKED_UP.format(shown_path(backup_dir())),
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
        explain(dialog, paragraphs=(self.REAPPLY_LEAD,), bullets=self.REAPPLY_NOTES)
        self._offer_write(dialog, "reapply", "Re-apply anyway",
                          Adw.ResponseAppearance.DESTRUCTIVE, {"command": "mouse_enable_buttons"})
        dialog.present(self._parent)

    def on_fallback_buttons(self, _b: Gtk.Button | None) -> None:
        """The fallback write, offered in place of Enable… in host mode with the spy armed."""
        dialog = Adw.AlertDialog(heading="Write the fallback to profile 1?")
        explain(dialog, paragraphs=(
            ("Onboard profile 1 is what the mouse runs without Gearbench. This sets G4 and "
             "G5 to back and forward, G6 to DPI shift, and G7 and G8 to DPI down and up; G9 "
             "does nothing."),
            *((FALLBACK_AGAIN,) if self._fallback_held else ())),
            bullets=(
                BACKED_UP.format(shown_path(backup_dir())),
                "In host mode, G4 to G9 keep their bindings.",
                "In onboard mode, G4 to G9 do these things instead of their bindings.",
            ))
        # Over a profile 1 that holds it, a flash write that changes nothing: Re-apply
        # anyway's guard, so Enter answers Cancel.
        self._offer_write(dialog, "write", "Write",
                          Adw.ResponseAppearance.DESTRUCTIVE if self._fallback_held
                          else Adw.ResponseAppearance.SUGGESTED,
                          {"command": "mouse_fallback_buttons"})
        dialog.present(self._parent)

    def on_restore_buttons(self, _b: Gtk.Button | None) -> None:
        dialog = Adw.AlertDialog(heading="Restore the newest backup?")
        explain(dialog, paragraphs=(
            (f"The newest backup of profile 1 in {shown_path(backup_dir())}/ is "
             "written back to the mouse."),),
            bullets=(
                (RESTORE_BAD_CRC_NOTE if self._doubt is PROFILE_BAD_CRC
                 else RESTORE_UNKNOWN_NOTE if self._doubt is PROFILE_UNKNOWN else RESTORE_UNDO),
                (RESTORE_GAME_NOTE.format(self._game_own) if self._game_own
                 else RESTORE_HOST_NOTE if self._host
                 else OTHER_PROFILE_NOTE.format(self._other) if self._other else AT_ONCE),
            ))
        self._offer_write(dialog, "restore", "Restore", Adw.ResponseAppearance.DESTRUCTIVE,
                          {"command": "mouse_restore"})
        dialog.present(self._parent)
