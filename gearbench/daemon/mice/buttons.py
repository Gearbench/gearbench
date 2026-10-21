"""The mice's buttons and wheel tilt: G4 to G9 as a mouse's event node or its button spy's
masks report them, through one gate to the bindings (`button_edge`); a held tilt's notches
on the virtual mouse; the evdev readers; the keystrokes a keyboard paired to the same
receiver leaves on a mouse's event node; and when the buttons next need the loop (`deadlines`).
Nothing here writes to a mouse."""

from __future__ import annotations

import logging
import selectors
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ...evdev import EV_KEY, EventReader, find_event_nodes, group_denied
from ...hidpp import VENDOR_ID as MOUSE_VENDOR, DeviceGone
from ...mouse import BUTTON_CODES
from ...spy import decode_mask, edges, tilt as spy_tilt
from ...uinput import EV_REL, UinputError
from ...wording import reason
from .attached import MOUSE_RETRY, AttachedMouse

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

#: Seconds between the notches of a held wheel tilt in HOST mode, the firmware's own.
TILT_REPEAT = 0.1

#: The key codes of a mouse's buttons on its event node, BTN_MOUSE + n - 1 for buttons 1 to
#: 16, BUTTON_CODES among them. In HOST mode no onboard profile runs to send a keystroke, so
#: any other key code there is from a keyboard paired to the mouse's receiver.
MOUSE_BUTTON_CODES = range(0x110, 0x120)

#: The end of saw_keystroke's warning and of KEYSTROKES, so the two give one wording.
CARRIES = ("carries keystrokes from a keyboard paired to its receiver, readable by the "
           "gearbench group; pair that keyboard to a receiver of its own")
#: `problems`' mouse_keystrokes (keystroke_problems).
KEYSTROKES = "the {name}'s event node " + CARRIES


