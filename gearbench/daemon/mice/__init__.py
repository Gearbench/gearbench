"""The mice's controller, `Mice`: attaching, `mouse_call`, silence and rechecks, HOST mode's
arm and hand-back, the teardown, and the commands no part gives. The parts Mice.__init__
builds do the rest; attached.py holds the attached-mouse record."""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from ...battery import F_UNIFIED
from ...hidpp import (
    DeviceGone,
    Event,
    Hidpp,
    HidppError,
    HidppFailure,
)
from ...mouse import (
    CABLED_IDS as MOUSE_CABLED,
    COOKIE_MODELS,
    PROFILE_SECTOR,
    WHEEL_HIRES,
    MouseError,
    SwitchRefused,
    find_attached_mice,
    host_why,
    not_taken_words,
    other_profile_words,
    profile_words,
    runs_another_profile,
    stuck_in_host,
)
from ...mouse_device import Mouse
from ...onboard import F_ONBOARD, MODE_HOST, MODE_NAMES, MODE_ONBOARD, stage_dpi
from ...spy import G_HUB_TABLE, SPY_MODELS, ButtonSpy
from ..shared import (
    F_WIRELESS,
    MOUSE_PROBE,
    MOUSE_SILENT_RECHECK,
    REAPPLY_INTERVAL,
    failure_reason,
    hidpp_refusal,
    kept_pick,
    look_due,
    name_text,
    switch_value,
    unanswered,
    with_note,
)
from .attached import (
    MOUSE_POLL_RETRY,
    MOUSE_RETRY,
    MOUSE_RETRY_MAX,
    AttachedMouse,
    KeptMouse,
    asked,
    dpi_keys,
    named_in,
    other_route,
)
from .battery import MouseBatteries
from .buttons import Buttons
from .color import MouseLighting
from .dpi import Dpi
from .flash import ProfileWrites
from .reply import MouseReply
from .routes import Routes
from .unattached import Unattached

if TYPE_CHECKING:
    from .. import Daemon

log = logging.getLogger("gearbench")


#: 0x8100 event 1, the stage the mouse's own DPI button moved it to in ONBOARD mode: its first
#: byte is the 0-based slot in the running profile's stage table. Seen on a G305 (OpenLogi issue
#: 1194); Solaar's notifications.py looks the slot up in the profile getCurrentProfile names.
STAGE_EVENT = 0x10

#: 0x0020 ConfigChange: fn 0 reads a two-byte configuration cookie, fn 1 sets it. A mouse that
#: answers its recheck and still holds Gearbench's cookie kept its setup (reapply_mouse).
F_CONFIG_CHANGE = 0x0020

#: Gearbench's cookie: not `00 00`, and not one G HUB sets; a new value would need its own
#: hardware check. Set, never cleared: `00 00` resets the lighting control and the kernel's
#: high-resolution wheel mode.
MOUSE_COOKIE = b"lc"
COOKIE_GET, COOKIE_SET = 0, 1

#: What brings reapply_mouse: a power-on report, a recheck answered, a setup owed (`cut_short`)
#: or a link back; each with its words for a setup that did not finish and for one that did.
type Occasion = Literal["power-on", "recheck", "unfinished", "linked"]
REAPPLY_WORDS: dict[Occasion, tuple[str, str]] = {
    "power-on": ("after power-on", "configured again after power-on"),
    "recheck": ("again after its recheck", "answered its recheck; set up again"),
    "unfinished": ("again", "set up again"),
    "linked": ("again after its link came back", "linked again; set up again"),
}


