"""The Headset tab's Spatial sound group: Gearbench Spatial on or off, the
headset it plays to, its head and room, and how a game reaches it. The window
builds one, of the one output, and the tab moves it to the headset page that
shows (HeadsetStack).

The window runs this, never the daemon. The systemctl and pw-dump calls can
take seconds, so each goes through the window's worker thread; building the
group runs nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from gi.repository import Adw, GLib, GObject, Gtk

from .. import pipewire_client, spatial
from ..wording import capitalized
from .style import (
    ASLEEP,
    STARTING,
    plain_row,
    put_dot,
    status_dot,
    toast_not_saved,
    toast_note,
)
from .tips import explain_choices
from .worker import DaemonWorker, LoadingGuard, Result

HELP = "A 7.1 headphone output for games that play 5.1 or 7.1"
HEAD_HELP = f"Changing it restarts {spatial.DESCRIPTION} while it runs."
#: The Headset it plays to row: none picked, then each headset in spatial.HEADSETS' order.
HEADSET_KEYS = (None, *(headset.key for headset in spatial.HEADSETS))
HEADSET_LABELS = (spatial.FIRST_CONNECTED.capitalize(),
                  *(headset.name for headset in spatial.HEADSETS))
HEADSET_HELP = f"The one picked while it is plugged in, else {spatial.FIRST_CONNECTED}. {HEAD_HELP}"
HEAD_LABELS = tuple(spatial.HEAD_NAMES[head] for head in spatial.HEADS)
#: Each head's tooltip in the Head dropdown's list, looked up by head: spatial.HEADS gives the
#: order.
HEAD_TIPS = tuple({"ku100": ("Neumann KU100, SADIE II subject D1: the most preferred head "
                             "in SADIE II's listening test."),
                   "kemar": "KEMAR, SADIE II subject D2: a dummy head with torso and shoulders."}[head]
                  for head in spatial.HEADS)
ROOM_LABELS = tuple(room.title() for room in spatial.ROOMS)
#: Each room's tooltip in the Room dropdown's list, looked up by room: spatial.ROOMS gives the
#: order.
ROOM_TIPS = tuple({"competitive": "No room: direction alone.",
                   "immersive": ("A short, dry room about 11 dB under the sound, which moves it "
                                 "out of your head.")}[room]
                  for room in spatial.ROOMS)
LAUNCH_HELP = f"With {spatial.DESCRIPTION} off, the game plays on your default output in stereo"
ENV_HELP = f"Set {spatial.ENVIRONMENT} in the game's environment"
PICK_HELP = (f"Pick {spatial.DESCRIPTION} before the game starts, and your usual output "
             "after it quits")
DOLBY_HELP = ("Keep the headset's Dolby button on the star, or the sound is processed twice. "
              "Gearbench cannot read the button.")
#: The Outputs row's end, after the headset's joined_shown, while its card has another
#: profile set than spatial's for it and a rule of Gearbench's gives that one (ruled).
RESTART_WIREPLUMBER = ("until WirePlumber restarts: log out of every session and back in, "
                       "or run `systemctl --user restart wireplumber`.")
DOLBY_TOAST = f"{spatial.DESCRIPTION} is playing on the {spatial.A50.name}: keep Dolby on the star."
COPIED = "Copied"
#: Each action's toast line when its failure is too long for a toast, with
#: the whole failure behind Details: systemctl's two lines for a start it
#: cannot queue, the room's failure. A save that failed has a line of its own.
NOT_STARTED = f"{spatial.DESCRIPTION} did not start"
NOT_STOPPED = f"{spatial.DESCRIPTION} did not stop"
#: set_head and set_headset save first: their StepError comes from systemctl after that.
HEAD_NOT_RESTARTED = f"The head is saved, but {spatial.DESCRIPTION} did not restart"
HEADSET_NOT_RESTARTED = f"The headset is saved, but {spatial.DESCRIPTION} did not restart"
ROOM_NOT_CHANGED = "The room did not change"
#: An action's second read: a --no-block start is still activating at the
#: first read, and the link to the headset comes after it.
FOLLOW_UP_MS = 1500

#: The dot for each summary kind: a spinner while it starts; a kind missing here is red.
DOTS: dict[str, bool | str | None] = {
    "ready": True, "playing": True, "off": None, "starting": STARTING,
    **dict.fromkeys(("waiting", "stopped", "unlinked", "manual"), ASLEEP),
}
#: Kinds where systemctl cannot act, so the controls are grayed out.
LOCKED = ("not_installed", "no_manager")


class SpatialGroup:
    """The group and its polls; every `spatial` read and action runs programs with `run`."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 run: pipewire_client.Run = subprocess.run) -> None:
        self._worker = worker
        self._toast = toast
        self._run = run
        self._loading = LoadingGuard()
        #: A read is out; an action runs, which the window's close waits for.
        self._pending = False
        self.busy = False
        self._follow_up: int | None = None
        #: The last summary kind shown, None while no state is shown.
        self.kind: str | None = None
        #: The headset the chain plays to, or would, at the last read that could see it, and
        #: whether its sink was running on the A50 there: one start of play is toasted once.
        self._aimed: spatial.Headset | None = None
        self._running = False
        #: Two headsets were in PipeWire at the last read that could see it, for the
        #: Headset it plays to row.
        self._two_present = False
        self.widget = self._build()

    def _build(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Spatial sound", description=HELP)
        self.switch_row = plain_row(Adw.SwitchRow, spatial.DESCRIPTION, "")
        # Added once there is a state to show (put_dot).
        self.dot = status_dot()
        self.switch_row.connect("notify::active", self.on_switch)
        group.add(self.switch_row)

        self.headset_row = plain_row(Adw.ComboRow, "Headset it plays to", HEADSET_HELP,
                                     model=Gtk.StringList.new(HEADSET_LABELS), visible=False)
        self.headset_row.connect("notify::selected", self.on_choice, HEADSET_KEYS,
                                 spatial.set_headset, HEADSET_NOT_RESTARTED)
        group.add(self.headset_row)
        self.head_row = plain_row(Adw.ComboRow, "Head", HEAD_HELP,
                                  model=Gtk.StringList.new(HEAD_LABELS))
        explain_choices(self.head_row, HEAD_TIPS)
        self.head_row.connect("notify::selected", self.on_choice, spatial.HEADS, spatial.set_head,
                              HEAD_NOT_RESTARTED)
        group.add(self.head_row)
        self.room_row = plain_row(Adw.ComboRow, "Room", "",
                                  model=Gtk.StringList.new(ROOM_LABELS))
        explain_choices(self.room_row, ROOM_TIPS)
        self.room_row.connect("notify::selected", self.on_choice, spatial.ROOMS, spatial.set_room,
                              ROOM_NOT_CHANGED)
        group.add(self.room_row)

        self.launch_row = plain_row(Adw.ActionRow, "Steam launch option", spatial.LAUNCH_OPTION,
                                    subtitle_selectable=True, tooltip_text=LAUNCH_HELP)
        self.launch_copy = self._copy_button(spatial.LAUNCH_OPTION, "Copy the launch option")
        self.launch_row.add_suffix(self.launch_copy)
        group.add(self.launch_row)
        self.env_row = plain_row(Adw.ActionRow, "Other launchers", ENV_HELP)
        self.env_copy = self._copy_button(spatial.ENVIRONMENT, f"Copy {spatial.ENVIRONMENT}")
        self.env_row.add_suffix(self.env_copy)
        group.add(self.env_row)
        self.pick_row = plain_row(Adw.ActionRow, "Or pick it in GNOME", PICK_HELP)
        group.add(self.pick_row)

        self.dolby_row = plain_row(Adw.ActionRow, "Dolby on the star", DOLBY_HELP,
                                   visible=False)
        # A hint, always: the warning sign is the switch's dot's for a state that needs a
        # look, and DOLBY_TOAST says when play starts. A screen reader skips the icon.
        self.dolby_icon = Gtk.Image(icon_name="dialog-information-symbolic",
                                    valign=Gtk.Align.CENTER,
                                    accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.dolby_row.add_prefix(self.dolby_icon)
        group.add(self.dolby_row)
        self.outputs_row = plain_row(Adw.ActionRow, "Outputs", "", visible=False)
        group.add(self.outputs_row)
        self._set_sensitive()
        return group

    def _copy_button(self, text: str, label: str) -> Gtk.Button:
        button = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=label, css_classes=["flat"])
        # An icon alone: its name for a screen reader, as the mute buttons set theirs.
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", self.copy, text)
        return button

    # -- reading ----------------------------------------------------------

    def refresh(self) -> None:
        """Read the state on the worker thread, unless a read is already out."""
        if self._pending:
            return
        self._pending = True
        self._worker.run(lambda: spatial.read_state(run=self._run), self._on_state)

    def tick(self) -> None:
        """The window's poll, which leaves a running action to read after itself."""
        if not self.busy:
            self.refresh()

    def close(self) -> None:
        if self._follow_up is not None:
            GLib.source_remove(self._follow_up)
            self._follow_up = None

    def _on_state(self, result: Result) -> None:
        self._pending = False
        # A read that lands while an action runs was queued before it: the
        # action's own read follows.
        if self.busy:
            return
        if isinstance(result, spatial.State):
            self.show(result)
            return
        self.kind = None
        words = f"Could not be read: {result}"
        put_dot(self.switch_row, self.dot, False, words)
        self.switch_row.set_subtitle(words)
        self._set_sensitive()

    def show(self, state: spatial.State) -> None:
        """Set every row from `state`. A read whose systemctl or pw-dump failed leaves the
        rows it cannot see as they were, like a read that failed (_on_state)."""
        kind, words = spatial.summary(state)
        self.kind = kind
        hints = [hint for hint in (spatial.stereo_hint(state), spatial.stale_hint(state))
                 if hint]
        subtitle = capitalized(words)
        self.switch_row.set_subtitle("\n".join([subtitle, *hints]))
        if state.systemd is None:
            self._loading.put_active(self.switch_row, state.enabled)
            for way in (self.launch_row, self.env_row, self.pick_row):
                way.set_visible(state.installed)
        self._loading.put_selected(self.headset_row, HEADSET_KEYS.index(state.choices.headset))
        # Shown once there is a choice to make, or one made. A read that cannot see
        # PipeWire finds no headset there, which is not one unplugged.
        if state.tools and state.pipewire is None:
            self._two_present = len(state.present) >= 2
        self.headset_row.set_visible(self._two_present or state.choices.headset is not None)
        self._loading.put_selected(self.head_row, spatial.HEADS.index(state.choices.head))
        self._loading.put_selected(self.room_row, spatial.ROOMS.index(state.choices.room))
        put_dot(self.switch_row, self.dot, DOTS.get(kind, False), subtitle)
        self._set_sensitive()
        # A read whose systemctl or pw-dump failed cannot see the chain, which is not
        # play stopping or moving: like a read that failed (_on_state), it keeps the last
        # look's.
        if state.systemd is None and state.tools and state.pipewire is None:
            self._aimed = spatial.aimed(state)
            playing = state.running and self._aimed is spatial.A50
            if playing and not self._running:
                self._toast(DOLBY_TOAST)
            self._running = playing
            joined =spatial.joined_headset(state)
            self.outputs_row.set_visible(joined is not None)
            if joined is not None:
                headset = joined[0]
                self.outputs_row.set_subtitle(
                    f"{headset.joined_shown} {RESTART_WIREPLUMBER}" if headset.ruled
                    else f"{headset.joined_shown}, {spatial.OUTSIDE_RULE}.")
        # The switch, not state.enabled: a read whose systemctl failed leaves the switch
        # as the last look or a click since left it.
        self.dolby_row.set_visible(self.switch_row.get_active()
                                   and self._aimed is spatial.A50)

    def _set_sensitive(self) -> None:
        usable = self.kind is not None and self.kind not in LOCKED and not self.busy
        for row in (self.switch_row, self.headset_row, self.head_row, self.room_row):
            row.set_sensitive(usable)

    # -- actions ----------------------------------------------------------

    def on_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on = row.get_active()
        switch = spatial.switch_on if on else spatial.switch_off
        self._act(lambda: switch(run=self._run), NOT_STARTED if on else NOT_STOPPED)

    def on_choice(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec,
                  choices: tuple[str | None, ...], choose: Callable[..., object],
                  failed: str) -> None:
        index = row.get_selected()
        if self._loading.active or index >= len(choices):
            return
        choice = choices[index]
        self._act(lambda: choose(choice, run=self._run), failed)

    def _act(self, work: Callable[[], object], failed: str) -> None:
        """Run `work` on the worker; `failed` is the toast line for a failure
        too long for a toast, other than a save that failed (_acted)."""
        self.busy = True
        self._set_sensitive()
        self._worker.run(work, lambda result: self._acted(result, failed))

    def _acted(self, result: Result, failed: str) -> None:
        self.busy = False
        self._set_sensitive()
        words = None
        if isinstance(result, pipewire_client.StepError):
            words = capitalized(str(result))
        elif isinstance(result, OSError):
            # Only saving spatial.json raises one: systemctl's and PipeWire's
            # arrive as StepError. Nothing was saved, so nothing restarted or changed.
            toast_not_saved(self._toast, spatial.choices_path(), result)
        elif isinstance(result, Exception):
            words = f"Spatial sound: {result}"
        if words is not None:
            # one_sentence: systemctl's and pw-cli's words can hold ". ", so
            # a first sentence could end inside them.
            toast_note(self._toast, words, failed, one_sentence=True)
        self.refresh()
        if self._follow_up is not None:
            GLib.source_remove(self._follow_up)
        self._follow_up = GLib.timeout_add(FOLLOW_UP_MS, self._on_follow_up)

    def _on_follow_up(self) -> bool:
        self._follow_up = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    def copy(self, button: Gtk.Button, text: str) -> None:
        button.get_clipboard().set(text)
        self._toast(COPIED)