class Buttons:
    """What the buttons and wheel tilt of every attached mouse do, and the event nodes they
    are read from."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice
        #: When the event nodes of the mice that want one are next tried, monotonic.
        self.input_retry = 0.0
        #: Event nodes the gearbench group would let the daemon read, for which a permission
        #: refusal was logged at warning, so the retry says it once.
        self._input_denied_said: set[str] = set()

    def button_edge(self, attached: AttachedMouse, button: str, pressed: bool) -> None:
        """A press or release of a G button, from the evdev node or the spy's mask, through one
        gate per mouse: a press of a button already down is dropped, and a release of one not
        down, so a press that arrives both ways acts once."""
        if (button in attached.buttons_down) == pressed:
            log.debug("mouse %s already %s; dropped", button,
                      "down" if pressed else "up")
            return
        if pressed:
            attached.buttons_down.add(button)
        else:
            attached.buttons_down.discard(button)
        self.d.bindings.on_mouse_button(button, pressed, attached)

    def take_spy_mask(self, attached: AttachedMouse, data: bytes) -> None:
        """A mask from the mouse's spy: the presses and releases since the
        last one (spy.edges, releases first), each through the gate."""
        new = decode_mask(data)
        old, attached.spy_mask = attached.spy_mask, new
        model = attached.its_spy.model
        for button, pressed in edges(old, new, model):
            self.button_edge(attached, button, pressed)
            if attached not in self.mice.attached:
                return          # a binding's DPI write dropped it
        self.follow_tilt(attached, spy_tilt(new, model))

    def follow_tilt(self, attached: AttachedMouse, way: int) -> None:
        """A wheel tilt from the spy's mask in HOST mode, where it scrolls nothing by itself:
        -1 left, +1 right, 0 let go. A new tilt scrolls one notch at once and one more every
        TILT_REPEAT while it is held (tend_tilts), as the firmware does in ONBOARD mode. A
        fixed action, not a binding: tilts are not bindable (the owner's decision of
        2026-09-25)."""
        if way == attached.tilt:
            return
        if way and self.d.session.why_inactive() is not None:
            # The virtual mouse would scroll the session that has the seat now.
            return
        attached.tilt, attached.tilt_at = way, None
        if way:
            self.tilt_step(attached, time.monotonic())

    def tilt_step(self, attached: AttachedMouse, now: float) -> None:
        """One notch of the held tilt, and when the next is due: TILT_REPEAT after this one's
        time, or after now for a pass that came late, so a slow loop drops notches instead of
        sending a burst. Without a virtual mouse nothing scrolls; one that fails lets go of the
        tilt."""
        due = (attached.tilt_at or now) + TILT_REPEAT
        attached.tilt_at = due if due > now else now + TILT_REPEAT
        if self.d.ptr is None:
            log.debug("wheel tilt ignored: no virtual mouse")
            return
        try:
            self.d.ptr.hwheel(attached.tilt)
        except UinputError as exc:
            log.warning("%s: the wheel tilt could not scroll: %s", attached.name, reason(exc))
            attached.tilt, attached.tilt_at = 0, None

    def deadlines(self) -> list[float]:
        """When the buttons next need the loop: each held tilt's next notch (tend_tilts), and
        the evdev retry while a mouse wants a reader (open_mouse_inputs)."""
        due = [m.tilt_at for m in self.mice.attached if m.tilt_at is not None]
        if any(self.wants_input(m) for m in self.mice.attached):
            due.append(self.input_retry)
        return due

    def tend_tilts(self) -> None:
        """Once a pass: the next notch of every held tilt that is due."""
        now = time.monotonic()
        for attached in self.mice.attached:
            if attached.tilt_at is not None and now >= attached.tilt_at:
                self.tilt_step(attached, now)

    def stop_tilts(self, why: str) -> None:
        """Let go of every held tilt, when a macro is stopped too (Session.stop_away). The
        release may come after, to a mask that holds none."""
        for attached in self.mice.attached:
            if attached.tilt:
                log.info("%s: wheel tilt let go: %s", attached.name, why)
                attached.tilt, attached.tilt_at = 0, None

    def drop_spy_state(self, attached: AttachedMouse) -> int | None:
        """Let go of what a mouse's buttons hold where a release could be lost, and answer the
        DPI under a held shift, which it forgets. Nothing is written to the mouse, which may
        not answer: a setup writes the remembered DPI. What another mouse's buttons hold stays:
        the bindings keep which mouse pressed each (Bindings.on_mouse_button)."""
        held = attached.dpi_before_shift
        self.d.bindings.stop_mouse_macro(attached)
        self.d.bindings.release_mouse_holds(attached)
        attached.dpi_before_shift = None
        attached.buttons_down.clear()
        attached.spy_mask = 0
        attached.tilt, attached.tilt_at = 0, None
        return held

    def saw_keystroke(self, attached: AttachedMouse, path: Path | str) -> None:
        """A key code no mouse button sends (MOUSE_BUTTON_CODES), on the event node of a mouse
        the daemon runs in HOST mode (runs_host), where no profile runs to send one: warned of
        once per attach, and in `problems` from then on. The key is not logged: it is someone's
        typing."""
        if attached.keystrokes:
            return
        attached.keystrokes = True
        log.warning("%s's event node %s %s", attached.name, path, CARRIES)

    def keystroke_problems(self) -> list[str]:
        """`problems`' mouse_keystrokes, one for each mouse saw_keystroke has warned of."""
        return [KEYSTROKES.format(name=m.name) for m in self.mice.attached if m.keystrokes]

    # -- the evdev readers ---------------------------------------------------

    def wants_input(self, attached: AttachedMouse) -> bool:
        """Whether this mouse has buttons the daemon reads on its event node
        (ButtonTable.event_node) and no reader open. Another is never opened: its node is not
        granted, as the G305's is not."""
        table = attached.mouse.button_table
        return table is not None and table.event_node and not attached.input

    def open_mouse_input(self, found: AttachedMouse) -> None:
        """Open one mouse's evdev nodes; never fatal."""
        if found not in self.mice.attached or not self.wants_input(found):
            return
        denied = None
        for path in find_event_nodes(MOUSE_VENDOR, found.product):
            try:
                reader = EventReader(path)
            except PermissionError:
                why = group_denied(path)
                if why is None:
                    # Cabled, the mouse's own keyboard collection (interface 01) is not granted
                    # by the udev rule, by design.
                    log.debug("no access to the mouse's %s, which is by design", path)
                    continue
                denied = f"{path}, since {why}"
                if str(path) not in self._input_denied_said:
                    self._input_denied_said.add(str(path))
                    log.warning("cannot read the %s's %s, since %s; with host mode off, G4 to G9 "
                                "are read there", found.name, path, why)
                continue
            except OSError as exc:
                log.warning("cannot read the mouse's %s: %s", path, reason(exc))
                continue
            self._input_denied_said.discard(str(path))
            self.d.selector.register(reader.fileno, selectors.EVENT_READ,
                                   ("mouse-input", reader, found))
            found.input.append(reader)
            log.info("reading mouse buttons from %s", path)
        found.input_denied = None if found.input else denied

    def open_mouse_inputs(self) -> None:
        """Retry the evdev nodes of every mouse that wants them, once due."""
        if time.monotonic() < self.input_retry:
            return
        self.input_retry = time.monotonic() + MOUSE_RETRY
        for attached in list(self.mice.attached):
            self.open_mouse_input(attached)

    def close_mouse_input(self, found: AttachedMouse) -> int | None:
        """Close one mouse's evdev nodes, and let go of what its buttons started; answers the
        DPI under a held shift (drop_spy_state)."""
        held = self.drop_spy_state(found)
        for reader in found.input:
            self.d.selector.unregister(reader.fileno)
            reader.close()
        found.input = []
        return held

    def pump_mouse_input(self, reader: EventReader, source: AttachedMouse) -> None:
        """Turn what one mouse evdev node has buffered into button actions for `source`.

        A read error closes all of the mouse's nodes, since they are one device. EBADF is
        caught on purpose: a select batch is computed before any handler runs, so a second node
        in it can be read after this closed it.
        """
        try:
            events = reader.read()
        except DeviceGone:
            log.info("mouse input %s went away; will look for it again", reader.path)
            self.close_mouse_input(source)
            return
        except OSError as exc:
            log.info("mouse input %s failed (%s); will look for it again", reader.path,
                     reason(exc))
            self.close_mouse_input(source)
            return
        # In HOST mode a key code no mouse button sends is a keyboard's on the receiver
        # (saw_keystroke): it says nothing of whether the mouse is on.
        if events and (not self.mice.runs_host(source) or any(
                type_ == EV_REL or (type_ == EV_KEY and code in MOUSE_BUTTON_CODES)
                for type_, code, _value in events)):
            self.mice.mark_answering(source)         # its own button or motion, so it is on
        for type_, code, value in events:
            if source not in self.mice.attached:
                # An earlier event in this batch dropped the mouse: the rest would act on a
                # closed handle.
                break
            if type_ == EV_KEY and code not in MOUSE_BUTTON_CODES and self.mice.runs_host(source):
                self.saw_keystroke(source, reader.path)
            if type_ != EV_KEY or code not in BUTTON_CODES or value == 2:
                continue        # value 2 is autorepeat
            self.button_edge(source, BUTTON_CODES[code], bool(value))
