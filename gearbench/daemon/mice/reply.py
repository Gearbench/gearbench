"""The `mouse` command: what the CLI and the window are told about the selected mouse, and
the reading kept between two asks, so that an idle mouse is not woken at every poll. It
reads a mouse and writes nothing to one."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...config import MOUSE_BUTTONS, format_color
from ...mouse import (
    STAGE_COLOR_NAMES,
    STAGE_COLORS,
    buttons_enabled,
    buttons_fallback,
    enabled_buttons,
)
from .attached import MOUSE_POLL_RETRY, AttachedMouse, effect_names, no_mouse

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

# How long a reading answers `mouse` while nothing is heard: asking an idle mouse wakes it
# and can outlast the probe, marking it not answering. Anything the mouse says makes the next
# `mouse` read it again, except a battery report from a mouse already answering
# (take_battery_report).
MOUSE_READING_MAX_AGE = 300.0

#: What `mouse` keeps of a reading. The battery is kept apart, so a reading without it does not
#: lose the last one.
MOUSE_READINGS = ("dpi", "dpi_range", "report_rate", "report_rates")


class MouseReply:
    """The `mouse` command's reply, and each mouse's last reading."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"mouse": self._cmd_mouse}

    def _cmd_mouse(self, request: dict) -> dict:
        # Attaches nothing: a probe of a mouse that is off stalls the loop, so the loop's retry
        # reconnects, pulled forward (poll_for_mouse). Reads the mouse only when due
        # (reading_due), never with `ask` false, a client's request for the last reading with
        # nothing sent.
        current = self.mice.current
        # mouse_bind takes no mouse, so the Mouse tab's button rows follow an M key or a game
        # profile while none is attached.
        bindings = {b: self.d.games.active_mouse_binding(b) for b in MOUSE_BUTTONS}
        if current is None:
            self.mice.poll_for_mouse()
            return {"ok": False, "error": no_mouse(self.mice.denied_mice()),
                    "bindings": bindings}
        info, answering = self.reading_for(current, request)
        clusters = current.lighting.clusters if current.lighting else ()
        profile = current.mouse.profile
        info.update({
            "answering": answering,
            "read_at": current.read_wall,
            "battery": current.battery,
            "battery_at": current.battery_at,
            "ok": True,
            "device": str(current.dev.path),
            "name": current.name,
            "product": f"{current.product:04x}",
            # What this model can do, so a front end renders only the sections it has.
            "capabilities": current.mouse.capabilities
                            + (["lighting"] if clusters else []),
            # Every attached mouse, for a choice, keyed by product id as the CLI reads it: two of
            # one model would collide.
            "devices": [{"product": f"{m.product:04x}", "name": m.name,
                         "device": str(m.dev.path),
                         "selected": m is current}
                        for m in self.mice.attached],
            "clusters": [c.name for c in clusters],
            # 0x0013, which effect_names leaves out, changed nothing on the hardware.
            "effects": effect_names(clusters),
            "input": [str(r.path) for r in current.input],
            # The node and why, when the daemon may not read it (open_mouse_input).
            "input_denied": current.input_denied,
            "color": format_color(current.color) if current.color is not None else None,
            "effect": current.effect,
            # The DPI indicator's color per stage, stage 1 first; none for a model without a table.
            "stage_colors": [format_color(c) for c in STAGE_COLORS.get(current.product, ())],
            # Each of those in a word, for the window's dots; None for a color without one.
            "stage_color_names": [STAGE_COLOR_NAMES.get(c)
                                  for c in STAGE_COLORS.get(current.product, ())],
            "sync_color": self.d.config.mouse_sync_color,
            "bindings": bindings,
            "buttons_enabled": bool(profile and buttons_enabled(profile)),
            "enabled_buttons": enabled_buttons(profile) if profile else [],
            "buttons_fallback": bool(profile and buttons_fallback(profile)),
            # HOST mode: `capable` means a measured spy, `spy_buttons` the buttons its masks carry.
            "host": {"wanted": self.d.config.mouse_host_mode,
                     "capable": current.spy is not None,
                     "active": current.host,
                     "error": current.host_error,
                     "spy_buttons": (list(current.its_spy.model.buttons)
                                     if current.host else [])},
            "game_profile": self.mice.dpi.tuning_reply(current),
        })
        return info

    def reading_for(self, current: AttachedMouse, request: dict) -> tuple[dict, bool]:
        """The readings `mouse`'s reply starts from, and its `answering`: a fresh reading
        when `request` does not say `ask` false, one is due (reading_due) and the mouse, not
        marked silent, answers; else its last (last_reading). A mouse found not answering is
        marked (mark_silent), and one not answering is rechecked within MOUSE_POLL_RETRY unless
        its receiver said its link is down (take_link_notice)."""
        fresh = None
        if current.silent:
            answering = False
        elif request.get("ask", True) is False or not self.reading_due(current):
            answering = True
        elif not self.mice.mouse_answers(current):
            self.mice.mark_silent(current)
            answering = False
        else:
            # Not the battery once the mouse reports it by itself: nothing is sent to an idle
            # mouse for it, as G HUB sends nothing.
            fresh = self.mice.mouse_call(
                lambda: current.mouse.describe(battery=not current.battery_reports),
                target=current)
            answering = fresh["answering"]
            if answering:
                self.keep_reading(current, fresh)
            else:
                self.mice.mark_silent(current)
        if not answering:
            # Someone is watching: the loop's recheck comes within MOUSE_POLL_RETRY, unless the
            # link is down (tend_mice).
            current.recheck_at = min(current.recheck_at,
                                     time.monotonic() + MOUSE_POLL_RETRY)
        # A read the mouse did not answer holds no readings: the last one's go with its read_at.
        info = fresh if fresh is not None and answering else self.last_reading(current)
        return info, answering

    def reading_due(self, attached: AttachedMouse) -> bool:
        """Whether `mouse` should read this mouse, not answer from its last reading. A failed
        write's unknown profile sector (flash_unknown) is settled only by a reading."""
        return (attached.reading is None
                or attached.heard_at > attached.read_at
                or time.monotonic() - attached.read_at >= MOUSE_READING_MAX_AGE
                or attached.mouse.flash_unknown)

    def keep_reading(self, attached: AttachedMouse, fresh: dict) -> None:
        """Keep what one reading of a mouse said, for `mouse` to answer from."""
        attached.reading = {key: fresh.get(key) for key in MOUSE_READINGS}
        attached.reading["mode"] = (fresh.get("onboard") or {}).get("mode")
        attached.read_at = time.monotonic()
        attached.read_wall = round(time.time())
        if fresh.get("battery") is not None:
            attached.battery, attached.battery_at = fresh["battery"], attached.read_wall

    def last_reading(self, attached: AttachedMouse) -> dict:
        """The `mouse` reply's readings with nothing sent: the onboard profile as read at attach
        or the last write, the rest from the last reading, None ([] for report_rates) where
        there was none."""
        info = attached.mouse.describe(ask=False)
        kept = attached.reading or {}
        for key in MOUSE_READINGS:
            if key in kept:
                info[key] = kept[key]
        if info.get("onboard"):
            info["onboard"]["mode"] = kept.get("mode")
        return info
