"""The keyboard's idle notices and its light level: each idle or wake notice answered and the
first of a train acted on (take_notice), the claim's own wake notice and the later copies of an
idle train put back left unanswered (expect_claim_wake, stale_idle_notice), the idle times
written at the claim (claimed), after a change or another keyboard's wake (wake_for_change,
wake_with_another, idle_changed) and where owed (tend_idle), the dark idle_off_after after the
dim, a dim held back for another keyboard in use acted on once none is and the others lit with
a keyboard back (tend_idle), the level the lock and the idle notices set (set_lights,
keyboard_awake), and the dark before a suspend (dark_for_sleep). The levels are the
controller's (`Keyboard.lights`, `idle_lights`) and the lock's, every keyboard's
(`Keyboards.lock_lights`), which every repaint reads."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from ...hidpp import DeviceGone, HidppError, HidppFailure
from ...keyboard import MODEL_OF as KEYBOARDS
from ...lighting import (
    DARK,
    DIM,
    IDLE_NOTICE,
    IDLE_WAKE_EVENT,
    LEVEL_WORDS,
    LIT,
    WAKE_NOTICE,
    Lighting,
)
from ..shared import darker, unanswered
from .notes import LIGHTS_DOWN_NOTE, NO_LIGHTING, named_words

if TYPE_CHECKING:
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: What `idle` says when the keyboard refused the new times or their write failed, by its
#: model's name; not owed: its next claim sends them again (claimed), which a keyboard that
#: keeps its own times refuses too.
IDLE_TIMES_NOTE = ("saved; the {} did not take the new times, and they are sent again when it "
                   "is next set up")

#: What `idle` says when the keyboard is not attached: given at the claim. Not for a model lit
#: through 0x8070's zones, which has no idle times (NO_IDLE_TIMES_NOTE). The model's name goes
#: in, as the keyboard's notes put it (notes.KEYBOARD_AWAY).
KEYBOARD_AWAY_NOTE = "saved; the {} is not attached, and gets the new times when it is back"

#: The same for a keyboard that did not answer: owed (times_owed), or given at its cable's
#: claim when the look for the cable dropped the receiver route (Keyboard.keyboard_unanswered).
KEYBOARD_SILENT_NOTE = "saved; the {} is not answering, and gets the new times once it answers"

#: What `idle` says for a keyboard attached without the lighting (0x8071) the times go to.
NO_LIGHTING_TIMES_NOTE = f"saved; {NO_LIGHTING}, so the keyboard does not get the new times"

#: The same for a keyboard lit through 0x8070's zones, which keeps no idle times and sends no
#: idle notice (keyboard_zones), by its model's name.
NO_IDLE_TIMES_NOTE = "saved; the {} has no idle times, so it does not dim while idle"

# Each idle or wake notice (0x8071's IDLE_WAKE_EVENT) comes as a train of eight over about 4 s until
# answered. A notice of the same kind within this of the previous copy taken, answered or left
# unanswered on purpose (take_notice), is the same train: the last gap inside a train is
# 2.0 s, the shortest seen between trains 27 s. Timed from that copy, not the last repaint,
# which the eighth copy outlasts.
EVENT_TRAIN_GAP = 3.0

#: An idle notice sooner than the idle time less this after the idle times were written (fn 7)
#: is stale: the write restarts the keyboard's count, so the notice predates it. Two seconds
#: covers the firmware's rounding (Lighting.set_power_times).
STALE_IDLE_MARGIN = 2.0

#: A lighting change rewrites the idle times (fn 7) to restart the idle count, as G HUB does, at
#: most once this often, so a color drag does not send fn 7 each frame. A skipped write lets the
#: keyboard dim up to 2 s early, a fifth of IDLE_RANGE's 10 s minimum. An idle-dimmed keyboard
#: has its first change written unless a change's write failed under 2 s before: a write the
#: keyboard took that recent would make its notice stale.
IDLE_RESTART_INTERVAL = 2.0
#: After a lighting change wakes the keyboard from idleness, the wake its times write brings ends
#: after the notice and on the G915 loses a per-key paint sent before then: a repaint at the
#: notice stayed dark, one 0.4 s later showed (measured 2026-09-26).
WAKE_REPAINT_DELAY = 0.5

#: How long after a claim a wake notice is taken for the claim's own, left unanswered
#: (expect_claim_wake): it comes within 1.5 s of the claim, and the loop can read it later
#: while another device connects, 2 s after the connect's line on 2026-10-23. A wake notice
#: later than this is answered: after the claim's 05 none comes until an idle notice is
#: answered, which ends the claim's own first.
CLAIM_WAKE_WINDOW = 10.0

#: Why a lighting change lit a keyboard, for the journal (wake_for_change).
CHANGE_WAKE = "a lighting change woke the keyboard"

#: Why a keyboard set up or heard again lit the others, by its model's name (tend_idle).
BACK_WAKE = "the {} is back"


class Idle:
    """The keyboard's idle notices and the light level they and the lock set."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: When the keyboard last dimmed for idleness (dim_for_idle, which alone sets the idle
        #: level dim): it goes dark idle_off_after later (idle_deadline), read only while dim.
        self._idle_since = 0.0
        #: When the idle times were last written (fn 7), which restarts the idle count, or None
        #: when the keyboard refused the claim's: an idle notice sooner than the idle time after
        #: is stale.
        self._times_written_at: float | None = None
        #: The idle time that write carried, which the keyboard counts by until it takes
        #: another: idle_dim_after runs ahead of it after a refused or owed write.
        self._dim_written = 0
        #: A stale idle notice since that write was put back; the next is only answered.
        self._stale_put_back = False
        #: The idle train being read had its first copy put back (stale_idle_notice): its later
        #: copies, sent before that answer landed, are left unanswered, since their answer
        #: (fn 5 `03`) would turn the idle notice off again.
        self._train_put_back = False
        #: The keyboard's last answered notice was an idle one (fn 5 `03`), so once it is idle a
        #: times write wakes it, lit or not: under never dim, or after a stale notice left answered.
        self._idle_answered = False
        #: The last notice answered since the claim, its answer taken or refused (answer_notice),
        #: was an idle one not put back (stale_idle_notice), whether it moved the level or not:
        #: the keyboard has not been typed on since its idle time ran out (idle_now). Kept apart
        #: from `_idle_answered`, which is the state of fn 5 and which a refused answer leaves
        #: as it was.
        self._idle_heard = False
        #: When a lighting change last wrote or tried the idle times, for IDLE_RESTART_INTERVAL.
        self._change_times_at = float("-inf")
        #: A lighting change or new idle times woke the keyboard from idleness, whose wake loses a
        #: per-key paint sent before it ends: keyboard_woke has it painted again at `_repaint_due`.
        self._paint_at_wake = False
        self._repaint_due: float | None = None
        #: The idle times a keyboard attached but marked not answering, or not answering their
        #: write, did not get (write_idle_times): written once it answers. A claim writes them,
        #: so it clears this, and a teardown drops it.
        self.times_owed = False
        #: When the keyboard last sent an idle or wake notice that was answered or left
        #: unanswered on purpose (take_notice), or was claimed (expect_claim_wake), which brings
        #: a wake notice within 1.5 s; and its kind, since the other kind starts a train of its
        #: own.
        self._last_rgb_event = 0.0
        self._last_rgb_kind: int | None = None
        #: The claim's own wake notice is still to come (expect_claim_wake), left unanswered
        #: within CLAIM_WAKE_WINDOW of the claim. The first idle or wake notice ends it.
        self._claim_wake_owed = False
        #: Its last idle notice dimmed nothing, for another keyboard in use (keyboard_went_idle),
        #: and its answer turned the notice off until a key: dimmed once none is (tend_idle).
        self._dim_held = False
        #: Set up, or heard again after it was marked not answering: once the pass's reports
        #: are read, each other keyboard below lit is lit with it unless it went idle since
        #: (tend_idle), as a key would, which after a claim sends no notice.
        self.back_owed = False

    # `lighting` for code reached only while the keyboard has 0x8071's, which alone has idle
    # times and notices (keyboard_zones.power_modes), after a check made in another function,
    # which the type checker does not follow. Nothing checks this cast.

    @property
    def _lighting(self) -> Lighting:
        return cast("Lighting", self.keyboard.lighting)

    def deadlines(self) -> list[float]:
        """When the idle keyboard next needs the loop: the dark after the dim, and the repaint a
        wake owes."""
        return [at for at in (self.idle_deadline(), self._repaint_due) if at is not None]

    # -- the claim's idle times ----------------------------------------------

    def claimed(self) -> None:
        """At the claim (Link.claim_keyboard), after its lighting claim: the idle and sleep times
        (fn 7) written again, with what the last times write left dropped, since link setup
        reloads them from the onboard profile, and the others' wake owed (back_owed). The
        keyboard's error reply leaves it claimed with its own times; other failures go to the
        caller."""
        self._times_written_at, self._paint_at_wake, self._repaint_due = None, False, None
        self.times_owed = self._idle_answered = self._idle_heard = False
        self.back_owed = True
        try:
            self.send_idle_times()
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the %s refused its idle times (%s); it keeps its own",
                     self.keyboard.model_name(), exc)

    def expect_claim_wake(self) -> None:
        """Take the wake notice a claim just made brings within 1.5 s for one already answered:
        it moves no level, and it is left unanswered (take_notice). The claim's 07 brings it,
        and the claim's own writes after, its 05 and fn 8 `01 01`, are its answer, as G HUB's
        claim answers it, before its paint (G HUB on the wired G515 TKL, capture A2). Answered
        again after the connect's paint, it took that paint back on the G915 X LIGHTSPEED on
        its cable, and the board showed its own lighting (2026-10-23)."""
        self._last_rgb_event, self._last_rgb_kind = time.monotonic(), WAKE_NOTICE
        self._claim_wake_owed = True

    def send_idle_times(self) -> None:
        """Write the configured idle times (fn 7) and note when, for stale_idle_notice."""
        self._lighting.set_power_times(self.d.config.idle_dim_after, self.d.config.idle_off_after)
        self._times_written_at, self._stale_put_back = time.monotonic(), False
        self._dim_written = self.d.config.idle_dim_after

    def keyboard_awake(self) -> None:
        """The keyboard's own level back to lit, with no dark due and no dim held back, and
        shown at the lock's level (Keyboards.lock_lights), not painted: the caller paints."""
        keyboard = self.keyboard
        keyboard.idle_lights, self._dim_held = LIT, False
        keyboard.lights = self.d.keyboards.lock_lights

    # -- the idle notices ----------------------------------------------------

    def take_notice(self, data: bytes) -> None:
        """An idle notice (data 00) or a wake notice (01) from the keyboard.

        Every copy is answered at once, which stops the train. A train's first answered copy
        moves the keyboard's own level through set_lights, which paints only when what the board
        shows changes, so an animation is not restarted for nothing. An unanswered copy does not
        count, so the next, queued meanwhile, can still be the first. A notice of the other kind
        starts its own train. The claim's own wake notice is left unanswered
        (expect_claim_wake), and so are the later copies of an idle train whose first was put
        back (stale_idle_notice), whose answer would take the put-back back.
        """
        kind = data[0]
        if kind not in (IDLE_NOTICE, WAKE_NOTICE):
            log.info("0x8071 event addr 0x%02x data %s: neither idle nor wake; left alone",
                     IDLE_WAKE_EVENT, data[:4].hex(" "))
            return
        now = time.monotonic()
        claim_wake, self._claim_wake_owed = self._claim_wake_owed, False
        if (claim_wake and kind == WAKE_NOTICE
                and now - self._last_rgb_event < CLAIM_WAKE_WINDOW):
            log.debug("the claim's own wake notice left unanswered: the claim's writes "
                      "answered it")
            self._last_rgb_event = now
            return
        if (kind == IDLE_NOTICE and self._train_put_back and self._last_rgb_kind == kind
                and now - self._last_rgb_event < EVENT_TRAIN_GAP):
            log.debug("a later copy of the idle notice put back left unanswered")
            self._last_rgb_event = now
            return
        self._train_put_back = False
        if not self.answer_notice(kind):
            return
        previous, self._last_rgb_event = self._last_rgb_event, now
        before, self._last_rgb_kind = self._last_rgb_kind, kind
        if kind == before and now - previous < EVENT_TRAIN_GAP:
            log.debug("0x8071 event addr 0x%02x data %s (same burst, answered, not repainting)",
                      IDLE_WAKE_EVENT, data[:4].hex(" "))
            return
        if kind == IDLE_NOTICE:
            self.keyboard_went_idle()
        else:
            self.keyboard_woke()

    def answer_notice(self, kind: int) -> bool:
        """Answer an idle or wake notice. False when the keyboard did not answer, which marks it
        not answering and leaves the level as it is; after an error reply the level still
        moves, and the notice still counts as heard (idle_now). DeviceGone goes to the caller
        (Keyboard.pump)."""
        try:
            if kind == IDLE_NOTICE:
                self._lighting.answer_idle()
            else:
                self._lighting.answer_wake()
        except DeviceGone:
            raise
        except HidppFailure as exc:
            what = "idle" if kind == IDLE_NOTICE else "wake"
            name = self.keyboard.model_name()
            if unanswered(exc):
                log.info("the %s's %s notice could not be answered: %s", name, what, exc)
                self.keyboard.keyboard_unanswered(exc)
                return False
            if isinstance(exc, HidppError):
                log.warning("the %s refused the answer to its %s notice: %s", name, what, exc)
            else:
                log.warning("could not answer the %s's %s notice: %s", name, what, exc)
        else:
            self._idle_answered = kind == IDLE_NOTICE
        self._idle_heard = kind == IDLE_NOTICE
        return True

    def keyboard_went_idle(self) -> None:
        """The first answered copy of an idle notice: dim now and dark idle_off_after later
        (idle_deadline), unless never dim is on. Only from lit, so a repeat notice does not put
        the dark off. With another keyboard driven and typed on since its own idle notice
        (Keyboards.in_use), nothing dims yet, and the dim is held back for tend_idle, since the
        answer turned the notice off until a key; once none is, every keyboard dims together
        (dim_together)."""
        why = self.journal_words("the keyboard is idle")
        if self.d.config.never_dim:
            log.info("%s; answered, not dimmed: never dim is on", why)
            return
        if self.stale_idle_notice():
            return
        if self.keyboard.idle_lights != LIT:
            log.info("%s; answered, already %s", why, LEVEL_WORDS[self.keyboard.idle_lights])
            return
        in_use = self.d.keyboards.in_use(self.keyboard)
        if in_use is not None:
            self._dim_held = True
            log.info("%s; answered, not dimmed: the %s is in use", why, in_use.model_name())
            return
        self.dim_together(why)

    def dim_together(self, why: str) -> None:
        """Dim this keyboard and each other driven that is lit and whose idle level is known
        (Keyboard.idle_level_known), the others said at debug: the keyboards dim together (the
        owner's decision of 2026-10-21, "Together")."""
        for keyboard in self.d.keyboards.driven():
            if (keyboard is not self.keyboard and keyboard.idle_level_known()
                    and keyboard.idle_lights == LIT):
                keyboard.idle.dim_for_idle(why, quiet=True)
        self.dim_for_idle(why)

    def dim_for_idle(self, why: str, quiet: bool = False) -> None:
        """Dim for idleness now, and dark idle_off_after later (idle_deadline), with no dim held
        back; `quiet` says it at debug, for each keyboard dimmed with another (dim_together)."""
        self._idle_since, self._dim_held = time.monotonic(), False
        if not self.set_lights(DIM, why, quiet=quiet):
            log.log(logging.DEBUG if quiet else logging.INFO, "%s; answered, the lights stay %s",
                    why, LEVEL_WORDS[self.keyboard.lights])

    def idle_now(self) -> bool:
        """Whether the keyboard has not been typed on since its idle time ran out
        (`_idle_heard`, Keyboards.in_use)."""
        return self._idle_heard

    def journal_words(self, words: str) -> str:
        """`words` for the journal, named by this keyboard's model where more than one keyboard
        is driven (notes.named_words)."""
        return named_words(self.keyboard.model_name(), words, len(self.d.keyboards.driven()) > 1)

    def wake_with_another(self, why: str) -> None:
        """Lit because another keyboard woke or came back (Keyboards.wake_others): its idle times
        written again, which wakes it from its own idleness and restarts its count, and its
        level lit, painted again once that wake is over (keyboard_woke). A write under
        IDLE_RESTART_INTERVAL old, as the `idle` command's to each keyboard, woke it already
        and is not repeated. The write counts as a lighting change's (wake_for_change), so a
        change waking every keyboard writes it once. Said at debug: a keyboard that woke for a
        key or a change says it itself."""
        now = time.monotonic()
        written, self._change_times_at = self._times_written_at, now
        if written is None or now - written >= IDLE_RESTART_INTERVAL:
            self.write_idle_times()
        self.set_lights(LIT, why, quiet=True)

    def stale_idle_notice(self) -> bool:
        """True for an idle notice sent before the idle times were last written (sooner after
        the write than the idle time it carried less STALE_IDLE_MARGIN); it does not dim.

        Its answer turned the idle notice off, so the keyboard is put back as a wake notice's
        answer leaves it (answer_wake) and its idle notice comes when the time runs out.
        Rewriting the times does not do it: a times write wakes only a keyboard that is idle,
        and fn 5 stayed `03 03` until a key. Once per times write: a second stale notice is
        left answered, for the next wake notice's answer to put back. The later copies of the
        train put back are left unanswered (take_notice, _train_put_back).
        """
        if self._times_written_at is None:
            return False
        age = time.monotonic() - self._times_written_at
        if age >= self._dim_written - STALE_IDLE_MARGIN:
            return False
        said = (f"the keyboard's idle notice came {age:.1f} s after its idle times were "
                f"written, sooner than {self._dim_written} s can run out: answered, "
                f"not dimmed")
        if self._stale_put_back:
            log.info("%s; its idle notice left off until its next wake notice", said)
            return True
        self._stale_put_back = True
        try:
            self._lighting.answer_wake()
        except DeviceGone:
            raise
        except HidppFailure as exc:
            if unanswered(exc):
                log.info("%s; its idle notice could not be put back on: %s", said, exc)
                self.keyboard.keyboard_unanswered(exc)
            elif isinstance(exc, HidppError):
                log.warning("%s; putting its idle notice back on was refused: %s", said, exc)
            else:
                log.warning("%s; its idle notice could not be put back on: %s", said, exc)
            return True
        self._idle_answered = self._idle_heard = False
        self._train_put_back = True
        log.info("%s, and its idle notice put back on", said)
        return True

    def keyboard_woke(self) -> None:
        """The first answered copy of a wake notice: a key pressed on a keyboard whose idle
        notice was answered, or the idle times written again, which brings it about 70 ms later.
        Lit, with a full apply when that changes what the board shows; the lock's level stands.
        After a lighting change or new idle times woke the keyboard, the board is painted again
        once the wake is over (_repaint_due). A dim held back is dropped: the keyboard was typed
        on, or took idle times, since its idle notice."""
        self._dim_held = False
        written = self._times_written_at
        for_times = written is not None and time.monotonic() - written < EVENT_TRAIN_GAP
        repaint, self._paint_at_wake = self._paint_at_wake and for_times, False
        why = self.journal_words("the keyboard woke for its idle times" if for_times
                                 else "a key woke the keyboard")
        changed = self.set_lights(LIT, why)
        if repaint:
            log.info("%s; painting again in %.1f s, once the wake is over", why, WAKE_REPAINT_DELAY)
            self._repaint_due = time.monotonic() + WAKE_REPAINT_DELAY
        elif not changed:
            log.info("%s; answered, the lights stay %s", why, LEVEL_WORDS[self.keyboard.lights])
        self.d.keyboards.wake_others(self.keyboard, why)

    def idle_deadline(self) -> float | None:
        """When a keyboard dimmed for idleness goes dark, or None. Read from the config each
        time, so a changed idle_off_after counts from the dim."""
        if self.keyboard.idle_lights != DIM:
            return None
        return self._idle_since + self.d.config.idle_off_after

    def tend_idle(self) -> None:
        """Once a pass, the work this keyboard's idleness and the writes owed to it have due."""
        self._light_others()
        self._dim_held_back()
        self._give_owed()
        self._repaint_after_wake()
        self._dark_when_due()

    def _light_others(self) -> None:
        """The others lit with a keyboard set up or heard again that has not gone idle since
        (back_owed)."""
        if not self.back_owed:
            return
        self.back_owed = False
        keyboard = self.keyboard
        if keyboard.idle_level_known() and not self.idle_now():
            self.d.keyboards.wake_others(keyboard, BACK_WAKE.format(keyboard.model_name()))

    def _dim_held_back(self) -> None:
        """A dim held back for another keyboard in use (_dim_held), once none is."""
        keyboard = self.keyboard
        if (self._dim_held and not self.d.config.never_dim and keyboard.idle_level_known()
                and self.d.keyboards.in_use(keyboard) is None):
            # Its idle notice is off until a key (answer_idle), so it sends no other to dim it.
            self.dim_together(self.journal_words("the keyboard is idle, and no other is in use "
                                                 "now"))

    def _give_owed(self) -> None:
        """An apply and idle times owed to a keyboard that answers again, asked on the probe's
        budget at most once per MOUSE_SILENT_RECHECK."""
        keyboard = self.keyboard
        if keyboard.apply_owed and keyboard.lighting is not None and keyboard.keyboard_answers():
            log.info("showing the %s the lighting it missed", keyboard.model_name())
            keyboard.apply_profile()
        if self.times_owed and keyboard.keyboard_answers():
            log.info("giving the %s the idle times it missed", keyboard.model_name())
            self.write_idle_times()

    def _repaint_after_wake(self) -> None:
        """The repaint a wake owes (_repaint_due), with a drag's latest frame over it, the
        leader's on a keyboard that shows the leader's lighting, or the apply owed in its place
        to a keyboard marked not answering by then."""
        if self._repaint_due is None or self._repaint_due > time.monotonic():
            return
        self._repaint_due = None
        keyboard = self.keyboard
        if keyboard.lights != DARK and keyboard.keyboard_answering():
            log.info("the %s's wake is over; painting it again", keyboard.model_name())
            frame = keyboard.last_preview
            leader = self.d.keyboard
            if frame is None and keyboard in self.d.keyboards.sharing(leader):
                # A drag on the leader, whose lighting this one shows.
                frame = leader.last_preview
            keyboard.apply_profile()
            if frame is not None and keyboard.keyboard_answering():
                # A drag in progress: its latest frame goes back over the repaint. Through
                # the socket's handler, since nothing here may raise into the loop.
                self.d.handle_command(frame)
        elif keyboard.lights != DARK and keyboard.dev is not None:
            # Marked since the wake: the paint it lost is owed, as write_due_leds owes one.
            keyboard.apply_owed = True

    def _dark_when_due(self) -> None:
        """Dark, the claim held, once the idle deadline passes (idle_deadline)."""
        due = self.idle_deadline()
        if due is None or due > time.monotonic():
            return
        why = self.journal_words(f"the keyboard has been idle {self.d.config.idle_off_after} s "
                                 "since it dimmed")
        if not self.set_lights(DARK, why):
            log.info("%s; the lights stay off", why)

    # -- the idle times ------------------------------------------------------

    def write_idle_times(self) -> str | None:
        """Write the idle times to the keyboard again: None when it took them, or a note saying
        why not. The write counts as activity (Lighting.set_power_times).

        Times a keyboard attached but marked not answering, or not answering their write, did
        not get are owed (times_owed) and written once it answers (tend_idle): it keeps its
        handle, and no claim comes when it answers. A keyboard away gets them at its claim. A
        refusal is not owed, since it would be refused again at every pass, nor are times for a
        keyboard attached without lighting, or for a model lit through 0x8070's zones, attached
        or away, which has nowhere to take them.
        """
        keyboard = self.keyboard
        self.times_owed = False
        if keyboard.lighting is None and keyboard.dev is not None:
            return NO_LIGHTING_TIMES_NOTE.format(keyboard.model_name())
        product = keyboard.keyboard_product
        if product is not None and KEYBOARDS[product].zone_lighting:
            return NO_IDLE_TIMES_NOTE.format(keyboard.model_name())
        if not keyboard.keyboard_answering():
            self.times_owed = keyboard.dev is not None
            note = KEYBOARD_SILENT_NOTE if self.times_owed else KEYBOARD_AWAY_NOTE
            return note.format(keyboard.model_name())
        try:
            with keyboard.keyboard_writing():
                self.send_idle_times()
        except DeviceGone:
            log.info("the %s went away while taking its idle times", keyboard.model_name())
            keyboard.teardown_device()
            return KEYBOARD_AWAY_NOTE.format(keyboard.model_name())
        except HidppFailure as exc:
            log.info("the %s did not take its idle times: %s", keyboard.model_name(), exc)
            if unanswered(exc):
                # Marked by keyboard_writing; on the receiver the cable look may have let it go.
                self.times_owed = keyboard.dev is not None
                return KEYBOARD_SILENT_NOTE.format(keyboard.model_name())
            return IDLE_TIMES_NOTE.format(keyboard.model_name())
        if keyboard.idle_lights != LIT or self._idle_answered:
            # Taken by a keyboard whose idle notice was answered, below lit or not, the times
            # wake it, and that wake loses a per-key paint sent before it ends: painted again
            # after it (keyboard_woke).
            self._paint_at_wake = True
        return None

    def wake_for_change(self, paint: bool = False) -> bool:
        """Treat a lighting change from the window or the CLI as activity at the keyboard, as
        G HUB does: rewrite the idle times, which restarts the idle count and wakes a keyboard
        that is idle, lit or not, painted again once that wake is over (keyboard_woke).

        Called by each command that paints, after its checks and right before its paint, so a
        refused command writes nothing. This keyboard's write comes at most once per
        IDLE_RESTART_INTERVAL, and none under never dim, or to a keyboard away or not
        answering. A write the keyboard did not take moves no level and never fails the change.

        A write it took on a keyboard below lit sets the idle level lit, painted here with
        `paint`, for a command whose own write does not repaint the whole board and the M and MR
        LEDs (`preview`, `brightness`). True when the level changed what shows and nothing has
        painted it yet, which the `paint` command answers with a full apply. The lock's level
        stands.

        Then each other keyboard driven below lit is lit (Keyboards.wake_others), whatever this
        keyboard's own state, since the keyboards dim together: after this one's write and,
        with `paint`, its paint, so a slow one does not delay it. Under never dim none is below
        lit.
        """
        woke = self._wake_own_for_change(paint)
        self.d.keyboards.wake_others(self.keyboard, CHANGE_WAKE)
        return woke

    def _wake_own_for_change(self, paint: bool) -> bool:
        """wake_for_change on this keyboard alone, with its answer."""
        keyboard = self.keyboard
        if (self.d.config.never_dim or keyboard.lighting is None
                or not keyboard.keyboard_answering()):
            return False
        now = time.monotonic()
        if now - self._change_times_at < IDLE_RESTART_INTERVAL:
            return False
        self._change_times_at = now
        if self.write_idle_times() is not None:
            return False
        if keyboard.idle_lights == LIT:
            log.debug("a lighting change: the idle times written again, which restarts "
                      "the keyboard's idle count")
            return False
        if self.set_lights(LIT, CHANGE_WAKE, paint=paint):
            return not paint
        log.info("%s; the lights stay %s", CHANGE_WAKE, LEVEL_WORDS[keyboard.lights])
        return False

    def idle_settings(self) -> tuple[int, int, bool]:
        """(idle_dim_after, idle_off_after, never_dim), to tell a change by."""
        return self.d.config.idle_dim_after, self.d.config.idle_off_after, self.d.config.never_dim

    def idle_changed(self, before: tuple[int, int, bool], paint: bool) -> str | None:
        """Act on idle settings changed from `before` on this keyboard: new times go to it,
        waking one that is idle, repainted after the wake (keyboard_woke); never dim turned on
        lights it, painted unless `paint` is False; never dim turned off waits for the next idle
        notice. The answer is write_idle_times' note, or None."""
        note = None
        if before[:2] != self.idle_settings()[:2]:
            note = self.write_idle_times()
        if self.d.config.never_dim and not before[2] and self.keyboard.idle_lights != LIT:
            if paint:
                self.set_lights(LIT, "never dim turned on")
            else:
                self.keyboard_awake()
        return note

    # -- the light level -----------------------------------------------------

    def set_lights(self, level: str, why: str, paint: bool = True, quiet: bool = False,
                   lock_before: str | None = None) -> bool:
        """Paint at the darker of the lock's level (Keyboards.lock_lights) and the idle
        keyboard's, set to `level` first, unless the lights are at it already, so the unlocked
        readings at start do not repaint. With `lock_before`, Keyboards.set_lights has set the
        lock's level to `level`, from `lock_before`, instead. True when they changed;
        `paint` False leaves the paint to the caller. `quiet` says it at debug, for a keyboard
        beside the one that says it.

        A keypress wakes both, each through its own signal: Mutter's input watch lights the
        lock's level and the keyboard's wake notice its own."""
        keyboard = self.keyboard
        lock = self.d.keyboards.lock_lights
        if lock_before is None:
            keyboard.idle_lights = level
        shown = darker(lock, keyboard.idle_lights)
        if shown == keyboard.lights:
            if paint and lock_before is not None and lock != lock_before:
                # The idleness holds the board where it is, but a synced mouse and a synced
                # headset's lights follow the idleness only once the whole desktop is idle
                # (at_level). Said, or a blank over a dark board leaves no line in the journal.
                log.log(logging.DEBUG if quiet else logging.INFO,
                        "%s; the %s's lights stay %s, for idleness", why, keyboard.model_name(),
                        LEVEL_WORDS[shown])
                keyboard.sync_devices(keyboard.shown_color())
            return False
        log.log(logging.DEBUG if quiet else logging.INFO, "%s; the %s's lights %s", why,
                keyboard.model_name(), LEVEL_WORDS[shown])
        keyboard.lights = shown
        if paint:
            keyboard.apply_profile()
        return True

    def dark_for_sleep(self) -> str:
        """Dark before a suspend. The answer is what happened, for the journal. A board shown
        dark with nothing owed or refused is asked nothing; otherwise the keyboard is asked
        first, on the probe's budget. With a dark, or a change made to a dark board, still
        owed to a keyboard that was not answering (`apply_owed`), or a dark it refused
        (`last_apply_error`), the dark is written here, not taken for one it shows."""
        keyboard = self.keyboard
        if (keyboard.lights == DARK and not keyboard.apply_owed
                and keyboard.last_apply_error is None):
            # Dark for idleness, which a synced mouse and a synced headset's lights follow only
            # once the desktop is idle (at_level).
            keyboard.sync_devices(keyboard.shown_color())
            # A dark no board shows: the keyboard left once its board was dark and
            # keyboard_awake kept the lock's level, or it has no lighting.
            if keyboard.dev is None:
                return "keyboard lights not turned off (the keyboard is away)"
            if keyboard.lighting is None:
                return NO_LIGHTING.format(keyboard.model_name())
            return "keyboard lights already off"
        keyboard.keyboard_answers()
        keyboard.lights = DARK
        # The apply darkens a synced mouse and a synced headset's lights, a keyboard without
        # lighting (None) or not.
        failed = keyboard.apply_profile()
        if failed == LIGHTS_DOWN_NOTE[DARK]:
            return "keyboard lights off"
        if failed is None:
            return NO_LIGHTING.format(keyboard.model_name())
        # apply_profile answers in a reply's words ("the change is saved"), untrue at a suspend.
        why = ("the keyboard is away" if keyboard.dev is None
               else "the keyboard is not answering" if not keyboard.keyboard_answering()
               else failed)
        return f"keyboard lights not turned off ({why})"
