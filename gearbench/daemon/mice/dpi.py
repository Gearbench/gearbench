"""The mice's DPI, and a game profile's own stages and rate: the DPI a user chose and its
return after a power cycle (`remember_dpi`, `restore_dpi`), the DPI buttons and the shift
(`dpi_step`, `dpi_shift`), the stage a mouse's own DPI button moved it to
(`take_stage_event`), the stages and rate in force (`stages_in_force`, `rate_for`), and a
loaded game profile's own, sent live to a mouse in HOST mode (`tune`, `retune`, `send_rate`).
What is written here goes to a mouse's working memory, to the game profile's file and, for
a DPI the user chose outside a game profile's own stages, to config.json (`remember_dpi`,
`forget_dpi`), never to the mouse's onboard profile."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from ...config import escaped
from ...library_files import slug
from ...mouse import PROFILE_SECTOR, MouseError, place_stages, profile_words
from ...onboard import stage_dpi
from ...profiles import REPORT_RATES, GameProfile, MouseTuning
from ..shared import MOUSE_PROBE, whole_number
from .attached import AttachedMouse, dpi_keys, own_stages, same_place, stage_request

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

#: Wait after a DPI write with a level byte: the flash ends dark and loses writes.
DPI_FLASH_REPAINT = 5.0


class Dpi:
    """Every attached mouse's DPI, and the stages and report rate in force on it."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice
        #: By model (Mouse.backup_name) and game profile (its slug), the DPI last chosen under
        #: that profile's own stages since the daemon started, which its next load goes back
        #: to (chosen_under); config.mouse_dpi keeps the one outside them (remember_dpi).
        self.game_dpi: dict[tuple[str, str], int] = {}

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"mouse_dpi": self._cmd_mouse_dpi}

    def send_rate(self, attached: AttachedMouse) -> bool:
        """Send the report rate in force live (0x8060 fn 2), since no profile runs in HOST mode:
        at an arm, and after a restore or any other HOST-mode profile 1 write. The loaded game
        profile's own rate on a mouse the daemon runs in HOST mode (runs_host), else profile
        1's: one counted there with host mode off awaits its hand-back. False when it was not
        taken."""
        tuning = self.tuning_for(attached) if self.mice.runs_host(attached) else None
        rate = self.rate_for(attached, tuning)
        if rate is None or "report_rate" not in attached.mouse.capabilities:
            return True
        attached.live_rate = None
        try:
            self.mice.mouse_call(lambda: attached.mouse.set_live_report_rate(rate, **MOUSE_PROBE),
                                 target=attached)
        except MouseError as exc:
            log.info("could not set %s's report rate to %s Hz: %s", attached.name, rate, exc)
            return False
        attached.live_rate = rate
        if attached.reading is not None:
            attached.reading["report_rate"] = rate
        return True

    def remembered_dpi(self, attached: AttachedMouse) -> int | None:
        """The DPI remembered for this mouse on either of its routes, or None."""
        return next((self.d.config.mouse_dpi[key] for key in dpi_keys(attached.product)
                     if key in self.d.config.mouse_dpi), None)

    def known_dpi(self, attached: AttachedMouse) -> int | None:
        """The DPI a mouse is on as far as the daemon knows, with nothing sent:
        the last reading, which its own writes keep up to date, else the one
        chosen for it, which restore_dpi put back at the attach."""
        dpi = (attached.reading or {}).get("dpi")
        return dpi if dpi is not None else self.remembered_dpi(attached)

    def remember_dpi(self, dpi: int, found: AttachedMouse) -> None:
        """Keep the DPI the user chose, for restore_dpi to put back after a power cycle. Under a
        game profile's own stages it is that profile's, kept in memory only (game_dpi, by
        model); outside them, or on own stages the loaded profile no longer has (a move off
        them owed), config.mouse_dpi keeps it, under this route's key only. One chosen
        outside them replaces the DPI read before a move to them that is owed, or whose retry
        was refused, as the one to go back to when they go (the owner's decision of 2026-10-07),
        and the one kept from before a load whose own stages were dropped under it. Not called
        for the DPI shift: held only as long as the paddle is, it is not a choice.
        """
        # Chosen since a stage edit's move went unanswered: its retry maps from this one.
        found.dpi_before_edit = None
        # The mouse is on own stages that the loaded profile still has. While a move off them
        # is owed, as at an unload the mouse did not answer, it is on stages the profile has
        # not, and the DPI chosen is the one outside own stages, which that move leaves.
        if (own_stages(self.in_force(found)) is not None
                and own_stages(self.tuning_for(found)) is not None):
            self.game_dpi[cast(tuple[str, str], self.game_key(found))] = dpi
            return
        # Forgotten, not set: desktop_dpi falls back on the DPI remembered below, and a retried
        # move to own stages reads the mouse again (wanted_dpi).
        found.dpi_before_tuning = found.dpi_before_load = None
        own, *siblings = dpi_keys(found.product)
        stale = [key for key in siblings if key in self.d.config.mouse_dpi]
        if self.d.config.mouse_dpi.get(own) == dpi and not stale:
            return
        self.d.config.mouse_dpi[own] = dpi
        for sibling in stale:
            del self.d.config.mouse_dpi[sibling]      # the same mouse on its other route
        self.d.persist()

    def forget_dpi(self, attached: AttachedMouse) -> None:
        """Forget the DPI remembered for this mouse on both routes: after a write changed profile
        1's stage list it may be an edited stage's old value, which restore_dpi would put back
        (ProfileWrites.follow_dpi)."""
        dropped = [self.d.config.mouse_dpi.pop(key, None) for key in dpi_keys(attached.product)]
        if any(value is not None for value in dropped):
            self.d.persist()

    def restore_dpi(self, found: AttachedMouse) -> bool:
        """Put the DPI back on a mouse that has just appeared or been set up again: it comes back
        from a power cycle on the stage it was last on in ONBOARD mode, and on the profile's
        default in HOST mode, neither of which need be the user's choice.

        The DPI is the remembered one. In HOST mode under a game profile's own stages it is the
        DPI last chosen under them, else their default stage, and the DPI outside them is kept
        for when they go (dpi_before_tuning). Stages edited while the mouse was away keep it on
        its stage, as retune does; the edited stage's DPI becomes the one chosen only once those
        stages are taken, so a retried setup maps it once. Stages that went out of force while
        it was away, or before an unanswered move was retried, put back the DPI from before
        them. Nothing is written when there is nothing to put back or the mouse already reads
        it. False when the read or the write failed, so the setup sets no cookie.
        """
        tuning = self.tuning_for(found) if self.mice.runs_host(found) else None
        stages, had = own_stages(tuning), own_stages(found.tuning)
        mapped = key = None
        if tuning is not None and stages is not None:
            kept = had is not None and found.tuning_load == self.d.games.loads
            key = self.game_key(found)
            if kept and had is not None and had != stages and key in self.game_dpi:
                # Edited while it was away. The DPI it reads is the power-on's, so the one
                # chosen says which stage it was on.
                mapped = same_place(self.game_dpi[key], had, stages)
            wanted = mapped or self.chosen_under(found, tuning, kept) or tuning.default
        else:
            wanted = self.desktop_dpi(found)
            found.dpi_before_load = None
        if wanted is None:
            self.take_tuning(found, tuning)
            return True
        try:
            have = self.mice.mouse_call(found.mouse.get_dpi, target=found)
            if stages is not None and had is None:
                # Read before a load's move that went unanswered, it stays: `have` may be the
                # power-on's.
                found.dpi_before_tuning = self.desktop_dpi(found) or have
            self.take_tuning(found, tuning)
            if mapped is not None and key is not None:
                self.game_dpi[key] = mapped
            if have == wanted:
                return True
            log.debug("%s came back on %s DPI; %s was chosen", found.name, have, wanted)
            # Without the level byte: a DPI put back never flashes, and after a power-on the
            # flash outlasted DPI_FLASH_REPAINT.
            applied = self.mice.mouse_call(
                lambda: found.mouse.set_dpi(wanted, indicate=False), target=found)
        except MouseError as exc:
            log.debug("could not restore %s to %s DPI: %s", found.name, wanted, exc)
            return False
        log.info("%s DPI restored to %s", found.name, applied)
        self.after_dpi_write(found, applied)
        return True

    def dpi_step(self, direction: int, target: AttachedMouse | None = None) -> None:
        found = target or self.mice.current
        if found is None:
            log.debug("DPI step ignored: mouse HID++ not connected")
            return
        stages = self.own_stages_in_force(found)
        applied = self.mice.mouse_call(found.mouse.step_dpi, direction, stages, target=found)
        log.info("%s DPI step %s: %d DPI", found.name, "up" if direction > 0 else "down",
                 applied)
        self.after_dpi_write(found, applied)
        # A choice, as retune's move: a held paddle's release must not put back the DPI before it.
        found.dpi_before_shift = None
        self.remember_dpi(applied, found)
        self.d.session.post_dpi_notice(f"{found.name}: {applied} DPI")

    def dpi_shift(self, pressed: bool, target: AttachedMouse | None = None) -> None:
        found = target or self.mice.current
        if found is None:
            return
        mouse = found.mouse
        shifted = self.stages_in_force(found)[1]
        stages = self.own_stages_in_force(found)
        if pressed:
            if shifted is None or found.dpi_before_shift is not None:
                return
            found.dpi_before_shift = self.mice.mouse_call(mouse.get_dpi, target=found)
            self.after_dpi_write(found, self.mice.mouse_call(
                lambda: mouse.set_dpi(shifted, stages=stages), target=found))
            log.debug("%s DPI shift: %d -> %d", found.name, found.dpi_before_shift, shifted)
        elif found.dpi_before_shift is not None:
            restore = found.dpi_before_shift
            # Dropped once answered: one not answered is owed (put_back_shift).
            applied = self.mice.mouse_call(lambda: mouse.set_dpi(restore, stages=stages),
                                           target=found)
            found.dpi_before_shift = None
            self.after_dpi_write(found, applied)
            log.debug("%s DPI shift released: back to %d", found.name, restore)

    def put_back_shift(self, attached: AttachedMouse,
                       why: str = "the shift held when its link went down") -> None:
        """Put back the DPI under a DPI shift whose release was lost with the link, or whose
        release's write the mouse did not answer, without the level byte; `why` is for the
        log. One not taken stays owed: the next link-up, a recheck that finds the setup kept,
        the teardown or the paddle's next release puts it back."""
        # Set: each caller checks it.
        held = cast(int, attached.dpi_before_shift)
        try:
            applied = self.mice.mouse_call(lambda: attached.mouse.set_dpi(held, indicate=False),
                                           target=attached)
        except MouseError as exc:
            log.info("%s: the DPI under the shift could not be put back: %s", attached.name, exc)
            return
        attached.dpi_before_shift = None
        log.info("%s back to %s DPI: %s", attached.name, applied, why)
        self.after_dpi_write(attached, applied)

    def take_stage_event(self, attached: AttachedMouse, slot: int) -> None:
        """The mouse's own DPI button moved it to stage `slot` of the profile it runs, outside
        HOST mode as the daemon runs it (runs_host; STAGE_EVENT). The stage's DPI is taken as
        dpi_step's is: the mouse's DPI for `mouse` and the tray, remembered for restore_dpi, and
        posted as a notice. Nothing is written to the mouse.

        The slot is into the running profile's table and the daemon holds profile 1's, so which
        profile runs is read first, on the probe's budget, as Solaar reads it; a mouse counted in
        HOST mode with host mode off may run none. A slot profile 1 does not have is dropped. A
        DPI the daemon already holds changes nothing, and neither does a stage under Gearbench's
        DPI shift: should the mouse report Gearbench's own DPI writes too, those are no new
        choice.
        """
        mouse = attached.mouse
        if attached.dpi_before_shift is not None:
            log.debug("%s moved to DPI stage %d under the DPI shift; not taken",
                      attached.name, slot + 1)
            return
        try:
            # mouse_event takes a stage report only at 0x8100's index, and with 0x8100 the read
            # is not None (Mouse.current_profile).
            running = cast(int, self.mice.mouse_call(lambda: mouse.current_profile(**MOUSE_PROBE),
                                                     target=attached))
        except MouseError as exc:
            log.info("%s moved to DPI stage %d by itself; not taken, since which profile it "
                     "runs could not be read: %s", attached.name, slot + 1, exc)
            return
        if running == 0:
            log.info("%s moved to DPI stage %d by itself; not taken, since in host mode it "
                     "runs no onboard profile and the daemon does not know that stage's DPI",
                     attached.name, slot + 1)
            return
        if running != PROFILE_SECTOR or mouse.profile is None or mouse.flash_unknown:
            log.info("%s moved to DPI stage %d by itself; it runs %s, whose stages the daemon "
                     "does not know, so it is not taken", attached.name, slot + 1,
                     profile_words(running))
            return
        dpi = mouse.profile.dpi_slot(slot)
        if dpi is None:
            log.info("%s moved to DPI stage %d by itself, which profile 1 does not have "
                     "(stages %s); ignored", attached.name, slot + 1,
                     ", ".join(map(str, mouse.profile.dpi_stages)))
            return
        if dpi == self.known_dpi(attached):
            log.debug("%s moved to DPI stage %d by itself, %d DPI, which it was known to be on",
                      attached.name, slot + 1, dpi)
            return
        log.info("%s moved to DPI stage %d by itself: %d DPI", attached.name, slot + 1, dpi)
        if attached.reading is not None:
            attached.reading["dpi"] = dpi
        self.remember_dpi(dpi, attached)
        self.d.session.post_dpi_notice(f"{attached.name}: {dpi} DPI")

    def after_dpi_write(self, found: AttachedMouse, dpi: int) -> None:
        """After a DPI write, `dpi` the value the mouse took: `mouse` answers with it until the
        mouse is next read, which a `mouse` with `ask` false never does.

        After a write that sent a stage's level byte to a mouse with lighting, in either mode,
        its lights flash and end dark (DPI_FLASH_REPAINT): what it shows is forgotten, and its
        lighting is written again once the flash is over (MouseLighting.repaint_after_flash,
        from tend_mice). A second write during the flash puts the repaint back."""
        if found.reading is not None:
            found.reading["dpi"] = dpi
        if found.lighting is None or not found.mouse.dpi_level_sent:
            return
        found.forget_showing()
        found.repaint_at = time.monotonic() + DPI_FLASH_REPAINT

    # -- a game profile's own stages and rate --------------------------------

    def tuning_for(self, attached: AttachedMouse) -> MouseTuning | None:
        """The loaded game profile's own stages and rate for this mouse's model, or None. Stages
        from a hand edit are clamped to the sensor's range and snapped to its step, as set_dpi
        takes a DPI (tune_stages snaps the same way, but refuses one outside the range): the
        mouse takes only a DPI on its step, and a choice is told from the stages by that DPI."""
        overlay = self.d.games.overlay
        tuning = overlay.tuning(dpi_keys(attached.product)) if overlay is not None else None
        span = attached.mouse.dpi_range
        if tuning is None or tuning.dpi_stages is None or span is None:
            return tuning
        stages = tuple(span.clamp(dpi) for dpi in tuning.dpi_stages)
        if len(set(stages)) != len(stages):
            return tuning      # two stages on one step: left as written
        # A tuning with stages names its default and shift among them (MouseTuning.from_json).
        return replace(tuning, dpi_stages=stages, default=span.clamp(cast(int, tuning.default)),
                       shift=span.clamp(cast(int, tuning.shift)))

    def in_force(self, attached: AttachedMouse) -> MouseTuning | None:
        """The game profile's own stages and rate the mouse is on while the daemon runs it in
        HOST mode (runs_host); otherwise profile 1 runs, or will once it is handed back."""
        return attached.tuning if self.mice.runs_host(attached) else None

    def stages_in_force(self, attached: AttachedMouse) -> tuple[list[int], int | None]:
        """The DPI stages and shift DPI in force: a game profile's own, else profile 1's."""
        tuning = self.in_force(attached)
        if tuning is not None and tuning.dpi_stages is not None:
            return list(tuning.dpi_stages), tuning.shift
        return attached.mouse.dpi_stages, attached.mouse.shift_dpi

    def own_stages_in_force(self, attached: AttachedMouse) -> list[int] | None:
        """The stages a DPI choice passes to Mouse.set_dpi and step_dpi: a game profile's own in
        force, or None for profile 1's, whose level byte counts slots. A game profile's list can
        equal profile 1's (tune_stages), so set_dpi cannot tell them apart by value."""
        own = own_stages(self.in_force(attached))
        return list(own) if own is not None else None

    @staticmethod
    def rate_for(attached: AttachedMouse, tuning: MouseTuning | None) -> int | None:
        """The report rate with `tuning` in force: its own, else profile 1's, or None."""
        if tuning is not None and tuning.report_rate is not None:
            return tuning.report_rate
        profile = attached.mouse.profile
        return profile.report_rate if profile is not None else None

    @staticmethod
    def rate_unsent(attached: AttachedMouse, rate: int | None) -> bool:
        """Whether `rate` has yet to go live on the mouse: against the rate last sent and
        answered, not its tuning's, since a retune cut short after its rate write never took
        the tuning. Never on a mouse without the report_rate capability, as send_rate."""
        return (rate is not None and rate != attached.live_rate
                and "report_rate" in attached.mouse.capabilities)

    def desktop_dpi(self, attached: AttachedMouse) -> int | None:
        """The DPI outside a game profile's own stages: the one before a load whose own stages
        were dropped under it (dpi_before_load), else the one read before they came into force
        (dpi_before_tuning), else the remembered one."""
        return (attached.dpi_before_load or attached.dpi_before_tuning
                or self.remembered_dpi(attached))

    def take_tuning(self, attached: AttachedMouse, tuning: MouseTuning | None) -> None:
        """Note that the mouse is on `tuning` now, under the load in force (Games.loads). A
        loaded profile with no stages of its own has no DPI chosen under them, and a tuning
        taken under another load ends the last one's dpi_before_load."""
        if own_stages(tuning) is None:
            attached.dpi_before_tuning = None
        key = self.game_key(attached)
        # The profile's stages, not `tuning`: a mouse that runs profile 1 is passed None while
        # the profile still has stages of its own.
        if key is not None and own_stages(self.tuning_for(attached)) is None:
            self.game_dpi.pop(key, None)
        if attached.tuning_load != self.d.games.loads:
            attached.dpi_before_load = None
        attached.tuning, attached.tuning_load = tuning, self.d.games.loads
        attached.owed_tuning = attached.tuning_failed = False
        attached.dpi_before_edit = None

    def game_key(self, attached: AttachedMouse) -> tuple[str, str] | None:
        """This mouse's key in game_dpi under the loaded game profile, or None."""
        overlay = self.d.games.overlay
        return (attached.mouse.backup_name, slug(overlay.name)) if overlay else None

    def chosen_under(self, attached: AttachedMouse, tuning: MouseTuning,
                     kept: bool = False) -> int | None:
        """The DPI last chosen for this mouse under the loaded game profile's own stages, or
        None. Unless `kept` (the same load's stages still in force), one not among `tuning`'s
        stages is forgotten."""
        # Each caller passes the loaded profile's tuning with its own stages: a profile is
        # loaded, so game_key has a key, and the stages are there.
        key = cast(tuple[str, str], self.game_key(attached))
        stages = cast(tuple[int, ...], tuning.dpi_stages)
        dpi = self.game_dpi.get(key)
        if dpi is None or kept or dpi in stages:
            return dpi
        del self.game_dpi[key]
        return None

    def profile_renamed(self, was: str, new: str) -> None:
        """Keep the DPIs chosen under a renamed game profile's stages under its new name."""
        for model, name in [key for key in self.game_dpi if key[1] == slug(was)]:
            self.game_dpi[(model, slug(new))] = self.game_dpi.pop((model, name))

    def profile_gone(self, name: str) -> None:
        """Forget the DPIs chosen under game profile `name`'s own stages, so a new profile under
        that name starts on its default stage: its file has gone, or a new profile takes a name
        whose file went outside Gearbench."""
        for key in [key for key in self.game_dpi if key[1] == slug(name)]:
            del self.game_dpi[key]

    def follow_tuning(self, target: AttachedMouse | None = None) -> int | None:
        """After the loaded game profile or its own stages or rate changed: tune every mouse.
        The DPI `target` moved to, or None."""
        moved = None
        for attached in list(self.mice.attached):
            dpi = self.tune(attached)
            if attached is target:
                moved = dpi
        return moved

    def tune(self, attached: AttachedMouse) -> int | None:
        """retune a mouse the daemon runs in HOST mode (runs_host) that answers; one not
        answering is owed it (tend_mice). Any other runs profile 1, or will once handed back, so
        nothing is sent. The DPI it moved to, or None."""
        if not self.mice.runs_host(attached):
            return None
        if attached.silent:
            attached.owed_tuning = True
            return None
        return self.retune(attached)

    def retune(self, attached: AttachedMouse) -> int | None:
        """Put a mouse in HOST mode on the stages and rate in force: the rate sent live unless
        it is `live_rate`, the DPI moved without the level byte. Own stages a load brings move
        it to the DPI last chosen under them, else their default (chosen_under), keeping the
        DPI before for when they go (desktop_dpi); stages changed under the same load keep it
        on its stage once it has taken that load's (`tuning_load`), else move it as a load
        does, and own stages dropped under it keep the DPI from before the load for its end
        (dpi_before_load). A mouse that stops answering part way is owed it, and the DPI
        read before a move it did not answer is kept, through a refused retry too, until a DPI
        is chosen (remember_dpi): the next try puts it back if no own stages are in force by
        then. A stage edit's move it did not answer keeps the DPI it was mapped from
        (`dpi_before_edit`), which the retry maps again, so the DPI is mapped once. The DPI it
        moved to, or None."""
        new, old = self.tuning_for(attached), attached.tuning
        loaded = attached.tuning_load != self.d.games.loads
        stages, before = own_stages(new), own_stages(old)
        undo = stages is None and before is None and (
            attached.dpi_before_tuning is not None
            or (loaded and attached.dpi_before_load is not None))
        mouse = attached.mouse
        rate = self.rate_for(attached, new)
        resend = self.rate_unsent(attached, rate)
        applied = edited = None
        read = False        # dpi_before_tuning read by this try
        said = "%s: the report rate in force could not be sent: %s"
        try:
            if resend and rate is not None:
                attached.live_rate = None
                self.mice.mouse_call(lambda: mouse.set_live_report_rate(rate), target=attached)
                attached.live_rate = rate
                log.info("%s at %s Hz, live", attached.name, rate)
                if attached.reading is not None:
                    attached.reading["report_rate"] = rate
            said = "%s: the DPI for the stages in force could not be sent: %s"
            wanted, why, read, edited = self.wanted_dpi(attached, new, before, loaded, undo)
            if wanted is not None:
                applied = self.mice.mouse_call(lambda: mouse.set_dpi(wanted, indicate=False),
                                               target=attached)
                # A held paddle's release would put back a DPI from before the move.
                attached.dpi_before_shift = None
        except MouseError as exc:
            log.info(said, attached.name, exc)
            attached.tuning_failed = True
            attached.owed_tuning = attached in self.mice.attached and attached.silent
            if read and not attached.owed_tuning:
                # Refused: no DPI write since this read landed, so the next try reads again. A
                # read kept from an unanswered try stays: that try's write may have landed.
                attached.dpi_before_tuning = None
            if edited is not None and attached.owed_tuning:
                attached.dpi_before_edit = edited
            return None
        desktop = self.desktop_dpi(attached)
        self.take_tuning(attached, new)
        if not loaded and stages is None and before is not None:
            # Own stages dropped under this load, the mouse kept on its stage: the load's end
            # puts back the DPI from before it.
            attached.dpi_before_load = desktop
        if applied is None:
            return None
        key = self.game_key(attached)
        if not loaded and stages is not None and before is not None and key in self.game_dpi:
            # The stage it was on, edited: its new value is the one chosen now.
            self.game_dpi[key] = applied
        log.info("%s at %s DPI: %s", attached.name, applied, why)
        self.after_dpi_write(attached, applied)
        return applied

    def wanted_dpi(self, attached: AttachedMouse, new: MouseTuning | None,
                   before: tuple[int, ...] | None, loaded: bool,
                   undo: bool) -> tuple[int | None, str, bool, int | None]:
        """Where retune moves a mouse's DPI with `new` in force, `before` the own stages it
        was on: the DPI, or None to leave it; why, for the journal; whether this try read
        dpi_before_tuning; and the DPI a stage edit was mapped from, or None. `undo`: a DPI
        is kept from a move to own stages that went unanswered, or from before a load whose
        own stages were dropped under it and which has ended, and none are in force now.
        The mouse is asked for its DPI at most once and nothing is kept before it answers,
        so a MouseError comes with nothing read."""
        stages, mouse = own_stages(new), attached.mouse
        wanted = edited = None
        why, read = "", False
        if loaded and new is not None and stages is not None:
            # Once a load: a retry after a DPI write that landed would read the game's.
            if before is None and attached.dpi_before_tuning is None:
                # The DPI from before the last load, if own stages were dropped under it; under
                # a held paddle the DPI read is its shift stage.
                attached.dpi_before_tuning = (
                    attached.dpi_before_load or attached.dpi_before_shift
                    or self.mice.mouse_call(mouse.get_dpi, target=attached))
                read = True
            # tuning_for answered `new` from the loaded profile, so one is loaded. Escaped:
            # the name is from a file, and `why` goes to the journal.
            chosen, name = (self.chosen_under(attached, new),
                            escaped(cast(GameProfile, self.d.games.overlay).name))
            wanted, why = ((chosen, f"the DPI last chosen under {name}") if chosen
                           else (new.default, f"{name}'s default stage"))
        elif (loaded and before is not None) or undo:
            wanted, why = (self.desktop_dpi(attached),
                           "where it was before a game profile's own stages")
        elif not loaded and stages != before:
            was = before if before is not None else mouse.dpi_stages
            now = stages if stages is not None else mouse.dpi_stages
            # A retry after a move it did not answer maps from the DPI before that move,
            # which may have landed.
            have = edited = (attached.dpi_before_edit or attached.dpi_before_shift
                             or self.mice.mouse_call(mouse.get_dpi, target=attached))
            if before is None and attached.dpi_before_tuning is None:
                # The DPI from before the load, if own stages were dropped under it, as a load's.
                attached.dpi_before_tuning = attached.dpi_before_load or have
                read = True
            moved = same_place(have, was, now)
            if moved is not None:
                wanted, why = moved, "the stage it was on"
        return wanted, why, read, edited

    def edits_tuning(self, target: AttachedMouse) -> GameProfile | None:
        """The loaded game profile when mouse_dpi_stages and mouse_rate edit its own stages and
        rate for `target` (wants_host); None when they write onboard profile 1."""
        return self.d.games.overlay if self.mice.wants_host(target) else None

    def tunes_game_profile(self, request: dict) -> bool:
        """Whether mouse_dpi_stages and mouse_rate for this request's mouse edit a loaded game
        profile's own (wants_host), not profile 1, which no load or unload changes (check_layer).
        True for a mouse not found: the handler says so."""
        try:
            return self.mice.wants_host(self.mice.mouse_for(request))
        except MouseError:
            return True

    def tuning_reply(self, attached: AttachedMouse) -> dict | None:
        """`mouse`'s `game_profile`: the loaded game profile's own stages and rate for this
        mouse, whether the stage and rate commands edit them, and whether they apply now. None
        with no profile loaded."""
        overlay = self.d.games.overlay
        if overlay is None:
            return None
        own = self.tuning_for(attached) or MouseTuning()
        return {"name": overlay.name, "edits": self.mice.wants_host(attached),
                "applies": self.mice.runs_host(attached),
                "dpi_stages": list(own.dpi_stages) if own.dpi_stages is not None else None,
                "default": own.default, "shift": own.shift, "report_rate": own.report_rate}

    def save_tuning(self, overlay: GameProfile, target: AttachedMouse,
                    tuning: MouseTuning) -> None:
        """Keep `tuning` as the game profile's own for `target` and save its file, or put it
        back and raise."""
        keys = dpi_keys(target.product)
        if (overlay.tuning(keys) or MouseTuning()) == tuning:
            return
        self.d.games.check_read_file()
        before = dict(overlay.mouse_tuning)
        overlay.set_tuning(keys, tuning)
        try:
            overlay.save()
        except BaseException:
            overlay.mouse_tuning = before
            raise

    def tuning_state(self, target: AttachedMouse) -> str:
        """Where a game profile's own stages and rate stand on `target` after tune: "later" out
        of HOST mode, "owed" while not answering or gone, "refused" when its retune ended at a
        failed request, a write or its read of the DPI (retune logged it; the next setup or load
        sends them), else "now". Not from `tuning`: a refused retune of stages and a rate the
        mouse was already counted on leaves it equal to the profile's."""
        if not target.host:
            return "later"
        if target.owed_tuning or target not in self.mice.attached:
            return "owed"
        return "refused" if target.tuning_failed else "now"

    @staticmethod
    def tuning_note(target: AttachedMouse, overlay: GameProfile, what: str,
                    dropped: bool, state: str) -> str:
        """What a stage or rate command says after it edited a game profile's own, `state` as
        tuning_state answers, or "dpi refused" from tune_rate: the rate is live, and the
        request the mouse refused was for the DPI. One sentence: the Mouse tab toasts a long
        one as a fixed line (style.short_note's `one_sentence`), since the name can hold
        ". "."""
        name = overlay.name
        again = (f"(see the journal), which is sent again at the mouse's next setup or "
                 f"{name}'s next load")
        refused = f"the mouse refused the change {again}"
        dpi = f", but the mouse refused the DPI for the stages in force {again}"
        if dropped:
            if state == "refused":
                return f"{name} has no {what} of its own for the {target.name}, but {refused}."
            once = " once it answers" if state == "owed" else ""
            return (f"{name} has no {what} of its own for the {target.name}: it uses "
                    f"onboard profile 1's{once}{dpi if state == 'dpi refused' else ''}.")
        when = {"later": "in force once the mouse is in host mode", "now": "in force now",
                "owed": "in force once the mouse answers",
                "refused": f"not in force: {refused}",
                "dpi refused": f"in force now{dpi}"}[state]
        return (f"Saved as {name}'s own {what} for the {target.name}, {when}; onboard "
                f"profile 1 is unchanged.")

    def tune_stages(self, target: AttachedMouse, overlay: GameProfile, request: dict) -> dict:
        """mouse_dpi_stages with a game profile loaded, for a mouse that wants host mode, taken
        or not: the profile's own stages for this model, placed as on profile 1 (place_stages)
        and saved to its file; `reset` drops them. Nothing is written to the mouse's flash."""
        mouse = target.mouse
        own = self.tuning_for(target) or MouseTuning()
        if request.get("reset"):
            tuning = replace(own, dpi_stages=None, default=None, shift=None)
        else:
            if own.dpi_stages is not None:
                old = list(own.dpi_stages)
                # A tuning with stages names its default and shift among them
                # (MouseTuning.from_json, and the replace below).
                old_default = old.index(cast(int, own.default))
                old_shift = old.index(cast(int, own.shift))
            else:
                old, profile = mouse.dpi_stages, mouse.profile
                old_default = profile.default_dpi_index if profile is not None else 0
                old_shift = profile.shift_dpi_index if profile is not None else 0
            stages, at_default, at_shift = place_stages(
                *stage_request(request), old, old_default, old_shift, mouse.dpi_range)
            tuning = replace(own, dpi_stages=tuple(stages), default=stages[at_default],
                             shift=stages[at_shift])
        self.save_tuning(overlay, target, tuning)
        moved = self.tune(target)
        # Tunes `target` a second time too, kept on purpose: a write it refused goes out once
        # more, and if that one is taken the note says "in force now" (after a reset, that
        # profile 1's is used) and the reply's `dpi` is the DPI it moved to. Not checked on a
        # mouse: whether one takes the second copy of a write it refused.
        again = self.follow_tuning(target)
        if moved is None:
            moved = again
        if tuning.dpi_stages is not None:
            stages, default, shift = list(tuning.dpi_stages), tuning.default, tuning.shift
        else:
            stages, profile, shift = mouse.dpi_stages, mouse.profile, mouse.shift_dpi
            default = (stage_dpi(stages, profile.default_dpi_index)
                       if profile is not None else None)
        reply = {"ok": True, "dpi_stages": stages, "default": default, "shift": shift,
                 "written": "game_profile", "overlay": overlay.name,
                 "note": self.tuning_note(target, overlay, "DPI stages",
                                          tuning.dpi_stages is None, self.tuning_state(target))}
        if moved is not None:
            reply["dpi"] = moved
        return reply

    def tune_rate(self, target: AttachedMouse, overlay: GameProfile, request: dict) -> dict:
        """mouse_rate with a game profile loaded, for a mouse that wants host mode, taken or
        not: the profile's own report rate for this model, saved to its file and sent live
        while in force; `reset` drops it."""
        own = self.tuning_for(target) or MouseTuning()
        if request.get("reset"):
            tuning = replace(own, report_rate=None)
        else:
            hz = whole_number(request["value"], "mouse_rate")
            if hz not in REPORT_RATES:
                raise MouseError(f"{hz} Hz is not supported; a game profile holds "
                                 f"{', '.join(map(str, REPORT_RATES))} Hz")
            tuning = replace(own, report_rate=hz)
        self.save_tuning(overlay, target, tuning)
        self.tune(target)
        # Tunes `target` a second time too, kept on purpose, as tune_stages: a rate write it
        # refused goes out once more.
        self.follow_tuning()
        rate, state = self.rate_for(target, tuning), self.tuning_state(target)
        if state == "refused" and not self.rate_unsent(target, rate):
            # retune sends the rate first: with it live, the retune ended at the DPI.
            state = "dpi refused"
        return {"ok": True, "report_rate": rate, "written": "game_profile",
                "overlay": overlay.name,
                "note": self.tuning_note(target, overlay, "report rate",
                                         tuning.report_rate is None, state)}

    def no_tuning(self, target: AttachedMouse) -> str:
        """Why `reset` has nothing to give back."""
        if self.d.games.overlay is None:
            return ("no game profile is loaded, so there are no stages or rate of its own "
                    "to drop")
        return (f"the {target.name} is not set to run in host mode: a game profile's own stages "
                f"and rate are for a mouse in host mode")

    def _cmd_mouse_dpi(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        value = whole_number(request["value"], "mouse_dpi")
        stages = self.own_stages_in_force(target)
        applied = self.mice.mouse_call(lambda: target.mouse.set_dpi(value, stages=stages),
                                       target=target)
        self.after_dpi_write(target, applied)
        target.dpi_before_shift = None          # as dpi_step's
        self.remember_dpi(applied, target)
        return {"ok": True, "dpi": applied, "requested": value}
