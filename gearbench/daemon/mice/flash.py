"""The writes to a mouse's onboard profile 1, kept in its flash: the report rate, the DPI
stages, G4 to G9 enabled or set to the fallback, and the restore of the newest backup. Every
one goes through `flash_write` to a method of `Mouse`, which backs the profile's sectors up
before it writes (`Mouse.write_profile`), except a rate that `Mouse.set_report_rate` sends
live with no backup, to a mouse without 0x8100 or one that reads HOST mode while the daemon
does not count it there; the rate of a mouse that sets_rate_live, which `Dpi.live_rate` sends
live alone; and the stages or rate of a loaded game profile's own (`Dpi.edits_tuning`), which
go to `Dpi` and write no flash. In HOST mode, where no profile runs, a write still puts the
mouse on profile 1's default stage and stored rate, so the DPI is read before it and put back
after (`put_back_after_write`)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...mouse import (
    enable_note,
    fallback_note,
    runs_another_profile,
    written_note,
)
from ...mouse_device import Mouse
from ...onboard import stage_dpi
from ...sensor import MouseError
from ...wording import printable
from ..shared import MOUSE_PROBE, whole_number
from .attached import (
    TUNING_HOST_ONLY,
    AttachedMouse,
    aimed,
    follow_shift,
    same_place,
    stage_request,
)

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

#: The error for a mouse that went away after its profile 1 write was made, by its name.
GONE_AFTER_WRITE = "the {} went away after the profile 1 write"

#: The sentences a HOST-mode write's note adds for a report rate not sent or not taken live.
#: The commit has put the mouse on profile 1's stored rate either way: only the live send failed.
RATE_NOT_ANSWERED = " The mouse is not answering, so the report rate is sent once it answers."
RATE_NOT_TAKEN = (" The live send of the report rate failed (see the journal); it is sent "
                  "again at the mouse's next setup.")

#: What a failed profile 1 write adds when its sector could not be read back (`flash_unknown`).
SECTOR_UNKNOWN = ("profile 1 may have changed and could not be read back; leave the mouse on: "
                  "`gearbench-ctl mouse` reads it again once the mouse answers (see RECOVERY "
                  "in gearbench-ctl(1))")


@dataclass(frozen=True)
class WriteOutcome:
    """What a profile 1 write (flash_write) leaves for the rest of its command."""

    #: A DPI from before a load (dpi_before_load, dpi_before_tuning) was kept when the write
    #: ended, noted before follow_kept, which drops one whose place is past the new list's
    #: end: the DPI follow_dpi reads is then a game profile's either way.
    dpi_kept: bool = False
    #: In HOST mode, the DPI could not be read before the write or put back after it
    #: (put_back_after_write), so what the mouse reads may be profile 1's default stage, no
    #: choice: follow_dpi takes nothing from the mouse.
    dpi_not_put_back: bool = False
    #: In HOST mode, the put-back sent the rate in force and the mouse answered with a
    #: refusal, for the write's note (rate_unsent).
    rate_refused: bool = False


class ProfileWrites:
    """The commands that write a mouse's onboard profile 1, and what follows a write."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "mouse_rate": self._cmd_mouse_rate,
            "mouse_dpi_stages": self._cmd_mouse_dpi_stages,
            "mouse_enable_buttons": self._cmd_mouse_enable_buttons,
            "mouse_fallback_buttons": self._cmd_mouse_fallback_buttons,
            "mouse_restore": self._cmd_mouse_restore,
        }

    def _cmd_mouse_rate(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        return aimed(target, self.set_rate(target, request))

    def set_rate(self, target: AttachedMouse, request: dict) -> dict:
        """mouse_rate's reply for `target`: a loaded game profile's own rate, a rate set live
        alone, or the rate in profile 1, also sent live in HOST mode."""
        overlay = self.mice.dpi.edits_tuning(target)
        if overlay is not None:
            return self.mice.dpi.tune_rate(target, overlay, request)
        if request.get("reset") is True:
            return {"ok": False, "error": self.mice.dpi.no_tuning(target)}
        hz = whole_number(request["value"], "mouse_rate")
        if target.sets_rate_live:
            return self.mice.dpi.live_rate(target, hz)
        live = ""
        if target.host:
            # Stored in profile 1 for the next arm (send_rate), and sent live now: in HOST
            # mode no profile runs. Not to a mouse the put-back found not answering
            # (put_back_after_write), as mouse_restore's.
            self.flash_write(target, target.mouse.store_report_rate, hz, rate=False)
            live = self.rate_after_write(target)
            if target not in self.mice.attached:
                # Torn down at the send: profile 1 holds the rate, as mouse_restore says.
                raise MouseError(GONE_AFTER_WRITE.format(target.name))
            where = "onboard"
        else:
            where, _ = self.flash_write(target, target.mouse.set_report_rate, hz)
        # What `mouse` answers from until the next reading: in effect from the write's commit,
        # which in HOST mode puts the mouse on the stored rate too (flash_write), unless the
        # mouse runs another profile, whose rate stays in force (written_note).
        if where == "live" or self.in_effect(target):
            target.on_rate(hz)
        reply = {"ok": True, "report_rate": hz, "written": where}
        if where == "onboard":
            reply["note"] = self.flash_note(target) + live
        return reply

    def rate_after_write(self, target: AttachedMouse) -> str:
        """After a HOST-mode profile 1 write, the rate in force sent live (send_rate) to a mouse
        still answering, and what is not taken owed by its cause, for every write command
        alike. Not answering, or silent at the send: owed to its recheck, whose full setup
        sends it (setup_host), or, heard from before that recheck, which then never comes, to
        tend_mice's retune (owed_tuning). Refused, or failed in the kernel: owed a full setup
        once it answers (cut_short), since no recheck comes to a mouse that never went silent.
        `cookie_set` is cleared for either. Returns the note's sentence, RATE_NOT_ANSWERED,
        RATE_NOT_TAKEN or nothing."""
        if self.mice.mouse_answering(target) and self.mice.dpi.send_rate(target):
            return ""
        target.cookie_set = False
        if not self.mice.mouse_answering(target):
            target.live_rate = None     # the commit may have changed it
            target.owed_tuning = True
            return RATE_NOT_ANSWERED
        target.cut_short = True
        return RATE_NOT_TAKEN

    def rate_unsent(self, target: AttachedMouse, outcome: WriteOutcome) -> str:
        """After a write whose rate put_back_after_write was to send, RATE_NOT_ANSWERED where
        the mouse is not answering, RATE_NOT_TAKEN where it refused the rate
        (`outcome.rate_refused`), else nothing, as rate_after_write owed it. The write's answer
        marked it answering, so only the put-back can have marked it: it was sent no rate or
        did not answer it. Taken right after the write: a request after it that goes
        unanswered marks the mouse too, the rate sent."""
        if not self.mice.mouse_answering(target):
            return RATE_NOT_ANSWERED
        return RATE_NOT_TAKEN if outcome.rate_refused else ""

    @staticmethod
    def in_effect(target: AttachedMouse) -> bool:
        """Whether a profile 1 write is in effect at its commit: in HOST mode, or on a mouse
        not last read running another profile (written_note)."""
        return target.host or not runs_another_profile(target.mouse.running_profile)

    def flash_note(self, target: AttachedMouse) -> str:
        """written_note, plus where a loaded game profile's own stages and rate stand."""
        note = written_note(target.name, target.host, target.mouse.running_profile)
        overlay = self.d.games.overlay
        if overlay is None:
            return note
        # Printable: a name keeps a bidi override, which would reverse the window's note.
        name = printable(overlay.name)
        if not self.mice.wants_host(target):
            return f"{note} {name} is loaded, and {TUNING_HOST_ONLY}."
        if self.mice.dpi.tuning_for(target) is None:
            return note
        if target.host:
            # The game profile's own stages or rate stay in force over profile 1's (send_rate,
            # stages_in_force).
            return written_note(target.name, True, own=name)
        # A mouse whose arm did not take runs profile 1 (in_force), as tuning_note says.
        return (f"{note} {name}'s own stages or rate are in effect once the mouse is in "
                f"host mode.")

    def flash_write[T](self, target: AttachedMouse, what: Callable[..., T],
                       *args: object, rate: bool = True) -> tuple[T, WriteOutcome]:
        """One onboard-profile write to `target`, through mouse_call, or the live rate
        `Mouse.set_report_rate` sends in its place (the module docstring): what `what`
        returned, and what the write leaves for the rest of the command (WriteOutcome).

        In ONBOARD mode a success is in effect when this returns, unless the mouse runs another
        profile (written_note). In HOST mode no profile runs, yet the write puts the mouse on
        profile 1's default stage and stored report rate (measured on the G502 X PLUS,
        2026-10-02), so the DPI is read first and put back after, with the rate in force unless
        the caller sends its own (`rate` False): put_back_after_write. A mouse gone or not
        answering at that read is not written to. Before the put-back, in either mode, the DPIs
        kept ahead of the remembered one follow a stage list the write changed
        (Dpi.follow_kept), a game profile's own stages in force or not: they are profile 1
        DPIs. Whether one was kept is noted first, for follow_dpi (`WriteOutcome.dpi_kept`).
        A model whose writes are not tested (Mouse.flash_refusal) is read no DPI first: its
        write is refused with nothing written, where an unanswered read would answer that the
        mouse is asleep. A failed write can still have changed the sector: Mouse reads it again and
        counts a sector that reads back as written as a success, and when that read fails too
        the sector is unknown (`flash_unknown`) until `mouse` reads it, as the error says.
        """
        dpi = None
        if target.host and target.mouse.flash_refusal is None:
            # On the write's own budget, not the probe's: a mouse asleep wakes at a request
            # and answers 0.42 to 1.63 s after it (hand_back).
            try:
                dpi = self.mice.mouse_call(target.mouse.get_dpi, target=target)
            except MouseError as exc:
                if not self.mice.mouse_answering(target):
                    raise           # gone, or not answering: the write would not be taken
                log.info("%s: no DPI read before the profile 1 write: %s", target.name, exc)
        started, written, gone = target.mouse.sector_writes, False, None
        not_put_back = refused = False
        before = target.mouse.dpi_stages
        try:
            result = self.mice.mouse_call(what, *args, target=target)
            written = True
        except MouseError as exc:
            if target.mouse.flash_unknown:
                # mouse_refusal words `exc` first, its raw text kept as the detail.
                raise MouseError(SECTOR_UNKNOWN) from exc
            raise
        finally:
            kept = target.dpi_before_load is not None or target.dpi_before_tuning is not None
            if target.mouse.sector_writes != started:
                # Before the put-back, whose requests can tear the mouse down: its next attach
                # takes them as they are then (KeptMouse).
                self.mice.dpi.follow_kept(target, before, target.mouse.dpi_stages)
                if target.host:
                    # A caller that sends its own rate does not after a write that raised.
                    gone, not_put_back, refused = self.put_back_after_write(
                        target, dpi, rate or not written)
        if gone is not None:
            raise gone      # the write was made; nothing more reaches the mouse
        return result, WriteOutcome(kept, not_put_back, refused)

    def put_back_after_write(self, target: AttachedMouse, dpi: int | None,
                             rate: bool) -> tuple[MouseError | None, bool, bool]:
        """After a HOST-mode profile 1 write, taken or not (flash_write): put back `dpi`, the
        DPI read before it, where the mouse reads another, and with `rate` send the rate in
        force and owe what is not taken (rate_after_write). Nothing goes to a mouse not
        answering. A DPI not put back is owed a full setup (setup_host): `cookie_set` cleared
        for its recheck, and `cut_short` for a mouse that gets none, heard from before it or
        never silent (tend_mice). On the probe's budget: the mouse has just answered the
        write. Raises nothing, the write's outcome being the command's. Returns a MouseError
        when the mouse went away, else None; whether the DPI was not read before or not put
        back, so follow_dpi takes nothing from the mouse; and whether a rate sent was refused,
        for the write's note (rate_unsent)."""
        done = False
        if dpi is not None and self.mice.mouse_answering(target):
            try:
                now = self.mice.mouse_call(lambda: target.mouse.get_dpi(**MOUSE_PROBE),
                                           target=target)
                if now != dpi:
                    self.mice.mouse_call(lambda: target.mouse.set_dpi(dpi, indicate=False,
                                                                      **MOUSE_PROBE),
                                         target=target)
                    log.info("%s back on %s DPI after the profile 1 write, which moved it to %s",
                             target.name, dpi, now)
                done = True
            except MouseError as exc:
                log.info("%s: could not put the DPI back after the profile 1 write: %s",
                         target.name, exc)
        not_put_back = not done
        refused = rate and self.rate_after_write(target) == RATE_NOT_TAKEN
        if target not in self.mice.attached:
            # Gone at the put-back's read or rate send alike: the write was made.
            return MouseError(GONE_AFTER_WRITE.format(target.name)), not_put_back, refused
        if not done:
            target.cookie_set, target.cut_short = False, True
        return None, not_put_back, refused

    def follow_dpi(self, target: AttachedMouse, before: list[int], after: list[int],
                   outcome: WriteOutcome) -> int | None:
        """After a write that changed the stage list, read the DPI the mouse is on and store it
        in place of a remembered DPI (remember_dpi), so restore_dpi does not put back an edited
        stage's old value. The DPIs desktop_dpi puts back first followed the edit at the write
        (flash_write). In HOST mode, where flash_write put back the DPI from before the
        write, a mouse on an old stage stays on its DPI while the new list holds it, else goes
        to the stage at the same place (same_place), as retune does; in ONBOARD mode the write
        is in effect at its commit. Where a DPI kept from before a load (dpi_before_load,
        dpi_before_tuning) stood at the write, dropped by it or not (`outcome.dpi_kept`), the
        DPI read is a game profile's, no choice outside it: the remembered DPI follows the edit
        instead, with nothing read (Dpi.follow_remembered), as the kept ones did. Under a game
        profile's own stages it follows the same way, and the mouse is neither read nor moved:
        profile 1's list is not the one in force.

        Under a held DPI shift the DPI read is the shift's, which is no choice: the DPI under
        the shift follows the edit (same_place), is remembered instead, and is put back at the
        release.

        With none remembered nothing is stored: a stored value would go stale at a stage change
        the daemon does not hear of. A DPI that cannot be read or moved is forgotten on both
        routes, since it may be the edited stage's old value, and so is the DPI of a mouse
        marked not answering, which is not read (the owner's decision of 2026-10-11). So is the
        one kept when the mouse answers a DPI outside DPI_LOWEST to DPI_HIGHEST, as 0
        (remember_dpi). The DPI the mouse is on, or None.
        """
        if after == before:
            return None
        # A DPI kept from before a load (desktop_dpi), or a game profile's own stages, puts the
        # mouse on a game profile's DPI, not the one outside it: the remembered DPI follows the
        # edit with nothing read, as the kept ones did (follow_kept), which remember_dpi would
        # clear. Taken from flash_write's outcome (dpi_kept): follow_kept drops a kept DPI
        # whose place is past the new list's end, and the mouse is still on the load's DPI.
        own = self.mice.dpi.own_stages_in_force(target) is not None
        aside = own or outcome.dpi_kept
        if aside:
            self.mice.dpi.follow_remembered(target, before, after)
        if own:
            # Under a game profile's own stages, profile 1's list is not the one in force.
            return None
        held = follow_shift(target, before, after)
        try:
            if outcome.dpi_not_put_back:
                raise MouseError("the DPI the write moved could not be put back")
            if not self.mice.mouse_answering(target):
                # Marked so since the write: a read would wait out its tries on the loop.
                raise MouseError("the mouse is not answering")
            dpi = self.mice.mouse_call(target.mouse.get_dpi, target=target)
            wanted = same_place(dpi, before, after) if target.host else None
            if wanted is not None and wanted != dpi:
                dpi = self.mice.mouse_call(lambda: target.mouse.set_dpi(wanted, indicate=False),
                                           target=target)
                if held is None:
                    log.info("%s at %s DPI: the stage it was on", target.name, dpi)
        except MouseError as exc:
            log.info("%s: could not read or move the DPI after the stage list changed: %s",
                     target.name, exc)
            self.mice.dpi.forget_dpi(target)
            return None
        if not aside and self.mice.dpi.remembered_dpi(target) is not None:
            self.mice.dpi.remember_dpi(dpi if held is None else held, target)
        target.on_dpi(dpi)
        return dpi

    def gone_after_write(self, target: AttachedMouse, before: list[int]) -> None:
        """A mouse gone part way through a write that changed profile 1's stage list, or left
        it unknown: follow_dpi cannot read it now, so a remembered DPI is forgotten, as at a
        read that fails. `before` is the list from before the write."""
        if (target not in self.mice.attached and self.mice.dpi.own_stages_in_force(target) is None
                and (target.mouse.flash_unknown or target.mouse.dpi_stages != before)):
            self.mice.dpi.forget_dpi(target)

    def _cmd_mouse_dpi_stages(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        return aimed(target, self.set_stages(target, request))

    def set_stages(self, target: AttachedMouse, request: dict) -> dict:
        """mouse_dpi_stages' reply for `target`: the stages listed, or with `factory` those of
        its factory profile, as a loaded game profile's own or in profile 1."""
        overlay = self.mice.dpi.edits_tuning(target)
        # Stages, a default or a shift beside factory or reset would be dropped without a word.
        listed = any(request.get(key) is not None for key in ("value", "default", "shift"))
        if request.get("factory") is True:
            if request.get("reset") is True or listed:
                return {"ok": False, "error": "factory takes no stages, default, shift or reset"}
            if overlay is None and (refused := target.mouse.write_refusal("stages")) is not None:
                # Before the ROM read, as flash_write refuses before its DPI read: one asleep
                # would get "did not answer" in place of the refusal.
                raise MouseError(refused)
            factory = self.mice.mouse_call(target.mouse.factory_profile, target=target)
            stages = factory.dpi_stages
            if not stages:
                return {"ok": False,
                        "error": f"the {target.name}'s factory profile holds no DPI stages"}
            request = {**request, "value": stages,
                       "default": stage_dpi(stages, factory.default_dpi_index),
                       "shift": stage_dpi(stages, factory.shift_dpi_index)}
        elif request.get("reset") is True and listed:
            return {"ok": False, "error": "reset takes no stages, default or shift"}
        if overlay is not None:
            return self.mice.dpi.tune_stages(target, overlay, request)
        if request.get("reset") is True:
            return {"ok": False, "error": self.mice.dpi.no_tuning(target)}
        stages, default, shift = stage_request(request)
        before = target.mouse.dpi_stages
        try:
            image, outcome = self.flash_write(target, target.mouse.set_dpi_stages, stages,
                                              default, shift)
        except MouseError:
            self.gone_after_write(target, before)
            raise
        reply = {"ok": True, "dpi_stages": image.dpi_stages,
                 "default": image.dpi_stages[image.default_dpi_index],
                 "shift": image.dpi_stages[image.shift_dpi_index],
                 "note": self.flash_note(target) + self.rate_unsent(target, outcome)}
        dpi = self.follow_dpi(target, before, image.dpi_stages, outcome)
        if dpi is not None:
            reply["dpi"] = dpi
        return reply

    def spy_drives(self, target: AttachedMouse) -> bool:
        """Whether the spy brings `target`'s G4 to G9 whatever profile 1 holds, for the notes
        of the writes to them. One stuck in HOST mode runs no profile and its spy is stopped: a
        write to them waits for profile 1 to run, as outside HOST mode. Not stuck_in_host_mode,
        which a put-back that went unanswered (put_back_after_write) turns false: that leaves
        the mouse stuck all the same."""
        return self.mice.runs_host(target) and not target.host_error

    @staticmethod
    def no_button_table(mouse: Mouse) -> dict:
        """The refusal of a write to the buttons in `mouse`'s onboard profile, whose model has
        no table this build knows (MouseModel.button_table)."""
        return {"ok": False,
                "error": f"the {mouse.name} has no button table this build knows, so nothing "
                         f"was written"}

    def _cmd_mouse_enable_buttons(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        mouse = target.mouse
        table = mouse.button_table
        if table is None:
            return self.no_button_table(mouse)
        if target.enable_refused is not None:
            # Before flash_write, which in host mode reads the DPI first: nothing is sent.
            return {"ok": False, "error": target.enable_refused}
        image, outcome = self.flash_write(target, mouse.enable_buttons)
        return aimed(target, {"ok": True, "buttons_enabled": table.all_enabled(image),
                              "enabled_buttons": table.enabled(image),
                              "buttons": mouse.button_words(image),
                              "note": (enable_note(target.name, self.spy_drives(target),
                                                   mouse.running_profile)
                                       + self.rate_unsent(target, outcome))})

    def _cmd_mouse_fallback_buttons(self, request: dict) -> dict:
        """G4 to G9 in profile 1 as the mouse is to run them without Gearbench, through the
        same backed-up and verified write as enabling them."""
        target = self.mice.mouse_for(request)
        mouse = target.mouse
        table = mouse.button_table
        if table is None:
            return self.no_button_table(mouse)
        image, outcome = self.flash_write(target, mouse.write_fallback_buttons)
        return aimed(target, {"ok": True, "buttons_fallback": table.holds_fallback(image),
                              "buttons": mouse.button_words(image),
                              "note": (fallback_note(target.name, self.spy_drives(target),
                                                     mouse.running_profile)
                                       + self.rate_unsent(target, outcome))})

    def _cmd_mouse_restore(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        before = target.mouse.dpi_stages
        try:
            (image, path), outcome = self.flash_write(target, target.mouse.restore_backup,
                                                      rate=False)
        except MouseError:
            self.gone_after_write(target, before)
            raise
        note = self.flash_note(target)
        if image.report_rate is not None and self.in_effect(target):
            # What `mouse` answers from until the next reading, as mouse_rate's; a rate
            # send_rate gets taken below replaces it.
            target.on_rate(image.report_rate)
        if target.host:
            # No profile runs in HOST mode: the rate in force goes live (send_rate), as at an
            # arm. Not to a mouse the put-back found not answering (put_back_after_write).
            note += self.rate_after_write(target)
            if target not in self.mice.attached:
                # Torn down at the send: follow_dpi would ask the closed node.
                self.gone_after_write(target, before)
                raise MouseError(GONE_AFTER_WRITE.format(target.name))
        table = target.mouse.button_table
        reply = {"ok": True, "restored_from": path,
                 "buttons_enabled": table is not None and table.all_enabled(image),
                 "note": note}
        dpi = self.follow_dpi(target, before, image.dpi_stages, outcome)
        if dpi is not None:
            reply["dpi"] = dpi
        return aimed(target, reply)
