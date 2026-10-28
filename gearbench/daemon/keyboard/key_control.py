"""The keyboard's custom assignments and Game Mode (keycontrol), on a keyboard whose feature
table lists them: its control list and the key ids of its G keys, whether its custom
assignments are on and the shortcut that turns them on and off, read at its setup and followed
by its event, each change posted as a notice, FN let go of as they go off; Game Mode as its
event last gave it; and on a keyboard with `assigned_keys`, the G keys bound in the layer in
force set to report to Gearbench and the rest left typing their own keys, or every G key on a
model with reports_unbound (TriggeredKeys), written at each connect and whenever that set
changes (tend). And on the G515 generation the host mode it is set to at each setup and
handed back from (take_host_mode, give_back_mode)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from ...hidpp import DeviceGone, HidppError, HidppFailure
from ...keyboard import (
    MODEL_OF as KEYBOARDS,
    assignments_untested,
    name as keyboard_name,
)
from ...keycontrol import (
    F_CONTROL_LIST,
    F_FILES,
    F_GAME_MODE,
    F_KEY_CONTROL,
    GAME_MODE_EVENT,
    GAME_MODE_UNKNOWN,
    SHORTCUT_TURNS,
    TOGGLE_EVENT,
    TriggeredKeys,
    key_ids_at,
    read_controls,
    read_enabled,
    read_host_mode,
    read_shortcut,
    shortcut_words,
    write_mode,
)
from ...report import SEND_REPORT
from ...wording import capitalized, word_list
from ..shared import failure_reason, unanswered

if TYPE_CHECKING:
    from ...hidpp import Event, Feature, Hidpp
    from ...keyboard import KeyboardModel
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: What the keys custom assignments set to report do while they are on, for a line.
RUN_BINDINGS = "run their bindings"


class KeyControl:
    """What the daemon holds of the keyboard's custom assignments and Game Mode."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: The G keys custom assignments set to report, on a keyboard with `assigned_keys` whose
        #: G keys' key ids are known.
        self.keys: TriggeredKeys | None = None
        #: Its control list, the control id at each key id (keycontrol.read_controls), and the
        #: key id of each of its G keys from G1 on (found_key_ids); None where not known.
        self.controls: tuple[int, ...] | None = None
        self.key_ids: tuple[int, ...] | None = None
        #: Whether its custom assignments are on, and the shortcut that turns them on and off,
        #: in words; None where it lacks 0x1B05 or did not say.
        self.enabled: bool | None = None
        self.shortcut: str | None = None
        #: Whether Game Mode is on, by its last event; None before one.
        self.game_mode: bool | None = None
        #: The feature indices of 0x1B05 and 0x4523, or None where it lacks one.
        self._control: int | None = None
        self._game: int | None = None
        #: The G keys of the last file the keyboard refused, which is not sent again until
        #: the keys to set change.
        self._refused: tuple[int, ...] | None = None
        #: The keys set to report by the file the journal last said custom assignments off
        #: for, or None: the same file written again, as at the link-up that follows FN+F1
        #: through a receiver, does not say it twice.
        self._off_said: tuple[int, ...] | None = None
        #: Whether 0x8101 fn 6 left the keyboard in host mode at the last setup it answered
        #: (take_host_mode), which its hand-back undoes; None until one answers after its
        #: connect. And whether a refusal of it has been said, so a repeat is not.
        self.host_mode: bool | None = None
        self._mode_refusal_said = False

    def set_up(self, controls: tuple[int, ...] | None = None) -> None:
        """At the keyboard's setup, after its first paint, which reads none of it, on its open
        node: find both features, read its control list, or take `controls`, the list its last
        connect read off the same device (Known.recall), and from it its G keys' key ids,
        whether its custom assignments are on and their shortcut, named by the list, and with
        `assigned_keys` build the keys they set, whose first file tend writes. A read the
        keyboard refuses leaves what it reads not known, but for the shortcut's, which keeps
        whether they are on as the read before it gave; one it does not answer, or its
        receiver cannot reach it for (HidppError.unreachable), raises into the setup's
        guard."""
        dev = cast("Hidpp", self.keyboard.dev)
        control, game = dev.feature(F_KEY_CONTROL), dev.feature(F_GAME_MODE)
        self._control = None if control is None else control.index
        self._game = None if game is None else game.index
        self.controls = self.read_controls(dev) if controls is None else controls
        self.key_ids = self.found_key_ids()
        if "assigned_keys" in self.keyboard.keyboard_able:
            self.keys = self.triggered_keys(dev)
        if control is None:
            return
        try:
            self.enabled = read_enabled(dev, control)
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the %s did not say whether its custom assignments are on: %s",
                     self.name(), failure_reason(exc))
            return
        try:
            pair = read_shortcut(dev, control)
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the %s did not say which shortcut turns its custom assignments on and "
                     "off: %s", self.name(), failure_reason(exc))
            return
        self.shortcut = None if pair is None else shortcut_words(pair, self.controls or ())

    def read_controls(self, dev: Hidpp) -> tuple[int, ...] | None:
        """The keyboard's control list (keycontrol.read_controls), or None where its table
        lacks 0x1B10 or it refused the list, which is said."""
        listed = dev.feature(F_CONTROL_LIST)
        if listed is None:
            return None
        try:
            return read_controls(lambda function, params: dev.request(listed.index, function,
                                                                      params))
        except HidppError as exc:
            if exc.unreachable:
                raise
            model = self.model()
            log.info("the %s did not give its control list (%s), so %s", self.name(),
                     failure_reason(exc),
                     f"its {model.own_key}s are named by the key ids its test found"
                     if model.key_ids else f"its {model.own_key}s' key ids are not known")
            return None

    def found_key_ids(self) -> tuple[int, ...] | None:
        """The key id of each of the model's G keys, from G1 on: the key ids the control list
        holds their control_ids at (keycontrol.key_ids_at), said at debug, where they match
        the model's measured key_ids or it has none; the measured ones where the list was not
        read or disagrees, which is said (say_mismatch); None where neither gives each."""
        model = self.model()
        found = (None if self.controls is None or not model.control_ids
                 else key_ids_at(self.controls, model.control_ids))
        if found is None or (model.key_ids and found != model.key_ids):
            if found is not None:
                self.say_mismatch(found)
            return model.key_ids or None
        missing = [name for name, key_id in zip(model.g_key_names, found, strict=True)
                   if key_id is None]
        if missing:
            log.debug("the %s's control list holds no key for %s", self.name(),
                      word_list(missing, "and"))
            return None
        key_ids = tuple(key_id for key_id in found if key_id is not None)
        log.debug("the %s's control list gives %s to %s key ids %s", self.name(),
                  model.g_key_names[0], model.g_key_names[-1],
                  " ".join(f"{key_id:#04x}" for key_id in key_ids))
        return key_ids

    def say_mismatch(self, found: tuple[int | None, ...]) -> None:
        """The control list read off the keyboard, `found` by control id, contradicts the key
        ids its model's test found: said, and the tested ones kept."""
        model = self.model()
        differ = [(f"{name} {'at no key id' if read is None else f'at key id {read:#04x}'}",
                   f"{tested:#04x}")
                  for name, read, tested in zip(model.g_key_names, found, model.key_ids,
                                                strict=True) if read != tested]
        log.warning("the %s's control list puts %s, where its test found %s; its custom "
                    "assignments name its %ss by the key ids its test found: %s", self.name(),
                    word_list([put for put, _tested in differ], "and"),
                    word_list([tested for _put, tested in differ], "and"), model.own_key,
                    SEND_REPORT)

    def triggered_keys(self, dev: Hidpp) -> TriggeredKeys | None:
        """The keys custom assignments set, on a keyboard with `assigned_keys`, by the G keys'
        key ids (found_key_ids); None where they are not known, which is said: no file can
        name them, so the G keys take no binding."""
        model = self.model()
        if self.key_ids is None:
            log.warning("the key ids of the %s's %ss are not known, so they %s, bound or not: "
                        "%s", self.name(), model.own_key, model.unbound, SEND_REPORT)
            return None
        return TriggeredKeys(dev, self.key_ids, model.trigger_numbers)

    def forget(self) -> None:
        """At the keyboard's teardown: nothing known of it any more."""
        self.keys = self.enabled = self.shortcut = self.game_mode = self.host_mode = None
        self.controls = self.key_ids = None
        self._control = self._game = self._refused = self._off_said = None

    def lost_power(self) -> None:
        """The keyboard was switched off and on (Link.reconfigure_keyboard): its file is gone,
        so tend writes it again, and Game Mode is not known until its next event."""
        if self.keys is not None:
            self.keys.assigned = None
        self.game_mode = None

    def model(self) -> KeyboardModel:
        # keyboard_product is set at the start of Keyboard.set_up, before it opens the keyboard,
        # and never cleared. Nothing checks the cast.
        return KEYBOARDS[cast("int", self.keyboard.keyboard_product)]

    # -- host mode -------------------------------------------------------------

    def files(self) -> Feature | None:
        """0x8101 on a keyboard of the G515 generation (KeyboardModel.custom_assignments) that
        lists it, or None. The keyboard is open."""
        return (cast("Hidpp", self.keyboard.dev).feature(F_FILES)
                if self.model().custom_assignments else None)

    def take_host_mode(self) -> None:
        """Host mode, 0x8101 fn 6 `05`, on a keyboard of the G515 generation, at each setup
        after the claim (Link.take_over), so before the BASE layer's file, as G HUB's takeover
        of the G515 TKL sends it (capture A2) and its connects to the G915 X LIGHTSPEED (Windows
        captures G5b and G5d): the owner's decision of 2026-10-23, "I want host mode default
        until we know more, we will not drop it." It is read first and written only where the
        keyboard is in another mode: it powers up in the on-board mode, as the G915 X LIGHTSPEED
        read after a power cycle (Windows capture G5c), and the G915 X LIGHTSPEED kept host mode
        across a link change (2026-10-23). A refusal is said once and the keyboard runs on in
        the mode it is in: a refused read keeps what the last answer found, for the hand-back,
        and a refused write leaves the mode it read. No answer, or its receiver unable to reach
        it, raises into the setup's guard."""
        files = self.files()
        if files is None:
            return
        dev = cast("Hidpp", self.keyboard.dev)
        try:
            if read_host_mode(dev, files):
                self.host_mode = True
                log.debug("the %s is in host mode already", self.name())
                return
            # Read in another mode: a refused write leaves it there.
            self.host_mode = False
            self.host_mode = write_mode(dev, files, host=True)
        except HidppError as exc:
            if exc.unreachable:
                raise
            if not self._mode_refusal_said:
                self._mode_refusal_said = True
                log.info("the %s refused host mode (%s); it runs on in the mode it is in",
                         self.name(), failure_reason(exc))
            return
        if self.host_mode:
            log.info("the %s set to host mode", self.name())
        else:
            log.warning("the %s answered host mode with another mode; it runs on in that one",
                        self.name())

    def give_back_mode(self) -> None:
        """The on-board mode, 0x8101 fn 6 `0f`, at the hand-back of a keyboard take_host_mode
        left in host mode (Keyboard.teardown_steps), as a 0x8100 keyboard gets its ONBOARD
        mode back, but before the lighting claim's release. A failure goes to the caller, a
        reply that still says host mode among them."""
        if write_mode(cast("Hidpp", self.keyboard.dev), cast("Feature", self.files()),
                      host=False):
            raise HidppFailure(f"the {self.name()} answered with host mode")
        self.host_mode = False

    def name(self) -> str:
        return keyboard_name(self.keyboard.keyboard_product)

    # -- the keys set to report ---------------------------------------------

    def wanted(self) -> tuple[int, ...]:
        """The numbers of the G keys to set to report: each bound in the layer in force, on
        its base layer alone, since the G515 generation has no MR key, or every one on a model
        with reports_unbound, where Bindings.on_key drops an unbound one's press, so a bind
        changes nothing to write. None of them while Gearbench cannot type
        (OnboardHold.untyped), so the keys type their own for whoever has the seat."""
        if self.keyboard.hold.untyped() is not None:
            return ()
        model = self.model()
        return tuple(number for number, key in enumerate(model.g_key_names, 1)
                     if model.reports_unbound
                     or self.d.games.active_binding(key, False) is not None)

    def tend(self) -> None:
        """Write the BASE layer's file where the keys to set (wanted) differ from those the
        file written last sets, whatever the keyboard holds at its connect. Not on a keyboard
        marked not answering, which gets it once it answers, nor a file it refused again. A file
        written lets go of what the keyboard's keys held, as its power going does, since their
        releases do not come (TriggeredKeys.assign)."""
        keys = self.keys
        if keys is None or not self.keyboard.keyboard_answering():
            return
        wanted = self.wanted()
        if wanted in (keys.assigned, self._refused):
            return
        try:
            keys.assign(wanted)
        except DeviceGone as exc:
            log.info("the %s went away while its %ss were set up: %s", self.name(),
                     self.model().own_key, failure_reason(exc))
            self.keyboard.teardown_device()
            return
        except HidppFailure as exc:
            self.not_written(exc, wanted)
            return
        self.d.bindings.keyboard_gone(held_only=True, source=self.keyboard)
        self._refused = None
        self.keyboard.mark_keyboard_answering()
        self.say_written(wanted)

    def not_written(self, exc: HidppFailure, wanted: tuple[int, ...]) -> None:
        """A file the keyboard did not take: one nothing answered is sent again once it
        answers (Keyboard.keyboard_unanswered), one the kernel failed outside
        hidpp.GONE_ERRNOS marks it not answering as well, and one it refused is not sent again
        until the keys to set change."""
        if unanswered(exc):
            self.keyboard.keyboard_unanswered(exc)
        elif isinstance(exc, HidppError):
            self._refused = wanted
            model = self.model()
            log.warning("the %s refused its custom assignments (%s), so its %ss %s, bound or "
                        "not", self.name(), failure_reason(exc), model.own_key, model.unbound)
        else:
            self.keyboard.mark_keyboard_silent(str(exc))

    def say_written(self, wanted: tuple[int, ...]) -> None:
        """The journal's line for a file written: at info for one that sets keys, every one on
        a model with reports_unbound, with a word on custom assignments turned off unless the
        journal said it last for the same keys (_off_said), at debug for one that sets none."""
        model = self.model()
        if not wanted:
            log.debug("the %s's %ss all %s", self.name(), model.own_key, model.unbound)
            return
        if model.reports_unbound:
            log.info("the %s's custom assignments set every %s to report to Gearbench; an "
                     "unbound one does nothing", self.name(), model.own_key)
        elif len(wanted) == len(model.g_key_names):
            log.info("the %s's custom assignments set every %s to report to Gearbench",
                     self.name(), model.own_key)
        else:
            names = [model.g_key_names[number - 1] for number in wanted]
            log.info("the %s's custom assignments set %s to report to Gearbench; its other %ss "
                     "%s", self.name(), word_list(names, "and"), model.own_key, model.unbound)
        if self.enabled is False and wanted != self._off_said:
            self._off_said = wanted
            log.info("%s", self.toggle_words())

    def owes_restore(self) -> bool:
        """Whether the keyboard's stop writes an empty BASE layer (restore): where the file
        written last set a key, or where what it holds is not known."""
        return self.keys is not None and self.keys.assigned != ()

    def restore(self) -> None:
        """An empty BASE layer, so each key does its own job again once Gearbench stops."""
        cast("TriggeredKeys", self.keys).assign(())

    # -- events and status ---------------------------------------------------

    def take_event(self, event: Event) -> bool:
        """Whether `event` was the keyboard's custom assignments turned on or off by their
        shortcut, or Game Mode turned on or off, each 01 on and 00 off, now kept and said; the
        assignments turned to a state other than the one kept post their notice
        (Session.assignments_notice), and turned off let go of FN (fn_unreported) and count
        as the off line said for the file in force (_off_said). Gearbench never turns them,
        and their state read at a setup posts none."""
        if event.address == TOGGLE_EVENT and event.feature_index == self._control:
            before, self.enabled = self.enabled, event.data[:1] == b"\x01"
            log.info("%s", self.toggle_words())
            if self.enabled != before:
                self.d.session.assignments_notice.post(self.name(), self.enabled,
                                                       self.notice_line())
            if not self.enabled:
                self._off_said = None if self.keys is None else self.keys.assigned
                self.fn_unreported()
            return True
        if event.address == GAME_MODE_EVENT and event.feature_index == self._game:
            self.game_mode = event.data[:1] == b"\x01"
            log.info("the %s's Game Mode is %s", self.name(), "on" if self.game_mode else "off")
            return True
        return False

    def fn_unreported(self) -> None:
        """FN held taken as let go (Modifiers.let_go), with its release's frame
        (Keyboard.take_fn), as the custom assignments go off, since a keyboard is taken to
        report FN only while they are on: the owner's decision of 2026-10-23, "All four
        lessons", after the G915 X LIGHTSPEED's FN keys stayed white. Nothing where FN is not
        held or not followed."""
        modifiers = self.keyboard.modifiers
        if modifiers is not None and modifiers.let_go():
            log.debug("FN taken as let go: the %s sends it only while its custom assignments "
                      "are on", self.name())
            self.keyboard.take_fn(False)

    def toggle_words(self) -> str:
        """Whether the keyboard's custom assignments are on, for the journal: off, with what
        that does to the keys set to report (keys_until)."""
        if self.enabled:
            return f"the {self.name()}'s custom assignments are on"
        said = f"the {self.name()}'s custom assignments are off"
        keys = self.keys_until()
        return said if keys is None else f"{said}, so {keys}"

    def notice_line(self) -> str:
        """The line of their notice (Session.assignments_notice): what the keys set to report
        do until the shortcut turns the custom assignments the other way (keys_until), or that
        shortcut alone where turning them changes no key."""
        return capitalized(self.keys_until() or self.turns())

    def keys_until(self) -> str | None:
        """What the keys the file written last sets to report do while the custom assignments
        are as they are, until their shortcut turns them the other way: all of them on a model
        with reports_unbound, else the bound ones, run their bindings while on and do as the
        model's `unbound` says while off. None where that file sets none or none was written."""
        if self.keys is None or not self.keys.assigned:
            return None
        model = self.model()
        keys = f"{model.own_key}s" if model.reports_unbound else f"bound {model.own_key}s"
        return f"its {keys} {RUN_BINDINGS if self.enabled else model.unbound} until {self.turns()}"

    def turns(self) -> str:
        """The shortcut that turns the custom assignments the other way, in SHORTCUT_TURNS'
        words: "FN+F1 turns them on" while off."""
        return SHORTCUT_TURNS.format(self.shortcut or "its shortcut",
                                     "off" if self.enabled else "on")

    def status(self) -> dict:
        """The keyboard's custom assignments and Game Mode for `status`: on or off and their
        shortcut, None where not known; the names of the keys set to report; whether its G keys
        take no binding until their write is tested (keyboard.assignments_untested); and Game
        Mode, "on", "off" or GAME_MODE_UNKNOWN, None on a keyboard without it."""
        set_up = () if self.keys is None or self.keys.assigned is None else self.keys.assigned
        game = None if self._game is None else (
            GAME_MODE_UNKNOWN if self.game_mode is None else "on" if self.game_mode else "off")
        return {
            "custom_assignments": self.enabled,
            "assignment_shortcut": self.shortcut,
            "reporting_keys": [self.model().g_key_names[number - 1] for number in set_up],
            "assignments_untested": (self.keyboard.dev is not None
                                     and assignments_untested(self.keyboard.keyboard_product)),
            "game_mode": game,
        }
