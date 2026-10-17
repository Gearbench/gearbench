"""The writes to a mouse's onboard profile 1, kept in its flash: the report rate, the DPI
stages, G4 to G9 enabled or set to the fallback, and the restore of the newest backup. Every
one goes through `flash_write` to a method of `Mouse`, which backs the profile's sectors up
before it writes (`Mouse.write_profile`), except a rate that `Mouse.set_report_rate` sends
live with no backup, to a mouse without 0x8100 or one that reads HOST mode while the daemon
does not count it there. In HOST mode, where no profile runs, a write still puts the mouse on
profile 1's default stage and stored rate, so the DPI is read before it and put back after
(`put_back_after_write`)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...mouse import (
    Mouse,
    MouseError,
    buttons_enabled,
    buttons_fallback,
    enable_note,
    enabled_buttons,
    fallback_note,
    written_note,
)
from ...onboard import stage_dpi
from ..shared import MOUSE_PROBE, whole_number
from .attached import AttachedMouse, own_stages, same_place, stage_request

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

#: The error for a mouse that went away after its profile 1 write was made.
GONE_AFTER_WRITE = "The mouse went away after the profile 1 write."

#: The sentences a HOST-mode write's note adds for a report rate not sent or not taken live.
RATE_NOT_ANSWERED = " The mouse is not answering, so the report rate is sent once it answers."
RATE_NOT_TAKEN = (" The mouse did not take the report rate (see the journal), which is sent "
                  "again at its next setup.")

#: What a failed profile 1 write adds when its sector could not be read back (`flash_unknown`).
SECTOR_UNKNOWN = ("Profile 1 may have changed and could not be read back. Leave the mouse on: "
                  "`gearbench-ctl mouse` reads it again once the mouse answers (see RECOVERY "
                  "in gearbench-ctl(1)).")


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
        overlay = self.mice.dpi.edits_tuning(target)
        if overlay is not None:
            return self.mice.dpi.tune_rate(target, overlay, request)
        if request.get("reset"):
            return {"ok": False, "error": self.mice.dpi.no_tuning(target)}
        hz = whole_number(request["value"], "mouse_rate")
        live = ""
        if target.host:
            # Stored in profile 1 for the next arm (send_rate), and sent live now: in HOST
            # mode no profile runs. Not to a mouse the put-back found not answering
            # (put_back_after_write), as mouse_restore's.
            self.flash_write(target, target.mouse.store_report_rate, hz, rate=False)
            sent = self.mice.mouse_answering(target) and self.mice.dpi.send_rate(target)
            if target not in self.mice.attached:
                # Torn down at the send: profile 1 holds the rate, as mouse_restore says.
                raise MouseError(GONE_AFTER_WRITE)
            if not sent:
                live = self.rate_owed(target)
            where = "onboard"
        else:
            where = self.flash_write(target, target.mouse.set_report_rate, hz)
        # What `mouse` answers from until the next reading: in effect from the write's commit,
        # which in HOST mode puts the mouse on the stored rate too (flash_write).
        if target.reading is not None:
            target.reading["report_rate"] = hz
        reply = {"ok": True, "report_rate": hz, "written": where}
        if where == "onboard":
            reply["note"] = self.flash_note(target) + live
        return reply

    def rate_owed(self, target: AttachedMouse) -> str:
        """After a HOST-mode profile 1 write whose rate the mouse did not take live, or was not
        sent since it was not answering: owed to its recheck, whose full setup sends it
        (setup_host), or, heard from before that recheck, which then never comes, to
        tend_mice's retune (owed_tuning). Any other failure, a refusal or the kernel's, waits
        for its next setup. The sentence for the reply's note."""
        target.cookie_set = False
        if not self.mice.mouse_answering(target):
            target.live_rate = None     # the commit may have changed it
            target.owed_tuning = True
            return RATE_NOT_ANSWERED
        return RATE_NOT_TAKEN

    def rate_unsent(self, target: AttachedMouse) -> str:
        """After a write whose rate put_back_after_write was to send, RATE_NOT_ANSWERED where
        the mouse is not answering, else nothing. The write's answer marked it answering, so
        only the put-back can have marked it: it was sent no rate or did not answer it, and its
        full setup sends it (cut_short). Taken right after the write: a request after it that
        goes unanswered marks the mouse too, the rate sent."""
        return "" if self.mice.mouse_answering(target) else RATE_NOT_ANSWERED

    def flash_note(self, target: AttachedMouse) -> str:
        """written_note, plus where a loaded game profile's own stages and rate stand."""
        note = written_note(target.name, target.host, target.mouse.running_profile)
        overlay = self.d.games.overlay
        if overlay is None:
            return note
        if not self.mice.wants_host(target):
            return (f"{note} {overlay.name} is loaded, and a game profile's own stages and "
                    f"rate are for a mouse in host mode.")
        if self.mice.dpi.tuning_for(target) is None:
            return note
        if target.host:
            # written_note's, but the game profile's own stages or rate stay in force over
            # profile 1's (send_rate, stages_in_force).
            return (f"Written to onboard profile 1 on the {target.name}. In host mode, "
                    f"Gearbench uses its stages and rate at once, except {overlay.name}'s own "
                    f"while it is loaded; the mouse runs profile 1 when Gearbench stops.")
        # A mouse whose arm did not take runs profile 1 (in_force), as tuning_note says.
        return (f"{note} {overlay.name}'s own stages or rate are in force once the mouse is in "
                f"host mode.")

    def flash_write[T](self, target: AttachedMouse, what: Callable[..., T],
                       *args: object, rate: bool = True) -> T:
        """One onboard-profile write to `target`, through mouse_call, or the live rate
        `Mouse.set_report_rate` sends in its place (the module docstring).

        In ONBOARD mode a success is in effect when this returns, unless the mouse runs another
        profile (written_note). In HOST mode no profile runs, yet the write puts the mouse on
        profile 1's default stage and stored report rate (measured on the G502 X PLUS,
        2026-10-02), so the DPI is read first and put back after, with the rate in force unless
        the caller sends its own (`rate` False): put_back_after_write. A mouse gone or not
        answering at that read is not written to. A failed write can still have changed the
        sector: Mouse reads it again and counts a sector that reads back as written as a
        success, and when that read fails too the sector is unknown (`flash_unknown`) until
        `mouse` reads it, as the error says.
        """
        dpi = None
        target.dpi_not_put_back = False
        if target.host:
            # On the write's own budget, not the probe's: a mouse asleep wakes at a request
            # and answers 0.42 to 1.63 s after it (hand_back).
            try:
                dpi = self.mice.mouse_call(target.mouse.get_dpi, target=target)
            except MouseError as exc:
                if not self.mice.mouse_answering(target):
                    raise           # gone, or not answering: the write would not be taken
                log.info("%s: no DPI read before the profile 1 write: %s", target.name, exc)
        started, written, gone = target.mouse.sector_writes, False, None
        try:
            result = self.mice.mouse_call(what, *args, target=target)
            written = True
        except MouseError as exc:
            if target.mouse.flash_unknown:
                # mouse_refusal words `exc` first, its raw text kept as the detail.
                raise MouseError(SECTOR_UNKNOWN) from exc
            raise
        finally:
            if target.host and target.mouse.sector_writes != started:
                # A caller that sends its own rate does not after a write that raised.
                gone = self.put_back_after_write(target, dpi, rate or not written)
        if gone is not None:
            raise gone      # the write was made; nothing more reaches the mouse
        return result

    def put_back_after_write(self, target: AttachedMouse, dpi: int | None,
                             rate: bool) -> MouseError | None:
        """After a HOST-mode profile 1 write, taken or not (flash_write): put back `dpi`, the
        DPI read before it, where the mouse reads another, and with `rate` send the rate in
        force (send_rate). Nothing goes to a mouse not answering. What is not done is owed a
        full setup (setup_host): `cookie_set` cleared for its recheck, and `cut_short` for a
        mouse that gets none, heard from before it or never silent (tend_mice). A DPI not read
        before or not put back sets `dpi_not_put_back`, so follow_dpi takes nothing from the
        mouse. On the probe's budget: the mouse has just answered the write. Raises nothing, the
        write's outcome being the command's; returns a MouseError when the mouse went away, else
        None."""
        done = dpi is not None and self.mice.mouse_answering(target)
        if done and dpi is not None:
            try:
                now = self.mice.mouse_call(lambda: target.mouse.get_dpi(**MOUSE_PROBE),
                                           target=target)
                if now != dpi:
                    self.mice.mouse_call(lambda: target.mouse.set_dpi(dpi, indicate=False,
                                                                      **MOUSE_PROBE),
                                         target=target)
                    log.info("%s back on %s DPI after the profile 1 write, which moved it to %s",
                             target.name, dpi, now)
            except MouseError as exc:
                log.info("%s: could not put the DPI back after the profile 1 write: %s",
                         target.name, exc)
                done = False
        target.dpi_not_put_back = not done
        if rate:
            if self.mice.mouse_answering(target):
                done = self.mice.dpi.send_rate(target) and done
            else:
                target.live_rate = None     # the commit may have changed it
                done = False
        if target not in self.mice.attached:
            # Gone at the put-back's read or rate send alike: the write was made.
            return MouseError(GONE_AFTER_WRITE)
        if not done:
            target.cookie_set, target.cut_short = False, True
        return None

    def follow_dpi(self, target: AttachedMouse, before: list[int],
                   after: list[int]) -> int | None:
        """After a write that changed the stage list, read the DPI the mouse is on and store it
        in place of a remembered DPI (remember_dpi), so restore_dpi does not put back an edited
        stage's old value. In HOST mode, where flash_write put back the DPI from before the
        write, a mouse on an old stage is moved to the same place in the new list, as retune
        does; in ONBOARD mode the write is in effect at its commit.

        Under a held DPI shift the DPI read is the shift's, which is no choice: the DPI under
        the shift moves to its place in the new list, is remembered instead, and is put back at
        the release.

        With none remembered nothing is stored: a stored value would go stale at a stage change
        the daemon does not hear of. A DPI that cannot be read or moved is forgotten on both
        routes, since it may be the edited stage's old value, and so is the DPI of a mouse
        marked not answering, which is not read (the owner's decision of 2026-10-11). The DPI
        the mouse is on, or None.
        """
        if after == before or own_stages(self.mice.dpi.in_force(target)) is not None:
            # Under a game profile's own stages, profile 1's list is not the one in force.
            return None
        held = target.dpi_before_shift
        moved = same_place(held, before, after)
        if moved is not None:
            # The DPI under the shift keeps its place in the list, for the release to put back.
            held = target.dpi_before_shift = moved
        try:
            if target.dpi_not_put_back:
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
        if self.mice.dpi.remembered_dpi(target) is not None:
            self.mice.dpi.remember_dpi(dpi if held is None else held, target)
        # What `mouse` answers from until the next reading.
        if target.reading is not None:
            target.reading["dpi"] = dpi
        return dpi

    def gone_after_write(self, target: AttachedMouse, before: list[int]) -> None:
        """A mouse gone part way through a write that changed profile 1's stage list, or left
        it unknown: follow_dpi cannot read it now, so a remembered DPI is forgotten, as at a
        read that fails. `before` is the list from before the write."""
        if (target not in self.mice.attached and own_stages(self.mice.dpi.in_force(target)) is None
                and (target.mouse.flash_unknown or target.mouse.dpi_stages != before)):
            self.mice.dpi.forget_dpi(target)

    def _cmd_mouse_dpi_stages(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        if request.get("factory"):
            if request.get("reset") or request.get("value") is not None:
                return {"ok": False, "error": "factory takes no stages and no reset"}
            factory = self.mice.mouse_call(target.mouse.factory_profile, target=target)
            stages = factory.dpi_stages
            if not stages:
                return {"ok": False,
                        "error": f"the {target.name}'s factory profile holds no DPI stages"}
            request = {**request, "value": stages,
                       "default": stage_dpi(stages, factory.default_dpi_index),
                       "shift": stage_dpi(stages, factory.shift_dpi_index)}
        overlay = self.mice.dpi.edits_tuning(target)
        if overlay is not None:
            return self.mice.dpi.tune_stages(target, overlay, request)
        if request.get("reset"):
            return {"ok": False, "error": self.mice.dpi.no_tuning(target)}
        stages, default, shift = stage_request(request)
        before = target.mouse.dpi_stages
        try:
            image = self.flash_write(target, target.mouse.set_dpi_stages, stages, default, shift)
        except MouseError:
            self.gone_after_write(target, before)
            raise
        reply = {"ok": True, "dpi_stages": image.dpi_stages,
                 "default": image.dpi_stages[image.default_dpi_index],
                 "shift": image.dpi_stages[image.shift_dpi_index],
                 "note": self.flash_note(target) + self.rate_unsent(target)}
        dpi = self.follow_dpi(target, before, image.dpi_stages)
        if dpi is not None:
            reply["dpi"] = dpi
        return reply

    @staticmethod
    def no_button_table(mouse: Mouse, what: str) -> dict | None:
        """The refusal of a write to G4 to G9 on `mouse`, or None where its model's table is
        known: the write puts the G502 X PLUS's six entries at its onboard indices, which are
        other buttons on any other mouse."""
        if "buttons" in mouse.capabilities:
            return None
        return {"ok": False,
                "error": f"{mouse.name} has no button table this build knows; {what} would "
                         f"write another model's entries into its profile"}

    def _cmd_mouse_enable_buttons(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        mouse = target.mouse
        refused = self.no_button_table(mouse, "enabling the extra buttons")
        if refused is not None:
            return refused
        image = self.flash_write(target, mouse.enable_buttons)
        return {"ok": True, "buttons_enabled": buttons_enabled(image),
                "enabled_buttons": enabled_buttons(image),
                "buttons": mouse.button_words(image),
                "note": (enable_note(target.name, target.host, mouse.running_profile)
                         + self.rate_unsent(target))}

    def _cmd_mouse_fallback_buttons(self, request: dict) -> dict:
        """G4 to G9 in profile 1 as the mouse is to run them without Gearbench, through the
        same backed-up and verified write as enabling them."""
        target = self.mice.mouse_for(request)
        mouse = target.mouse
        refused = self.no_button_table(mouse, "the fallback")
        if refused is not None:
            return refused
        image = self.flash_write(target, mouse.write_fallback_buttons)
        return {"ok": True, "buttons_fallback": buttons_fallback(image),
                "buttons": mouse.button_words(image),
                "note": (fallback_note(target.name, target.host, mouse.running_profile)
                         + self.rate_unsent(target))}

    def _cmd_mouse_restore(self, request: dict) -> dict:
        target = self.mice.mouse_for(request)
        before = target.mouse.dpi_stages
        try:
            image, path = self.flash_write(target, target.mouse.restore_backup, rate=False)
        except MouseError:
            self.gone_after_write(target, before)
            raise
        note = self.flash_note(target)
        if target.reading is not None and image.report_rate is not None:
            # What `mouse` answers from: in effect from the write's commit, which in HOST mode
            # puts the mouse on the stored rate too (flash_write), as mouse_rate's. A rate
            # send_rate gets taken below replaces it.
            target.reading["report_rate"] = image.report_rate
        if target.host:
            # No profile runs in HOST mode: the rate in force goes live (send_rate), as at an
            # arm. Not to a mouse the put-back found not answering (put_back_after_write).
            sent = self.mice.mouse_answering(target) and self.mice.dpi.send_rate(target)
            if target not in self.mice.attached:
                # Torn down at the send: follow_dpi would ask the closed node.
                self.gone_after_write(target, before)
                raise MouseError(GONE_AFTER_WRITE)
            if not sent:
                note += self.rate_owed(target)
        reply = {"ok": True, "restored_from": path,
                 "buttons_enabled": buttons_enabled(image),
                 "note": note}
        dpi = self.follow_dpi(target, before, image.dpi_stages)
        if dpi is not None:
            reply["dpi"] = dpi
        return reply