class Mice:
    """Every attached mouse and what the daemon holds for it."""

    # -- the controller ------------------------------------------------------

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self._mouse_retry = 0.0
        self._mouse_backoff = MOUSE_RETRY
        #: By model (Mouse.backup_name, shared by cable and receiver), what a mouse torn down
        #: leaves for the new AttachedMouse that a replug or a move between its routes builds.
        self._mouse_kept: dict[str, KeptMouse] = {}
        #: By path, the handle and find_attached_mice entry of each mouse node kept open after its
        #: probe went unanswered (keep_waiting, pump_waiting).
        self._mouse_waiting: dict[str, tuple[Hidpp, tuple[Path, int, int]]] = {}
        #: Until when a kept node's motion or late reply does not bring the retry forward again
        #: (pump_waiting), monotonic.
        self._mouse_nudged = 0.0
        #: Every attached mouse, in the order found. Each carries the product id it reports,
        #: which differs by route, and the evdev lookup uses that one.
        self.attached: list[AttachedMouse] = []
        #: The product id of the mouse chosen to show, or None for the first attached.
        #: `current` finds it on either route (named_in), and falls back to the first attached
        #: while no mouse of its model is, the pick kept for when it is back; config.json keeps
        #: the last one picked (mouse_select) for the next start.
        self.selected = kept_pick(daemon.config.mouse_selected)
        #: Models (Mouse.backup_name) whose last teardown found them counted in HOST mode, until
        #: an arm's mode read answers or a profile read finds one running (run_profile_one):
        #: found there, this run left it so (_mode_before_arm).
        self._left_in_host: set[str] = set()
        #: The parts, each built with this controller, which it keeps as `mice`, and the
        #: daemon where it reads it.
        self.reply = MouseReply(daemon, self)
        self.buttons = Buttons(daemon, self)
        self.color = MouseLighting(daemon, self)
        self.flash = ProfileWrites(daemon, self)
        self.dpi = Dpi(daemon, self)
        self.battery = MouseBatteries(daemon, self)
        self.routes = Routes(daemon, self)
        self.unattached = Unattached(self)

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            **self.reply.commands(),
            **self.dpi.commands(),
            **self.flash.commands(),
            "mouse_select": self._cmd_mouse_select,
            **self.color.commands(),
            "mouse_host_mode": self._cmd_mouse_host_mode,
        }

    def deadlines(self) -> list[float]:
        """When the mice next need the loop; the attach retry always counts, since another mouse
        can arrive while one is attached."""
        due = [m.recheck_at for m in self.attached if m.silent and not m.link_down]
        due += [m.reapplied_at + REAPPLY_INTERVAL for m in self.attached
                if m.cut_short and not m.silent]
        due += [m.repaint_at for m in self.attached if m.repaint_at is not None]
        due += [m.sent.deadline for m in self.attached if m.sent is not None]
        # What a request or a missed probe filed in a mouse's node, or in a kept one
        # (keep_waiting), does not wake select: due now.
        nodes = [*(m.dev for m in self.attached), *(dev for dev, _ in self._mouse_waiting.values())]
        due += [self._mouse_retry, *(0.0 for dev in nodes if dev.pending)]
        return due + self.buttons.deadlines() + self.battery.deadlines() + self.routes.deadlines()

    @property
    def current(self) -> AttachedMouse | None:
        """The mouse commands act on: the selected one, found as a request's `device` finds
        its mouse (named_in), so on its other route too, else the first attached; None when
        none is attached."""
        picked = None if self.selected is None else named_in(self.attached, f"{self.selected:04x}")
        return picked if picked is not None else next(iter(self.attached), None)

    # -- attaching -----------------------------------------------------------

    def connect_mouse(self, found: tuple[Path, int, int]) -> bool:
        """Attach the mouse `found` names. Never fatal.

        A receiver keeps its node while its mouse is off, so one cheap probe decides; a node
        whose mouse does not answer is kept open (keep_waiting) and used again here.

        Every request to the mouse before its handles are published is inside a guard, the
        lighting claim among them (claim_at_attach). What follows can tear the mouse down again,
        so the answer is whether it is attached at the end.
        """
        # The device index comes from the topology, 0x01 on a receiver and 0xFF on a cable: a
        # wrong one discards every reply as someone else's.
        path, product, index = found
        started = time.monotonic()
        kept = self._mouse_waiting.pop(str(path), None)
        try:
            if kept is not None:
                # Registered by keep_waiting, and only while it is kept.
                dev = kept[0]
                self.d.selector.unregister(dev.fileno)
            else:
                dev = Hidpp(path, device_index=index)
        except PermissionError as exc:
            self.unattached.open_refused(path, exc)
            return False
        except OSError as exc:
            log.debug("mouse not reachable on %s: %s", path, exc)
            return False
        self.unattached.opened(path)
        try:
            # The probe answers 0x1004's index: where battery reports come from.
            probed = dev.lookup(F_UNIFIED, **MOUSE_PROBE)
        except HidppFailure as exc:
            log.debug("mouse on %s did not answer the probe: %s", path, exc)
            if isinstance(exc, DeviceGone):
                dev.close()
            else:
                self.keep_waiting(dev, found)
            self.d.note_slow(started, f"mouse connect on {path}", "no answer to the probe",
                           repeats=str(path))
            return False
        try:
            mouse = Mouse(dev, product=product)
            wireless = dev.feature(F_WIRELESS)
            onboard = dev.feature(F_ONBOARD)
            config_change = dev.feature(F_CONFIG_CHANGE) if product in COOKIE_MODELS else None
            spy = self.find_spy(mouse)
            host = self.d.config.mouse_host_mode and spy is not None
            lighting = self.color.find_lighting(mouse)
            if not host:
                # Inside the guard: a mouse asleep since the probe would otherwise raise out
                # of the retry and end the daemon. In HOST mode it comes after the arm.
                self.color.claim_at_attach(mouse, lighting)
        except (OSError, HidppFailure, RuntimeError, IndexError) as exc:
            # IndexError is a reply cut short, behind the transport's own length check.
            why = self.unattached.setup_refused(path, product, exc)
            dev.close()
            self.d.note_slow(started, f"mouse connect on {path}", f"could not set it up: {why}",
                           repeats=str(path))
            return False
        self.unattached.set_up(path)
        left = self.routes.left_route(product, path)
        inherited = self._mouse_kept.pop(mouse.backup_name, KeptMouse())
        now = time.monotonic()
        attached = AttachedMouse(dev=dev, mouse=mouse, lighting=lighting,
                                 product=product,
                                 wireless=wireless.index if wireless else None,
                                 onboard=onboard.index if onboard else None,
                                 config_change=config_change.index if config_change else None,
                                 battery_index=probed.index if probed else None,
                                 spy=spy, **vars(inherited),
                                 # It has just answered the probe: awake, and set up from here.
                                 heard_at=now, reapplied_at=now)
        self.attached.append(attached)
        # Read for what the mouse says unprompted, its power-on report among it.
        self.d.selector.register(dev.fileno, selectors.EVENT_READ, ("mouse-hid", attached))
        if self.selected is None:
            self.selected = product
        log.info("%s", attached.connected_words(path))
        if host:
            # The evdev node too: it carries G4 to G9 until the arm lands, and whenever it
            # does not take.
            took, _lit = self.setup_host(attached, moved=bool(left))
            self.log_profile_one(mouse)
            self.buttons.open_mouse_input(attached)
        else:
            took = self._setup_onboard(attached)
        if took:
            self.set_cookie(attached)
        attached.cut_short = attached.silent and not took
        self.keep_wheel(attached)
        self.d.forget_slow(str(path))
        self.d.note_slow(started, f"{mouse.name} connect on {path}")
        return attached in self.attached

    def switch_to_onboard(self, attached: AttachedMouse) -> None:
        """ensure_onboard_mode on the probe's budget through mouse_call: G HUB can leave the
        mouse in HOST mode, where no profile runs. A mode write, not a profile write, and what
        the mouse boots into anyway: volatile, measured on the G502 X PLUS (the G502 HERO's
        firmware also writes its lighting records on leaving HOST mode, as the comment on
        mouse.G502HERO says), and sent to a model whose writes are not tested too. A mouse
        without a spy that does not answer it is owed it (`owed_onboard`); one that refused
        answered."""
        try:
            switched = self.mouse_call(lambda: attached.mouse.ensure_onboard_mode(**MOUSE_PROBE),
                                       target=attached)
        except MouseError as exc:
            # At info: a mouse left with no profile running is warned of by run_profile_one.
            log.info("%s: the switch to onboard mode was not taken: %s", attached.name, exc)
        else:
            if switched:
                log.info("%s %s", attached.name, switched)
        attached.owed_onboard = attached.spy is None and attached.silent

    def _setup_onboard(self, attached: AttachedMouse) -> bool:
        """connect_mouse's setup outside HOST mode: True when profile 1, the DPI and the lighting
        all took."""
        if attached.spy is not None:
            self.stop_spy(attached)
        # Each step checks the mouse is still attached and answering: mouse_call drops a mouse
        # whose node has gone, and one not answering would cost its timeouts again (cut_short).
        if self.mouse_answering(attached):
            self.switch_to_onboard(attached)
        if attached.spy is not None and attached.silent:
            # Not answering the spy stop or the switch, it may be in HOST mode with its
            # programmable buttons dead, as a crashed run leaves it: counted there, nothing
            # more goes now (the owner's decision of 2026-09-28), and its next setup hands it
            # back (leave_host), the owner's decision of 2026-10-03. One that refused answered.
            attached.host = True
        # G HUB can leave it on another profile too.
        profile = self.mouse_answering(attached) and self.run_profile_one(attached)
        self.log_profile_one(attached.mouse)
        dpi = self.mouse_answering(attached) and self.dpi.restore_dpi(attached)
        self.buttons.open_mouse_input(attached)
        lit = attached in self.attached and self.color.give_back_lighting(attached)
        return lit and dpi and profile

    def run_profile_one(self, attached: AttachedMouse) -> bool:
        """Have the mouse run onboard profile 1, the one Gearbench writes.

        The profile is read on the probe's budget; a mouse on another is switched with 0x8100
        fn 3 `00 01` and read back. The switch lands on profile 1's default DPI stage, so the
        caller's restore_dpi follows. A mouse in HOST mode, where no profile runs, is not
        switched: leaving HOST mode is ensure_onboard_mode's, host_refused's and hand_back's. A
        read that names a profile shows the mouse out of HOST mode, so its model leaves
        `_left_in_host`; one that names none with host mode on counts it in HOST mode (`host`),
        where host_refused's ONBOARD write answered and not taken leaves it.

        True when profile 1 runs, or there is nothing to run. A switch that fails or does not
        take is logged at warning and kept as `profile_error` for `problems`, unless the mouse
        went away with it. One not sent (SwitchRefused) is logged in words that say what to do,
        and kept as none: `problems` words it from the sector (other_profile_words), so a restore
        that mends profile 1 ends it.
        """
        mouse = attached.mouse
        if mouse.onboard is None:
            return True
        # With 0x8100, neither read below is None (Mouse.current_profile,
        # Mouse.switch_to_profile_one).
        try:
            found = cast(int, self.mouse_call(lambda: mouse.current_profile(**MOUSE_PROBE),
                                              target=attached))
        except MouseError as exc:
            log.info("could not read which onboard profile %s runs: %s", attached.name, exc)
            return False
        if found:
            self._left_in_host.discard(mouse.backup_name)
        if found == PROFILE_SECTOR:
            attached.profile_error = None
            return True
        running, kept = found, True
        if found == 0:
            why = "it did not go back to onboard mode, where a profile runs"
            # So host mode off and the stop hand it back, as hand_back's profile step counts one.
            attached.host |= self.wants_host(attached)
        else:
            try:
                running = cast(int, self.mouse_call(
                    lambda: mouse.switch_to_profile_one(**MOUSE_PROBE), target=attached))
            except MouseError as exc:
                if attached not in self.attached:
                    return False        # gone, as mouse_call said: no profile to warn of
                kept = not isinstance(exc, SwitchRefused)
                why = f"the switch to profile 1 failed: {exc}" if kept else str(exc)
            else:
                if running == PROFILE_SECTOR:
                    attached.profile_error = None
                    log.info("%s was running %s; switched it to profile 1",
                             attached.name, profile_words(found))
                    return True
                why = "the switch to profile 1 was answered and did not take"
        attached.profile_error = why if kept else None
        log.warning("%s runs %s, not profile 1: %s", attached.name, profile_words(running), why)
        return False

    @staticmethod
    def log_profile_one(mouse: Mouse) -> None:
        """Log what onboard profile 1 holds, at attach, under the mouse's name."""
        if mouse.profile is None:
            return
        stages, rate = mouse.profile.dpi_stages, mouse.profile.report_rate
        default, shift = stage_dpi(stages, mouse.profile.default_dpi_index), mouse.shift_dpi
        # Only a model with a button table has G4 to G9: another's entries are not those
        # buttons.
        table = mouse.button_table
        buttons = "" if table is None else f", {table.state_words(mouse.profile)}"
        # A rate byte other than 1, 2, 4 or 8 reads as None (ProfileImage.report_rate). No stage
        # is 0 (ProfileImage.dpi_slot), so only no stages leaves the default and shift unknown.
        log.info("%s profile 1: %s, DPI stages %s, default %s, shift %s%s", mouse.name,
                 f"{rate} Hz" if rate else "an unknown report rate",
                 ", ".join(map(str, stages)) or "none", default or "?", shift or "?", buttons)

    @staticmethod
    def find_spy(mouse: Mouse) -> ButtonSpy | None:
        """The mouse's button spy (ButtonSpy.find), or None where it has none, its model is not
        in SPY_MODELS, which is not asked, or it has no 0x8100. A lookup the mouse refuses is
        taken as none, and the mouse runs ONBOARD mode; one it does not answer raises, as the
        attach's other lookups do."""
        try:
            # HOST mode is 0x8100's mode write: without it Mouse.set_mode sends nothing and an
            # arm would read the mode back as None.
            if mouse.onboard is not None or mouse.product not in SPY_MODELS:
                return ButtonSpy.find(mouse.dev, mouse.product)
            why = "has no onboard profiles feature (0x8100)"
        except HidppError as exc:
            if unanswered(exc):
                raise
            why = f"refused the lookup of its button spy ({exc})"
        log.info("%s %s; it stays in onboard mode", mouse.name, why)
        return None

    def maybe_connect_mouse(self) -> None:
        """Look for every known mouse not yet attached, once the retry is due; before it, for
        the cable of a mouse attached through its receiver, on a pace of its own
        (Routes.look_for_cables).

        The next deadline is set after the attempt: one against a mouse that is off can outlast
        MOUSE_RETRY, and set before, the retry would be due again at once and starve the keyboard
        and the socket. A mouse came back when connect_mouse attached it, not when the list
        grew: a move to the cable drops the receiver's entry as it adds the cable's. A teardown
        during the pass, an attach's own included, still brings the next attempt within
        MOUSE_RETRY.
        """
        self.routes.look_for_cables()
        if not look_due(time.monotonic(), self._mouse_retry):
            return
        # Every mouse not attached yet, so a second receiver is still looked for. A cabled mouse
        # covers its receiver route, which stops answering while it is cabled; not the other way
        # round, which is how a mouse moves to its cable (Routes.left_route).
        already = self.attached_models()
        wanted = [f for f in find_attached_mice() if f[1] not in already]
        came = False
        # Set below, as the earlier of this and what a teardown in the pass pulls it to.
        self._mouse_retry = float("inf")
        try:
            for found in wanted:
                came = self.connect_mouse(found) or came
        finally:
            if not wanted or came:
                # Nothing missing, or something came back: the backoff starts again.
                wait = self._mouse_backoff = MOUSE_RETRY
            else:
                # Doubled for next time, so the first retry stays prompt.
                wait = self._mouse_backoff
                self._mouse_backoff = min(wait * 2, MOUSE_RETRY_MAX)
            self._mouse_retry = min(self._mouse_retry, time.monotonic() + wait)

    def attached_models(self) -> set[int]:
        """Product ids not to attach: each attached mouse's, and a cabled one's receiver routes.
        Another cable id of its model is a second mouse, since a mouse has one id on its cable."""
        return {m.product for m in self.attached} | {
            route for m in self.attached if m.product in MOUSE_CABLED
            for key in dpi_keys(m.product) if (route := int(key, 16)) not in MOUSE_CABLED}

    def poll_for_mouse(self) -> None:
        """Bring the next reconnect attempt within MOUSE_POLL_RETRY, for a client asking about a
        mouse that is not there. The backoff is left as it is, so it grows again once the asking
        stops."""
        self._mouse_retry = min(self._mouse_retry, time.monotonic() + MOUSE_POLL_RETRY)

    def keep_waiting(self, dev: Hidpp, found: tuple[Path, int, int]) -> None:
        """Keep a mouse node open after its first probe went unanswered, so the mouse is heard
        when it comes on (pump_waiting). The retry still asks it, for a receiver that says
        nothing, and sooner once the node carries motion or a late reply."""
        self._mouse_waiting[str(found[0])] = (dev, found)
        self.d.selector.register(dev.fileno, selectors.EVENT_READ, ("mouse-wait", str(found[0])))

    def stop_waiting(self, key: str) -> None:
        """Close and unregister a node keep_waiting kept."""
        dev, _found = self._mouse_waiting.pop(key)
        self.d.selector.unregister(dev.fileno)
        dev.close()

    def stop_all_waiting(self) -> None:
        """Close every node kept open for a mouse that has not answered."""
        for key in list(self._mouse_waiting):
            self.stop_waiting(key)

    def pump_waiting(self, key: str) -> None:
        """Read a mouse node kept open by keep_waiting. The receiver's link notice attaches the
        mouse at once, and so does anything else it says, which is then acted on as from any
        attached mouse. A link-down notice is dropped, and a node that has gone is closed.

        Motion through the receiver, or a HID++ report that queues nothing, such as the probe's
        late reply, says the mouse is on: the retry is brought forward to now, at most once per
        MOUSE_RETRY, since a probe that misses costs the loop 1.5 s.
        """
        dev, found = self._mouse_waiting[key]
        try:
            heard = dev.pump()
        except HidppFailure as exc:
            log.debug("the mouse node %s kept open has gone: %s", key, exc)
            self.stop_waiting(key)
            return
        down = False
        while (event := dev.next_event()) is not None:
            if event.link_down:
                down = True
                continue
            if found[1] in self.attached_models():
                # The same mouse attached on its cable meanwhile: its receiver is not probed,
                # as at the retry.
                continue
            log.info("the mouse on %s %s; attaching it", key,
                     "linked to its receiver" if event.link else "said something")
            if not self.connect_mouse(found):
                # Kept again only by a probe that went unanswered (keep_waiting).
                if key in self._mouse_waiting:
                    log.info("the mouse on %s did not answer; still waiting for it", key)
            elif not event.link:
                attached = next(m for m in self.attached if m.dev is dev)
                self.mouse_event(attached, event)
            return
        now = time.monotonic()
        # Heard.hidpp is true of the link-down notice too, which says the mouse is not on.
        if ((heard.input or (heard.hidpp and not down))
                and found[1] not in self.attached_models() and now >= self._mouse_nudged):
            log.debug("the mouse on %s is heard; looking for it now", key)
            self._mouse_nudged = now + MOUSE_RETRY
            self._mouse_retry = min(self._mouse_retry, now)

    def pump_waiting_queued(self) -> None:
        """pump_waiting for each kept node with notifications queued: a probe that went
        unanswered files what it read while it waited, the receiver's link-up among it, and
        that does not wake select."""
        for key in [k for k, (dev, _found) in self._mouse_waiting.items() if dev.pending]:
            self.pump_waiting(key)

    def reapply_mouse(self, attached: AttachedMouse, occasion: Occasion = "power-on") -> None:
        """Give a mouse back what it lost at power-on: the lighting claim, the color and a DPI
        set over 0x2201 live in its working memory. Its power-on report asks the host to
        configure it again, as G HUB does.

        `occasion` is what brings it, a power-on report by default. "recheck" is a silent mouse
        that answered its recheck: switched off and on unheard, or only slow. When `cookie_set`
        trusts it, its 0x0020 cookie is read first: one that still holds MOUSE_COOKIE kept its
        setup and is not configured again; only its spy is started again in HOST mode (respy),
        the DPI under a shift it did not answer is put back, and tend_mice gives any lighting
        owed. One in HOST mode whose spy does not start again (respy) is configured again.
        "linked" is a mouse whose link came back, configured again whatever its cookie says:
        one without 0x1D4B, its only sign of a power cycle, or one counted in HOST mode whose
        spy host_link_up did not start again (respy). "unfinished" is a mouse owed a full setup
        (`cut_short`) and answering: no probe went out, and no cookie is read. Beyond a
        recheck's cookie and the DPI under a held shift, the occasion changes only the words
        logged (REAPPLY_WORDS).

        In HOST mode the setup is setup_host's; otherwise the hand-back of a mouse counted in
        HOST mode (leave_host) or a switch to ONBOARD mode it is owed (`owed_onboard`), the claim
        and its paint, profile 1, whose switch lands on the default stage, then restore_dpi.
        What its buttons held is let go of first, since a release may have been lost; at
        "unfinished" alone, the DPI under a held shift is put back where none is remembered
        (setup_host's held).
        """
        now = time.monotonic()
        if now - attached.reapplied_at < REAPPLY_INTERVAL:
            return
        if occasion == "recheck" and attached.cookie_set:
            cookie = self.read_cookie(attached)
            if cookie == MOUSE_COOKIE and (not attached.host or self.respy(attached)):
                log.info("%s answered its recheck and kept its setup; not configured again%s",
                         attached.name, ", its button spy started again" if attached.host else "")
                if attached.dpi_before_shift is not None:
                    # No setup follows to set the DPI, as at a link back (host_link_up).
                    self.dpi.put_back_shift(attached, "a shift it did not answer")
                return
            if not self.mouse_answering(attached):
                return      # its node went, or it stopped answering again
        attached.reapplied_at = now
        # Set up again from here: the cookie is trusted again once it is.
        attached.cookie_set = False
        self.mark_answering(attached)       # it has just said it is on
        # Switched on, it shows its own lighting until it is given ours.
        attached.forget_showing()
        # The DPI under a held shift is put back only at a setup owed while it answers: a mouse
        # that may have been switched off and on is on its own DPI.
        held = {"unfinished": self.buttons.drop_spy_state(attached)}.get(occasion)
        if self.wants_host(attached):
            took, lit = self.setup_host(attached, held=held)
        else:
            if attached.host:
                # Host mode was turned off while it was not answering, its hand-back did not
                # land, or it did not answer the spy stop or the switch at its attach.
                self.leave_host(attached, "host mode is off", held)
            elif attached.owed_onboard:
                self.switch_to_onboard(attached)
            lit = self.mouse_answering(attached) and self.color.claim_and_paint(attached)
            # Not to a mouse whose claim just timed out: that is its timeouts again.
            profile = self.mouse_answering(attached) and self.run_profile_one(attached)
            dpi = self.mouse_answering(attached) and self.dpi.restore_dpi(attached, held)
            took = lit and profile and dpi
        if took:
            self.set_cookie(attached)
        attached.cut_short = attached.silent and not took
        self.keep_wheel(attached)
        when, done = REAPPLY_WORDS[occasion]
        if attached not in self.attached:
            log.info("%s went away while being configured %s", attached.name, when)
            return
        if attached.cut_short:
            # Nothing after the step that went unanswered was sent (tend_mice finishes it).
            log.info("%s stopped answering while being configured %s; set up once it answers",
                     attached.name, when)
            return
        log.info("%s %s%s", attached.name, done,
                 # A refused DPI restore says so only at debug (restore_dpi).
                 "" if took else ", except its lighting" if not lit
                 else ", except a step that did not take")

    def recheck_mice(self) -> None:
        """Ask every mouse again before anything is written to it, at a resume: a receiver that
        lost power in a suspend sends no power-on report. One that answers is configured again
        in full, its cookie not read (`cookie_set` is cleared); one that does not is left not
        answering (tend_mice), and set up in full once heard from (cut_short)."""
        now = time.monotonic()
        for attached in self.attached:
            attached.cookie_set = False
            attached.drop_sent()
            attached.showing, attached.silent, attached.link_down = None, True, False
            attached.recheck_wait, attached.recheck_at = MOUSE_SILENT_RECHECK, now
            attached.reapplied_at = 0.0
            # Heard from before its recheck, it would get none.
            attached.cut_short = True
        self._mouse_retry = min(self._mouse_retry, now)

    # -- what problems() lists, and a mouse's name in it ---------------------

    def named(self, mouse: AttachedMouse) -> str:
        """"the <model>", or "the <label>" (AttachedMouse.label) when another attached mouse
        has its name: two of one model (its receiver and its cable) share one."""
        twin = any(m is not mouse and m.name == mouse.name for m in self.attached)
        return f"the {mouse.label}" if twin else f"the {mouse.name}"

    def problems(self) -> list[tuple[str, str]]:
        """The mice's entries for the daemon's `problems`, as (code, message): the mice found and
        not attached (Unattached.problems), then each attached mouse's, then the keystrokes
        saw_keystroke warned of. Nothing is sent to a mouse."""
        found = self.unattached.problems()
        for attached in self.attached:
            found += self.attached_problems(attached)
        found += [("mouse_keystrokes", message) for message in self.buttons.keystroke_problems()]
        return found

    def attached_problems(self, attached: AttachedMouse) -> list[tuple[str, str]]:
        """One attached mouse's entries for `problems`."""
        name = self.named(attached)
        found: list[tuple[str, str]] = []

        def add(code: str, message: str) -> None:
            found.append((code, message))

        running = attached.mouse.running_profile
        if runs_another_profile(running) and not attached.host:
            add("mouse_other_profile", other_profile_words(
                name, running, attached.mouse.switch_refusal(), attached.profile_error,
                attached.silent))
        if self.wants_host(attached) and not attached.host and attached.host_error:
            # Profile 1 only where the last read found it: mouse_other_profile names another.
            runs = "; it runs onboard profile 1" if running == PROFILE_SECTOR else ""
            add("mouse_not_host", not_taken_words(f"{name} ({attached.host_error}){runs}"))
        if self.stuck_in_host_mode(attached):
            add("mouse_left_in_host", stuck_in_host(
                [name], host_why(self.wants_host(attached), attached.host_error)))
        if attached.input_denied and not attached.host:
            add("mouse_buttons_no_permission",
                f"Gearbench cannot read G4 to G9 on {name} from "
                f"{attached.input_denied}")
        return found

    # -- mouse_call, silence and rechecks ------------------------------------

    def mouse_call[T](self, what: Callable[..., T], *args: object,
                      target: AttachedMouse, answered: bool = True) -> T:
        """Run one piece of mouse HID++ work, and sort out what a failure means, for a caller
        that leaves that to this function. A failure is re-raised as MouseError, in
        hidpp_refusal's sentence with the mouse's name, which mouse_refusal gives as the
        reply. `target` is the mouse the work is for, the one a failure marks or tears down.

        Only a node that has gone tears the mouse down. A timeout, or the receiver's answer that
        it cannot reach the mouse, is a mouse off behind its receiver, whose node stays: it is
        kept attached and marked not answering, so its power-on report is heard when it comes
        back. An error reply is a mouse that answered. A write or read the kernel failed with an
        errno that does not mean the node is gone is neither: it is worded as the mouse not
        reached, in the words of the OSError behind it, and the mark is left as it was. Success
        clears the mark unless `answered` is False: a write sent without waiting succeeds
        against a mouse that is off.
        """
        try:
            result = what(*args)
        except DeviceGone as exc:
            log.info("%s went away: %s", target.name, failure_reason(exc))
            self.teardown_mouse(target)
            raise MouseError(hidpp_refusal(target.name, exc)["error"]) from exc
        except HidppFailure as exc:
            if unanswered(exc):
                self.mark_silent(target)
            raise MouseError(hidpp_refusal(target.name, exc)["error"]) from exc
        if answered:
            self.mark_answering(target)
        return result

    def mouse_answering(self, attached: AttachedMouse) -> bool:
        """Whether a mouse is still attached and not marked not answering (mark_silent)."""
        return attached in self.attached and not attached.silent

    def mark_silent(self, attached: AttachedMouse) -> None:
        """Note that a mouse did not answer; see MOUSE_SILENT_RECHECK."""
        if attached.silent:
            return
        log.info("%s is not answering; asking again in %d s", attached.name, MOUSE_SILENT_RECHECK)
        attached.silent = True
        # Off, perhaps, and on again unheard: what it shows is not known.
        attached.showing = None
        attached.recheck_wait = MOUSE_SILENT_RECHECK
        attached.recheck_at = time.monotonic() + MOUSE_SILENT_RECHECK

    def mark_answering(self, attached: AttachedMouse, quiet: bool = False) -> None:
        """Clear the mark: the mouse said something or answered a request, so its link is up,
        whatever its receiver said last. `quiet` is for a caller that logs it in its own words.
        The `heard_at` it sets makes the next `mouse` read it again (MOUSE_READING_MAX_AGE).
        """
        if attached.silent and not quiet:
            log.info("%s is answering again", attached.name)
        attached.silent = attached.link_down = False
        attached.heard_at = time.monotonic()

    def take_link_notice(self, attached: AttachedMouse, down: bool) -> None:
        """Act on the receiver's notice that the mouse's link went down or came up
        (hidpp.LINK_NOTICE), `down` its Event.link_down.

        Down, the mouse is off or out of range: marked not answering, and not probed at its
        recheck until the link is back. A color in flight is owed, and what its buttons held is
        let go of, since their releases will not come, but for the DPI under a held shift, which
        the link-up puts back unless it takes the mouse for power-cycled: in HOST mode a mode
        read that does not find HOST (host_link_up), or a mouse without 0x1D4B.
        REAPPLY_INTERVAL counts from nothing: the next link is a new connection.

        Up, it is answering. A mouse with 0x1D4B sends its power-on report next, which
        configures it; one without says nothing more, so it is configured again now, whatever
        its cookie says. A mouse in HOST mode is asked its mode at once (host_link_up).
        """
        if down:
            if not attached.link_down:
                log.info("%s's receiver lost the link: switched off or out of range; "
                         "nothing is written to it unasked until it is back", attached.name)
            attached.link_down = attached.silent = True
            attached.reapplied_at = 0.0
            attached.showing = None
            attached.drop_sent()
            # All but the DPI under a held shift, kept for the link-up (see the docstring).
            attached.dpi_before_shift = self.buttons.drop_spy_state(attached)
            return
        log.info("%s linked to its receiver again", attached.name)
        self.mark_answering(attached, quiet=True)
        if attached.host:
            self.host_link_up(attached)
        elif attached.wireless is None:
            self.reapply_mouse(attached, "linked")
        elif attached.dpi_before_shift is not None:
            self.dpi.put_back_shift(attached)

    def tend_mice(self) -> None:
        """Once a pass: recheck each silent mouse that is due, and give each mouse what it is
        owed: an unfinished setup, a repaint after a DPI flash, its lighting and its tuning.

        A recheck is one probe. A mouse that answers it was switched on unheard, or was only
        slow; on a model in COOKIE_MODELS its cookie tells the two apart (reapply_mouse).
        """
        now = time.monotonic()
        for attached in list(self.attached):
            if attached.silent and not attached.link_down and now >= attached.recheck_at:
                if self.mouse_answers(attached):
                    # reapply_mouse says it answered, in its own words.
                    self.mark_answering(attached, quiet=True)
                    self.reapply_mouse(attached, "recheck")
                else:
                    attached.recheck_wait = min(attached.recheck_wait * 2, MOUSE_RETRY_MAX)
                    attached.recheck_at = time.monotonic() + attached.recheck_wait
            if (attached.cut_short and self.mouse_answering(attached)
                    and now >= attached.reapplied_at + REAPPLY_INTERVAL):
                # Answering, so no recheck will set it up.
                log.info("%s is owed a full setup; setting it up again", attached.name)
                self.reapply_mouse(attached, "unfinished")
            if (attached in self.attached and attached.repaint_at is not None
                    and now >= attached.repaint_at):
                self.color.repaint_after_flash(attached)
            if attached in self.attached and attached.owed_lighting and attached.sent is None:
                self.color.give_back_lighting(attached)
            if attached in self.attached and attached.owed_tuning:
                self.dpi.tune(attached)
        self.battery.poll()

    def mouse_answers(self, attached: AttachedMouse, gone: bool = True) -> bool:
        """Whether a mouse answers one cheap request within the probe's budget: the window's
        `mouse` request gives up after three seconds. A node that has gone answers `gone`:
        True lets the real request say so and tear it down."""
        try:
            attached.dev.lookup(F_UNIFIED, **MOUSE_PROBE)
        except DeviceGone:
            return gone
        except HidppFailure:
            return False
        return True

    def read_cookie(self, attached: AttachedMouse) -> bytes | None:
        """The mouse's 0x0020 cookie, read on the probe's budget, or None when the read failed
        (mouse_call); the caller tells the failures apart by `silent` and whether it is still
        attached. Called only with `cookie_set`, which implies `config_change`."""
        # set_cookie sets `cookie_set` only on a mouse with `config_change`.
        index = cast(int, attached.config_change)
        try:
            reply = self.mouse_call(lambda: attached.dev.request(
                index, COOKIE_GET, **MOUSE_PROBE), target=attached)
        except MouseError as exc:
            log.debug("could not read %s's configuration cookie: %s", attached.name, exc)
            return None
        return reply[:2]

    def set_cookie(self, attached: AttachedMouse) -> None:
        """Mark a mouse set up in full: MOUSE_COOKIE on 0x0020, on the probe's budget, for a
        recheck to read back (reapply_mouse). A write that fails leaves `cookie_set` False, so
        the next recheck it answers configures it again."""
        if (index := attached.config_change) is None or not self.mouse_answering(attached):
            return
        try:
            self.mouse_call(lambda: attached.dev.request(
                index, COOKIE_SET, MOUSE_COOKIE, **MOUSE_PROBE), target=attached)
        except MouseError as exc:
            log.debug("could not set %s's configuration cookie: %s", attached.name, exc)
            return
        attached.cookie_set = True

    def keep_wheel(self, attached: AttachedMouse) -> None:
        """Keep the wheel's mode at the end of a setup: one read on the probe's budget. A mode
        with high resolution on is kept: hid-logitech-hidpp writes it at each connect through a
        receiver and scales the wheel for it. Off, after one was kept, the kept mode is written
        back, since the kernel goes on scaling. A mode without high resolution is never kept or
        written, so Gearbench never undoes the kernel's own write or chooses a mode. A failure
        is logged at debug."""
        mouse = attached.mouse
        if mouse.wheel is None or not self.mouse_answering(attached):
            return
        kept = attached.wheel_mode
        try:
            # With 0x2121, checked above, the read is not None (Mouse.wheel_mode).
            mode = cast(int, self.mouse_call(lambda: mouse.wheel_mode(**MOUSE_PROBE),
                                             target=attached))
            if mode & WHEEL_HIRES:
                if kept != mode:
                    attached.wheel_mode = mode
                    log.debug("%s's wheel is in mode %02x; kept", attached.name, mode)
                return
            if kept is None:
                log.debug("%s's wheel is in mode %02x, high resolution off, with none kept "
                          "to put back", attached.name, mode)
                return
            self.mouse_call(lambda: mouse.set_wheel_mode(kept, **MOUSE_PROBE), target=attached)
        except MouseError as exc:
            log.debug("%s: the wheel's mode was not read or put back: %s", attached.name, exc)
            return
        log.info("%s's wheel was in mode %02x; put back to high resolution (%02x)",
                 attached.name, mode, kept)

    def pump_mouse(self, attached: AttachedMouse) -> bool:
        """Read what one mouse has said on its HID++ node (MouseLighting.read_node), then
        settle its color in flight and act on its notifications (mouse_events). True when it
        was motion alone: nothing HID++, the mouse not marked silent, and no color sent without
        waiting in flight, whose answer a request may have kept for mouse_events to settle
        (Daemon.one_pass).
        """
        idle = attached.sent is None and not attached.silent
        heard = self.color.read_node(attached)
        if heard is None:
            return False
        self.mouse_events(attached)
        return idle and not heard.hidpp

    def mouse_events(self, attached: AttachedMouse) -> None:
        """Settle a color in flight (check_mouse_write), then act on the queued notifications."""
        self.color.check_mouse_write(attached)
        while attached in self.attached and (event := attached.dev.next_event()) is not None:
            self.mouse_event(attached, event)

    def mouse_event(self, attached: AttachedMouse, event: Event) -> None:
        """One notification from a mouse. The receiver's link notice is taken first, since a
        link going down is not the mouse talking. 0x1D4B fn 0 is sent when the mouse comes back:
        its second byte asks the host to configure it again, its third says the power switch was
        used. Anything not acted on is logged.
        """
        if event.link:
            self.take_link_notice(attached, event.link_down)
            return
        # hidpp's _raw_read drops a report shorter than its id's REPORT_LENGTH, so an event
        # has 3 bytes at least.
        if event.feature_index == attached.battery_index and event.address == 0:
            self.battery.take_report(attached, event.data)
            return
        self.mark_answering(attached)       # it is talking, so it answers
        if attached.host and event.feature_index == attached.its_spy.index and event.address == 0:
            self.buttons.take_spy_mask(attached, event.data)
            return
        if event.feature_index == attached.wireless and event.address == 0:
            if event.data[2] == 1:
                now = time.monotonic()
                if now - attached.switched_on_at >= REAPPLY_INTERVAL:
                    log.info("%s was switched on", attached.name)
                attached.switched_on_at = now
            if event.data[1] == 1:
                self.reapply_mouse(attached)
            return
        if (not self.runs_host(attached) and event.feature_index == attached.onboard
                and event.address == STAGE_EVENT):
            self.dpi.take_stage_event(attached, event.data[0])
            return
        log.debug("%s notification: feature idx 0x%02x addr 0x%02x %s",
                  attached.name, event.feature_index, event.address, event.data[:6].hex(" "))

    # -- HOST mode and the spy -----------------------------------------------

    def wants_host(self, attached: AttachedMouse) -> bool:
        """Whether this mouse is to run in HOST mode: the switch is on and it has a spy HOST mode
        drives (spy.SPY_MODELS)."""
        return self.d.config.mouse_host_mode and attached.spy is not None

    def runs_host(self, attached: AttachedMouse) -> bool:
        """Whether the daemon runs this mouse in HOST mode: counted there with host mode on.
        One counted there with host mode off is not: it is owed a hand-back, and may run a
        profile in ONBOARD mode (_setup_onboard)."""
        return attached.host and self.wants_host(attached)

    def stuck_in_host_mode(self, attached: AttachedMouse) -> bool:
        """Whether a mouse is counted in HOST mode and answering, and not driven there: host
        mode is off, or its arm and its switch back were refused (host_refused)."""
        return attached.host and not attached.silent and bool(
            attached.host_error or not self.wants_host(attached))

    def setup_host(self, attached: AttachedMouse, moved: bool = False, held: int | None = None
                   ) -> tuple[bool, bool]:
        """HOST mode's setup, at attach and whenever the mouse has lost it: the arm, then the
        lighting claim and its paint, the report rate in force sent live and the DPI, each only
        to a mouse still attached and answering. The claim follows the mode write, as G HUB
        orders it. `moved` is an attach that dropped the same mouse on its other route. `held`
        is the DPI under a held shift its caller let go of (Buttons.drop_spy_state), put back
        where none is remembered (Dpi.restore_dpi), as hand_back's, or under a game profile's
        own stages kept for their end.

        Answers (all took, the lighting took); only the first earns the cookie, so a mouse whose
        arm did not take is armed again at its next recheck. It is sent no rate: one put back in
        ONBOARD mode (host_refused) keeps its profile's own.
        """
        armed = self.arm_host(attached, moved)

        # Claimed again after the mode write: what it shows is not known.
        attached.forget_showing()
        lit = self.mouse_answering(attached) and self.color.claim_and_paint(attached)
        rate = self.mouse_answering(attached) and (not armed or self.dpi.send_rate(attached))
        dpi = self.mouse_answering(attached) and self.dpi.restore_dpi(attached, held)
        return armed and lit and rate and dpi, lit

    def arm_host(self, attached: AttachedMouse, moved: bool) -> bool:
        """Put the mouse in HOST mode with its button spy armed, in G HUB's order: 0x8100 fn 2
        (the mode, _mode_before_arm's read), fn 1 `02`, spy fn 0 (the count, which must be the
        model's), fn 3 (the table in force, logged), fn 1 `00 00 00` (start), fn 4 (G HUB's
        table), and 0x8100 fn 2 again, which must read HOST. Each on the probe's budget, through
        mouse_call.

        True when the arm took. A mouse that stops answering or goes away part way is left as
        it is, for its recheck or its next attach. One that refuses a step from the HOST write
        on, answers an unknown count, or reads back a mode that is not HOST, is put back in
        ONBOARD mode (host_refused); a refused table read is only logged. `host` is set before
        the HOST write, so an arm cut short at any step is still taken back to ONBOARD mode
        when host mode is turned off.
        """
        mouse, spy, model = attached.mouse, attached.its_spy, attached.its_spy.model
        attached.armed = False

        if not self._mode_before_arm(attached, moved):
            return False
        why, step = None, "the switch to host mode"
        try:
            # Counted before the write, as keyboard.set_keyboard_mode counts its own: a write
            # that lands with its reply lost leaves the mouse in HOST mode all the same.
            attached.host = True
            self.mouse_call(lambda: mouse.set_mode(MODE_HOST, **MOUSE_PROBE), target=attached)
            step = "the button spy's count"
            count = self.mouse_call(lambda: spy.count(**MOUSE_PROBE), target=attached)
            if count != model.count:
                why = (f"its button spy counts {count} control{'' if count == 1 else 's'}, "
                       f"not the {model.count} this build knows")
            else:
                try:
                    table = self.mouse_call(lambda: spy.table(**MOUSE_PROBE), target=attached)
                except MouseError as exc:
                    if not self.mouse_answering(attached):
                        return False
                    log.debug("%s: the button spy's table could not be read: %s",
                              attached.name, exc)
                else:
                    log.debug("%s: the button spy's table before ours: %s", attached.name,
                              table[:count].hex(" "))
                step = "the button spy's start"
                self.mouse_call(lambda: spy.start(**MOUSE_PROBE), target=attached)
                step = "the button spy's table"
                self.mouse_call(lambda: spy.set_table(G_HUB_TABLE, **MOUSE_PROBE), target=attached)
                step = "the mode read back"
                mode = self.mouse_call(lambda: mouse.get_mode(**MOUSE_PROBE), target=attached)
                if mode != MODE_HOST:
                    shown = MODE_NAMES[mode] if mode in MODE_NAMES else repr(mode)
                    why = f"the mode read back as {shown} after the switch to host mode"
        except MouseError as exc:
            if not self.mouse_answering(attached):
                return False
            why = f"{step} failed: {exc}"
        if why is not None:
            self.host_refused(attached, why)
            return False
        attached.host_error, attached.armed = None, True
        log.info("%s in host mode, its button spy armed (%d controls)", attached.name, count)
        return True

    def _mode_before_arm(self, attached: AttachedMouse, moved: bool) -> bool:
        """arm_host's first mode read, on the probe's budget: `host` takes what it finds, and is
        set when the read gets no answer, since a crashed run can leave the mouse in HOST mode;
        a refused read is taken as ONBOARD, and a mouse in ONBOARD mode runs profile 1 first, so
        profile 1 is the fallback. False when the mouse stopped answering or went away."""
        # Counted in HOST mode by this run, attached (`host`) or torn down so (_left_in_host):
        # only a mouse found there otherwise blames another driver.
        ours = attached.host or attached.mouse.backup_name in self._left_in_host
        try:
            mode = self.mouse_call(lambda: attached.mouse.get_mode(**MOUSE_PROBE), target=attached)
        except MouseError as exc:
            if not self.mouse_answering(attached):
                attached.host = True
                return False
            log.info("could not read which mode %s is in (%s); taken as onboard",
                     attached.name, exc)
            mode = MODE_ONBOARD
        else:
            attached.host = mode == MODE_HOST
            self._left_in_host.discard(attached.mouse.backup_name)
        if mode != MODE_HOST:
            self.run_profile_one(attached)
            return self.mouse_answering(attached)
        if moved:
            log.info("%s kept host mode across the move", attached.name)
        elif ours:
            log.info("%s was in host mode already", attached.name)
        else:
            log.info("%s was in host mode already: whatever drove it last did not hand "
                     "it back", attached.name)
        return True

    def host_refused(self, attached: AttachedMouse, why: str) -> None:
        """Stop the spy and put the mouse back in ONBOARD mode (0x8110 fn 2, 0x8100 fn 1 `01`)
        on profile 1, or on the profile it runs where the switch is refused (Mouse.switch_refusal,
        run_profile_one): left in HOST mode, its programmable buttons would be dead. `why` is
        kept as `host_error` for `problems`. HOST mode is tried again at the mouse's next
        power-on, recheck, resume or attach, and at `mouse_host_mode on`. It counts as in host
        mode until its ONBOARD write lands, as the profile read after it shows: one answered
        and not taken leaves no profile running (run_profile_one)."""
        attached.host_error = why
        log.warning("%s: host mode did not take (%s); switching it back to onboard mode",
                    attached.name, why)
        for what, send in (("the button spy's stop", lambda: attached.its_spy.stop(**MOUSE_PROBE)),
                           ("onboard mode", lambda: attached.mouse.set_mode(MODE_ONBOARD,
                                                                            **MOUSE_PROBE))):
            try:
                self.mouse_call(send, target=attached)
            except MouseError as exc:
                log.info("%s: %s was not taken: %s", attached.name, what, exc)
                if not self.mouse_answering(attached):
                    return
            else:
                if what == "onboard mode":
                    attached.host = False
        self.run_profile_one(attached)

    def stop_spy(self, attached: AttachedMouse) -> None:
        """Spy fn 2 alone, to a mouse attached with host mode off: a spy left armed by a crashed
        run goes on reporting in ONBOARD mode, where each press would come both ways."""
        try:
            self.mouse_call(lambda: attached.its_spy.stop(**MOUSE_PROBE), target=attached)
        except MouseError as exc:
            log.debug("%s: the button spy's stop was not taken: %s", attached.name, exc)

    def respy(self, attached: AttachedMouse) -> bool:
        """Spy fn 1 and fn 4 again, to a mouse in HOST mode that kept its setup: a recheck whose
        cookie is intact, or a link back in HOST mode. A third-party report has the firmware drop
        the table on sleep and at link-up (Mouser PR 295, unverified here). True when both
        took."""
        spy = attached.its_spy
        try:
            self.mouse_call(lambda: spy.start(**MOUSE_PROBE), target=attached)
            self.mouse_call(lambda: spy.set_table(G_HUB_TABLE, **MOUSE_PROBE), target=attached)
        except MouseError as exc:
            log.info("%s: its button spy could not be started again: %s", attached.name, exc)
            return False
        return True

    def hand_back(self, attached: AttachedMouse, release: bool = False, *, held: int | None
                  ) -> tuple[list[str], list[str], BaseException | None, int | None]:
        """Give HOST mode back, in G HUB's order: spy fn 2, with `release` a lighting claim it
        holds or may hold released (held_lighting), ONBOARD mode (0x8100 fn 1 `01`), onboard
        profile 1 (a read, and fn 3 `00 01` for a mouse on another; a read of no profile is a
        mouse still in HOST mode, sent no fn 3; a model whose writes are not tested keeps the
        profile it runs, read and sent no fn 3), and the DPI, but on a mouse whose onboard
        profile sets it from then on (Mouse.dpi_host_only). The mode write moves the DPI, so
        it is put back through set_dpi without its stage's level byte: a DPI put back never
        flashes. That DPI is the one outside a game profile's own stages (desktop_dpi), else
        `held`, the DPI under a held paddle, else the one read before the mode write.

        Each on the probe's budget, and direct calls, not mouse_call: teardown_mouse runs this.
        A step that gets no answer, or finds the node gone, skips the ones after it; when the
        first gets no answer, the whole is asked once more, since a mouse asleep wakes at a
        request and answers 0.42 to 1.63 s after it (the owner's decision of 2026-09-27). The
        mouse is not `armed` from the first step on. It counts as in HOST mode until the switch
        to ONBOARD mode lands, and again when the switch to profile 1 leaves no profile running,
        so its next setup finishes the hand-back. Answers what was done, what was not, the
        failure that stopped it or None, and the DPI put back or None.
        """
        # The mode change may disturb what the mouse shows (inferred).
        attached.forget_showing()
        attached.armed = False
        steps, dpi = self._hand_back_steps(attached, release, held)
        for attempt in (1, 2):
            done: list[str] = []
            missed: list[str] = []
            stopped: BaseException | None = None
            at_first = False
            put: int | None = None
            for index, (what, action) in enumerate(steps):
                if stopped is not None:
                    missed.append(f"{what} (skipped: "
                                  + ("gone)" if isinstance(stopped, DeviceGone | OSError)
                                     else "not answering)"))
                    continue
                try:
                    action()
                except (HidppFailure, MouseError, OSError) as exc:
                    log.debug("%s: %s not done: %s", attached.name, what, exc)
                    missed.append(f"{what} ({exc})")
                    if isinstance(exc, DeviceGone | OSError) or unanswered(exc):
                        stopped, at_first = exc, index == 0
                else:
                    if what == "DPI":
                        done.append(f"{dpi['wanted']} DPI")
                        put = dpi["wanted"]
                    elif what != "DPI read":
                        done.append(what)
            if attempt == 2 or not at_first or isinstance(stopped, DeviceGone | OSError):
                break
            log.info("%s did not answer the hand-back's first request; asking once more, in "
                     "case it was asleep", attached.name)
        return done, missed, stopped, put

    def _hand_back_steps(self, attached: AttachedMouse, release: bool, held: int | None
                         ) -> tuple[list[tuple[str, Callable[[], object]]], dict]:
        """hand_back's steps in order, each a name and its request, and the DPI to put back
        (`wanted`), which the steps update."""
        mouse = attached.mouse
        dpi = {"wanted": self.dpi.desktop_dpi(attached) or held}

        def onboard() -> None:
            mouse.set_mode(MODE_ONBOARD, **MOUSE_PROBE)
            attached.host = False
            self.dpi.out_of_force(attached)

        def read_dpi() -> None:
            dpi["wanted"] = mouse.get_dpi(**MOUSE_PROBE)

        def put_dpi() -> None:
            if dpi["wanted"] is None:
                raise MouseError("nothing to put back")
            dpi["wanted"] = mouse.set_dpi(dpi["wanted"], indicate=False, **MOUSE_PROBE)

        def profile_one() -> None:
            first = running = mouse.current_profile(**MOUSE_PROBE)
            # A first read of 0 is an ONBOARD write that did not land, answered or not, so no
            # fn 3. Relies on a read right after a switch that landed naming a profile; "Host
            # mode off and on again" step 2 checks it. A model whose writes are not tested is
            # sent no fn 3 (Mouse.switch_refusal) and keeps the profile it runs.
            if first and runs_another_profile(first) and not keeps:
                # The read named a sector, so the mouse has 0x8100 and this is not None
                # (Mouse.switch_to_profile_one).
                running = cast(int, mouse.switch_to_profile_one(**MOUSE_PROBE))
            if running == 0:
                # No profile runs, so the mouse counts as in HOST mode again; after the switch
                # too (the owner's decision of 2026-10-15), though there the ONBOARD write
                # landed, as the first read shows, so that 0 is not the "host mode"
                # profile_words names.
                attached.host = True
                raise MouseError("the switch left no profile running" if first
                                 else f"it runs {profile_words(running)}")
            if runs_another_profile(running) and not keeps:
                raise MouseError(f"it runs {profile_words(running)}")

        keeps = mouse.flash_refusal is not None
        steps: list[tuple[str, Callable[[], object]]] = [
            ("button spy stopped", lambda: attached.its_spy.stop(**MOUSE_PROBE))]
        if release and (lighting := attached.held_lighting) is not None:
            steps.append(("lighting released", lambda: lighting.release(**MOUSE_PROBE)))
        has_dpi = "dpi" in mouse.capabilities and not mouse.dpi_host_only
        if has_dpi and dpi["wanted"] is None:
            steps.append(("DPI read", read_dpi))
        steps += [("onboard mode", onboard),
                  ("the profile it runs" if keeps else "profile 1", profile_one)]
        if has_dpi:
            steps.append(("DPI", put_dpi))
        return steps, dpi

    def leave_host(self, attached: AttachedMouse, why: str, held: int | None = None) -> bool:
        """Take a mouse out of HOST mode while it stays attached; the lighting stays claimed. A
        mouse that went away is torn down, and one that stopped answering is marked so, as
        mouse_call would. True when every step of the hand-back was done. `held` is the DPI
        under a held shift its caller already let go of, as setup_host's."""
        held = self.buttons.drop_spy_state(attached) or held
        done, missed, stopped, put = self.hand_back(attached, held=held)
        # hand_back clears `host` only once the ONBOARD write lands.
        log.info("%s %s (%s): %s%s", attached.name,
                 "still in host mode" if attached.host else "out of host mode", why,
                 ", ".join(done) or "nothing done", f"; not {', '.join(missed)}" if missed else "")
        if isinstance(stopped, DeviceGone | OSError):
            self.teardown_mouse(attached)
        elif stopped is not None:
            self.mark_silent(attached)
        elif put is not None:
            # For the reading; sent without the level byte, so no repaint is due.
            self.dpi.after_dpi_write(attached, put)
        return not missed

    def host_link_up(self, attached: AttachedMouse) -> None:
        """A mouse in HOST mode whose link came back: its mode, one request on the probe's
        budget. Still HOST (out of range and back) with host mode on and its spy armed by an
        arm that ran to its end (`armed`), the spy is started again (respy). ONBOARD (switched
        off and on), host mode turned off meanwhile, or its arm refused or cut short, it is
        configured again at once, and the power-on report that follows falls within
        REAPPLY_INTERVAL. No answer leaves it to that report or its recheck. Found in HOST mode,
        it gets back the DPI under a shift held when its link went down, after the respy or
        before the setup, which would keep the shift's DPI when none is remembered."""
        try:
            mode = self.mouse_call(lambda: attached.mouse.get_mode(**MOUSE_PROBE), target=attached)
        except MouseError as exc:
            log.info("could not read which mode %s is in after its link came back: %s",
                     attached.name, exc)
            mode = None
        kept = (mode == MODE_HOST and self.wants_host(attached) and attached.armed
                and self.respy(attached))
        if kept:
            log.info("%s kept host mode; its button spy started again", attached.name)
        if (mode == MODE_HOST and attached.dpi_before_shift is not None
                and self.mouse_answering(attached)):
            # Before a setup too: its drop_spy_state forgets the DPI under the shift.
            self.dpi.put_back_shift(attached)
        if not kept and self.mouse_answering(attached):
            self.reapply_mouse(attached, "linked")

    def host_mode_changed(self, on: bool) -> None:
        """Act on the host mode switch set `on` or off on each mouse with a spy HOST mode drives
        that is answering. On arms it (setup_host); off hands it back to ONBOARD mode
        (leave_host) and claims and paints its lighting again. A mouse not answering, or that
        stops answering part way, gets it in full once it answers (cut_short): its cookie is not
        trusted after a change of mode. Turned on, a mouse already `host` is sent nothing unless
        `cookie_set` is False: a refused hand-back or fallback leaves it `host` with its spy
        maybe stopped. The cookie is set only when all of it took, the hand-back included, so a
        recheck sets up again a mouse left on another profile or DPI."""
        for attached in [m for m in self.attached if m.spy is not None]:
            if on == attached.host and (not on or attached.cookie_set):
                continue
            attached.cookie_set = False
            if attached.silent:
                # Heard from before its recheck, it would get none.
                attached.cut_short = True
                continue
            if on:
                attached.reapplied_at = time.monotonic()
                took, _lit = self.setup_host(attached, held=self.buttons.drop_spy_state(attached))
            else:
                whole = self.leave_host(attached, "host mode turned off")
                # `whole` last: the claim and paint go after a hand-back done in part too.
                took = (self.mouse_answering(attached) and self.color.claim_and_paint(attached)
                        and whole)
            if took:
                self.set_cookie(attached)
            attached.cut_short = attached.silent and not took

    # -- teardown ------------------------------------------------------------

    def ready_for_dark(self) -> None:
        """Before the black a power-off with color sync on writes each synced mouse (the
        keyboard's lights_out, then teardown_all): a color in flight is forgotten, and a
        mouse in HOST mode leaves it."""
        for attached in self.attached:
            # A color still in flight would leave the black owed, and nothing gives owed
            # lighting after the stop: its answer is not waited for.
            attached.drop_sent()
        # A synced mouse in HOST mode goes back to ONBOARD mode before the keyboard's
        # lights_out writes it black: a mode change may disturb the claim (inferred), and the
        # black is what has to hold. One not answering is sent nothing, as at any stop
        # (teardown_mouse): each write would wait out its timeouts.
        for attached in [m for m in self.attached
                         if m.host and not m.silent and m.lighting is not None]:
            self.leave_host(attached, "the machine is powering off")

    def teardown_all(self, keep_dark: bool) -> str:
        """Let go of every mouse and every node kept waiting at the stop, and answer how, for
        shutdown's line. `keep_dark`, a power-off with color sync on, writes each synced mouse
        black first, one not answering excepted, and keeps its claim (teardown_mouse)."""
        # At a power-off a synced mouse stays dark with its claim held, or it lights up on its
        # own; the black here covers a keyboard that is away.
        if keep_dark:
            self.color.go_dark()
        # The configuration cookie is left as is: the next start sets each mouse up in full
        # anyway.
        said = [self.teardown_mouse(m, keep_dark=keep_dark) for m in list(self.attached)]
        self.stop_all_waiting()
        return "; ".join(said) or "no mouse"

    def teardown_mouse(self, going: AttachedMouse, keep_dark: bool = False) -> str:
        """Drop one mouse; other mice are still driven. Answers how it went, for shutdown's
        line.

        `keep_dark`, at a power-off with color sync on, keeps the lighting claimed: released,
        the mouse would go back to its own lighting, which the black written before was to stop.
        The release is 0x8071's `01 00 06` (SW_FIRMWARE) or 0x8070's fn 8 `00`; the cookie stays
        (MOUSE_COOKIE). One that shows its own lighting is sent no release, unless a claim
        that failed may have landed (held_lighting). A mouse in HOST mode is handed back first
        (hand_back); one in ONBOARD mode under a held DPI shift gets the DPI under it back after
        the release. One not answering is sent nothing: left in HOST mode, its programmable
        buttons are dead until its next power cycle or the next start.
        """
        # Through a receiver the evdev node outlives the mouse: a reader left open would sit
        # beside the next one when it is back, and every press would act twice. The call answers
        # the DPI under a held shift, for the put-back below.
        held = self.buttons.close_mouse_input(going)
        with contextlib.suppress(KeyError, ValueError):
            self.d.selector.unregister(going.dev.fileno)
        # Nothing to a mouse not answering: the release would wait out its timeouts.
        said = f"{going.name} let go"
        # In HOST mode hand_back puts it back.
        shifted = held if not going.host and not going.silent else None
        if going.host and not going.silent:
            # The release in its place in G HUB's order; a synced mouse at a power-off was
            # handed back before its black (ready_for_dark).
            done, missed, _stopped, _put = self.hand_back(going, release=not keep_dark, held=held)
            kept = ["lighting claim held"] if keep_dark and going.held_lighting is not None else []
            said = (f"{going.name} let go: {', '.join(done + kept) or 'nothing done'}"
                    + (f"; not {', '.join(missed)}" if missed else ""))
        elif going.host or (going.silent and going.held_lighting is not None):
            said = f"{going.name} not answering" + (", left in host mode" if going.host else "")
            if going.held_lighting is not None:
                said += ", its lighting not released"
        elif going.held_lighting is not None and keep_dark:
            said = self.color.dark_words(going)
        elif going.held_lighting is not None:
            try:
                going.held_lighting.release()
            except (HidppFailure, OSError) as exc:
                said = f"{going.name} let go, its lighting not released ({exc})"
        if shifted is not None:
            try:
                going.mouse.set_dpi(shifted, indicate=False, **MOUSE_PROBE)
            except (HidppFailure, MouseError, OSError) as exc:
                said += f"; the DPI under the shift not put back ({exc})"
            else:
                said += f"; back to {shifted} DPI from the shift"
        going.dev.close()
        # The selection stays, so the mouse is shown again when it is back (Mice.current).
        self.attached = [m for m in self.attached if m is not going]
        self._mouse_kept[going.mouse.backup_name] = KeptMouse.of(going)
        # The last teardown says whether this run left the model in HOST mode.
        left = self._left_in_host
        (left.add if going.host else left.discard)(going.mouse.backup_name)
        # A replug or a re-enumeration may bring it back in a second: look again soon, whatever
        # the backoff had grown to. A cable pulled from a mouse with a receiver leaves it there,
        # looked for at once: a probe it does not answer yet keeps the node open, and its link
        # notice attaches it (pump_waiting).
        soon = 0.0 if going.product in MOUSE_CABLED and other_route(going.product) else MOUSE_RETRY
        self._mouse_retry = min(self._mouse_retry, time.monotonic() + soon)
        return said

    # -- the commands and their helpers --------------------------------------

    def mouse_for(self, request: dict, *, write: bool = True) -> AttachedMouse:
        """The mouse a request is for: the one `device` names (`asked`), else `current`. A
        client sends `device` where the shared selection could move off the mouse it means.
        MouseError when none is attached or no attached mouse is the one named, which says for
        a `write` that nothing was written: a request never goes to another model; a `device`
        neither text nor a number is refused (name_text)."""
        wanted = request.get("device")
        if wanted is None:
            if self.current is None:
                raise MouseError(self.unattached.none_attached())
            return self.current
        return asked(self.attached, name_text(wanted, "device"), self.unattached.found, write=write)

    def _cmd_mouse_select(self, request: dict) -> dict:
        """Choose the mouse the window shows and a command without `device` means, by name or
        product id as `device` names one (`asked`), and keep it in config.json for the next
        start, a choice that stands when its save fails (Daemon.persist); every attached
        mouse is still driven."""
        if not self.attached:
            return {"ok": False, "error": self.unattached.none_attached()}
        text = name_text(request["value"], "mouse_select")
        attached = asked(self.attached, text, self.unattached.found)
        self.selected, picked = attached.product, f"{attached.product:04x}"
        log.info("showing %s", attached.name)
        if self.d.config.mouse_selected != picked:
            self.d.config.mouse_selected = picked
            self.d.persist()
        return {"ok": True, "selected": picked, "name": attached.name}

    def _cmd_mouse_host_mode(self, request: dict) -> dict:
        """HOST mode on or off for the mice with a spy HOST mode drives: a setting, saved and in
        effect at once. The reply's note names a mouse the switch did not take on and the step
        that tries again."""
        value = switch_value(request.get("value"), "mouse_host_mode")
        self.d.save_setting(mouse_host_mode=value)
        self.host_mode_changed(value)
        reply = {"ok": True, "mouse_host_mode": value,
                 # host_error is an earlier arm's when the switch is off.
                 "mice": [{"product": f"{m.product:04x}", "name": m.name, "host": m.host,
                           "error": m.host_error if value else None}
                          for m in self.attached if m.spy is not None]}
        stuck = [m for m in self.attached if self.stuck_in_host_mode(m)]
        if value:
            # One not answering is set up in full once it answers (cut_short), and a
            # host_error it has can be an earlier arm's.
            refused = [f"{self.named(m)}: {m.host_error}" for m in self.attached
                       if not m.host and m.host_error and not m.silent]
            said = [not_taken_words("; ".join(refused), len(refused) > 1)] if refused else []
            said += [stuck_in_host([self.named(m)], host_why(True, m.host_error)) for m in stuck]
            return with_note(reply, "; ".join(said))
        return with_note(reply, stuck_in_host([self.named(m) for m in stuck]) if stuck else None)
