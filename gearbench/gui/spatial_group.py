"""The Headset tab's Spatial sound group: Gearbench Spatial on or off, its head and room,
and how a game reaches it; and the Headset for computer sound group above Outputs, whose
one row picks the headset Gearbench Spatial and Outputs play to, which spatial.json keeps
and both read. The window builds one of each, and the tab moves them to the headset page
that shows (HeadsetStack).

The window runs this, never the daemon. The systemctl and pw-dump calls can
take seconds, so each goes through the window's worker thread; building the
groups runs nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Collection

from gi.repository import Adw, GObject, Gtk

from .. import pipewire_client, pipewire_headsets, spatial
from ..astro import NAME as A50_NAME
from ..wording import capitalized
from .style import (
    ASLEEP,
    COULD_NOT_READ,
    plain_row,
    put_dot,
    status_dot,
    toast_not_saved,
    toast_note,
)
from .tips import explain_choices
from .unit_switch import NOT_STARTED_OF, NOT_STOPPED_OF, UNIT_DOTS, failure_words
from .worker import FOLLOW_UP_MS, DaemonWorker, LoadingGuard, Result, Settle

TITLE = "Spatial sound"
HELP = "A 7.1 headphone output for games that play 5.1 or 7.1"
#: What a change of the Head row or the Headset row does to a running Gearbench Spatial: the
#: line of both rows. The Headset row's own entry shows where the sound goes while the pick
#: is unplugged, so its line says no more.
RESTARTS_IT = f"Changing it restarts {spatial.DESCRIPTION} while it runs"
#: The group above Outputs and Spatial sound on each headset page, and its one row (the
#: owner's decisions of 2026-10-28).
PICK_TITLE = "Headset for computer sound"
PICK_ROW = "Headset"
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
#: The third way to a game's sound, which names no desktop: GNOME and KDE Plasma both pick
#: the output in their sound settings and their panel's volume menu.
PICK_IT = "Default output"
PICK_HELP = (f"Pick {spatial.DESCRIPTION} before the game starts, and your usual output "
             "after it quits")
DOLBY_HELP = ("Keep the headset's button on the star, or the sound is processed twice; "
              "Gearbench cannot read it")
#: The row for a headset whose card lists its outputs another way (spatial.joined_headset):
#: not "Outputs", the group above Spatial sound.
JOINED_ROW = "Headset outputs"
#: The Headset outputs row's end, after the headset's joined_shown, while its card has
#: another profile set than spatial's for it, a rule of Gearbench's gives that one (ruled),
#: and spatial.restart_needed asks for the restart.
RESTART_WIREPLUMBER = (f"{pipewire_headsets.UNTIL_RESTART}: log out of every session and back in, "
                       "or run `systemctl --user restart wireplumber`")
DOLBY_TOAST = f"{spatial.DESCRIPTION} is playing on the {A50_NAME}: keep Dolby off, on the star"
COPIED = "Copied"
#: Each action's toast line when its failure is too long for a toast, with
#: the whole failure behind Details: systemctl's two lines for a start it
#: cannot queue, the room's failure. A save that failed has a line of its own.
NOT_STARTED = NOT_STARTED_OF.format(spatial.DESCRIPTION)
NOT_STOPPED = NOT_STOPPED_OF.format(spatial.DESCRIPTION)
#: set_head and set_headset save first: their StepError comes from systemctl after that.
HEAD_NOT_RESTARTED = f"The head is saved, but {spatial.DESCRIPTION} did not restart"
HEADSET_NOT_RESTARTED = f"The headset is saved, but {spatial.DESCRIPTION} did not restart"
ROOM_NOT_CHANGED = "The room did not change"

#: The dot for each summary kind; a kind missing here is red.
DOTS: dict[str, bool | str | None] = {
    "ready": True, "playing": True, **dict.fromkeys(("waiting", "unlinked", "manual"), ASLEEP),
    **UNIT_DOTS}


def headset_choices(present: Collection[str]) -> tuple[str | None, ...]:
    """The Headset row's choices: none picked, then each headset of `present`, the keys of
    those plugged in, in pipewire_headsets.HEADSETS' order, so a replug moves no entry (the owner's
    decision of 2026-10-28: only the headsets connected)."""
    return (None, *(headset.key for headset in pipewire_headsets.HEADSETS
                    if headset.key in present))


def headset_labels(choices: tuple[str | None, ...]) -> list[str]:
    """Each of headset_choices' choices as the Headset row lists it, in the CLI's words."""
    return [capitalized(spatial.headset_words(spatial.Choices(headset=key))) for key in choices]


class SpatialGroup:
    """The groups and their polls; every `spatial` read and action runs programs with `run`,
    `picked` is told as a pick on the Headset row ends, and `kept_up` of an action that
    failed (Window.kept_up)."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 kept_up: Callable[[], None], run: pipewire_client.Run = subprocess.run,
                 picked: Callable[[], object] = lambda: None) -> None:
        self._worker = worker
        self._toast = toast
        self._kept_up = kept_up
        self._run = run
        #: Told as a pick on the Headset row ends, saved or not: Outputs plays to the headset
        #: picked too, and reads it from spatial.json (outputs_state.read_state).
        self._picked = picked
        self._loading = LoadingGuard()
        #: A read is out; an action runs, which the window's close waits for.
        self._pending = False
        self.busy = False
        #: The read again FOLLOW_UP_MS after an action (_acted).
        self._follow_up = Settle(self.refresh)
        #: The last summary kind shown, None while no state is shown.
        self.kind: str | None = None
        #: The headset the chain plays to, or would, at the last read that could see it, and
        #: whether its sink was running on the A50 there: one start of play is toasted once.
        self._aimed: pipewire_headsets.Headset | None = None
        self._running = False
        #: The headsets in PipeWire at the last read that could see it, by key, and the
        #: choices the Headset row lists for them (headset_choices).
        self._present: tuple[str, ...] = ()
        self._headset_keys = headset_choices(())
        self.headset_group = self._build_headset()
        self.widget = self._build()

    def _build_headset(self) -> Adw.PreferencesGroup:
        """The Headset for computer sound group, hidden until there is a choice to make or
        one made (_show_headsets)."""
        group = Adw.PreferencesGroup(title=PICK_TITLE, visible=False)
        self.headset_row = plain_row(Adw.ComboRow, PICK_ROW, RESTARTS_IT,
                                     model=Gtk.StringList.new(headset_labels(self._headset_keys)))
        self.headset_row.connect("notify::selected", self.on_headset)
        group.add(self.headset_row)
        return group

    def _build(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=TITLE, description=HELP)
        self.switch_row = plain_row(Adw.SwitchRow, spatial.DESCRIPTION, "")
        # Added once there is a state to show (put_dot).
        self.dot = status_dot()
        self.switch_row.connect("notify::active", self.on_switch)
        group.add(self.switch_row)

        self.head_row = plain_row(Adw.ComboRow, "Head", RESTARTS_IT,
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
        self.pick_row = plain_row(Adw.ActionRow, PICK_IT, PICK_HELP)
        group.add(self.pick_row)

        self.dolby_row = plain_row(Adw.ActionRow, "Dolby button", DOLBY_HELP, visible=False)
        # A hint, always: the warning sign is the switch's dot's for a state that needs a
        # look, and DOLBY_TOAST says when play starts. A screen reader skips the icon.
        self.dolby_icon = Gtk.Image(icon_name="dialog-information-symbolic",
                                    valign=Gtk.Align.CENTER,
                                    accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.dolby_row.add_prefix(self.dolby_icon)
        group.add(self.dolby_row)
        self.outputs_row = plain_row(Adw.ActionRow, JOINED_ROW, "", visible=False)
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
        self._follow_up.cancel()

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
        words = COULD_NOT_READ.format(result)
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
        # A read that cannot see PipeWire finds no headset there, which is not one unplugged.
        if state.tools and state.pipewire is None:
            self._present = state.present
        self._show_headsets(state.choices.headset)
        self._loading.put_selected(self.head_row, spatial.HEADS.index(state.choices.head))
        self._loading.put_selected(self.room_row, spatial.ROOMS.index(state.choices.room))
        put_dot(self.switch_row, self.dot, DOTS.get(kind, False), subtitle)
        self._set_sensitive()
        # A read whose systemctl or pw-dump failed cannot see the chain, which is not
        # play stopping or moving: like a read that failed (_on_state), it keeps the last
        # look's.
        if state.systemd is None and state.tools and state.pipewire is None:
            self._aimed = spatial.aimed(state)
            playing = state.running and self._aimed is pipewire_headsets.A50
            if playing and not self._running:
                self._toast(DOLBY_TOAST)
            self._running = playing
            joined = spatial.joined_headset(state)
            self.outputs_row.set_visible(joined is not None)
            if joined is not None:
                headset = joined[0]
                self.outputs_row.set_subtitle(
                    f"{headset.joined_shown}, {pipewire_headsets.OUTSIDE_RULE}" if not headset.ruled
                    else f"{headset.joined_shown} {RESTART_WIREPLUMBER}"
                    if spatial.restart_needed(state, headset)
                    else f"{headset.joined_shown} {pipewire_headsets.UNTIL_RESTART}")
        # The switch, not state.enabled: a read whose systemctl failed leaves the switch
        # as the last look or a click since left it.
        self.dolby_row.set_visible(self.switch_row.get_active()
                                   and self._aimed is pipewire_headsets.A50)

    def _show_headsets(self, picked: str | None) -> None:
        """The Headset row lists the headsets plugged in at the last read that could see
        them, its list made again only when they change, so a read that changes nothing
        leaves the loading guard alone. It shows the one `picked` while that is plugged in,
        else The one connected first, where the sound goes, and the pick stays saved until
        another is made. The group shows once there is a choice to make, two headsets, or
        one made."""
        choices = headset_choices(self._present)
        if choices != self._headset_keys:
            self._headset_keys = choices
            self._loading.put_model(self.headset_row,
                                    Gtk.StringList.new(headset_labels(choices)))
        self._loading.put_selected(self.headset_row,
                                   choices.index(picked) if picked in choices else 0)
        self.headset_group.set_visible(len(self._present) >= 2 or picked is not None)

    def _set_sensitive(self) -> None:
        usable = (self.kind is not None and self.kind not in pipewire_client.LOCKED
                  and not self.busy)
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
                  failed: str, then: Callable[[], object] | None = None) -> None:
        if self._loading.active:
            return
        choice = choices[row.get_selected()]
        self._act(lambda: choose(choice, run=self._run), failed, then)

    def on_headset(self, row: Adw.ComboRow, pspec: GObject.ParamSpec) -> None:
        """A pick on the Headset row, among the choices its list shows now; its end tells
        `picked`."""
        self.on_choice(row, pspec, self._headset_keys, spatial.set_headset,
                       HEADSET_NOT_RESTARTED, self._picked)

    def _act(self, work: Callable[[], object], failed: str,
             then: Callable[[], object] | None = None) -> None:
        """Run `work` on the worker; `failed` is the toast line for a failure
        too long for a toast, other than a save that failed, and `then` is told at its end,
        after the read that follows (_acted)."""
        self.busy = True
        self._set_sensitive()
        self._worker.run(work, lambda result: self._acted(result, failed, then))

    def _acted(self, result: Result, failed: str, then: Callable[[], object] | None) -> None:
        self.busy = False
        self._set_sensitive()
        if isinstance(result, Exception):
            self._kept_up()
        if isinstance(result, OSError):
            # Only saving spatial.json raises one: systemctl's and PipeWire's
            # arrive as StepError. Nothing was saved, so nothing restarted or changed.
            toast_not_saved(self._toast, spatial.choices_path(), result)
        elif isinstance(result, Exception):
            # one_sentence: systemctl's and pw-cli's words can hold ". ", so
            # a first sentence could end inside them.
            toast_note(self._toast, failure_words(result, TITLE), failed, one_sentence=True)
        self.refresh()
        # Whatever the result: set_headset saves before the restart that can fail.
        if then is not None:
            then()
        self._follow_up.restart(FOLLOW_UP_MS)

    def copy(self, button: Gtk.Button, text: str) -> None:
        button.get_clipboard().set(text)
        self._toast(COPIED)
