"""The `mouse` command: what a client is told about the selected mouse, or about the one a
request's `device` names, and the reading kept between two asks, so that an idle mouse is not
woken at every poll. It reads a mouse and writes nothing to one."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...config import format_color
from ...mouse import (
    CABLED_IDS,
    STAGE_COLOR_NAMES,
    STAGE_COLORS,
    buttons_idle_words,
    model_key,
)
from ...onboard import entry_job
from ...profiles import mouse_color_json
from ...report import untested
from ...wording import product_route
from ..shared import attached_entry
from .attached import MOUSE_POLL_RETRY, AttachedMouse, effect_names

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

# How long a reading answers `mouse` while nothing is heard: asking an idle mouse wakes it
# and can outlast the probe, marking it not answering. Anything the mouse says makes the next
# `mouse` read it again, except a battery report from a mouse already answering
# (MouseBatteries.take_report).
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

    def status_entries(self) -> list[dict]:
        """`status`'s `mice_attached`: each mouse attached (shared.attached_entry), with its
        route by its product id."""
        return [attached_entry(m.name, str(m.dev.path), product_route(m.product, CABLED_IDS),
                               m.battery, m.battery_at, answering=not m.silent,
                               battery_found="battery" in m.mouse.capabilities)
                for m in self.mice.attached]

    def _cmd_mouse(self, request: dict) -> dict:
        # Attaches nothing: a probe of a mouse that is off stalls the loop, so the loop's retry
        # reconnects, pulled forward (poll_for_mouse). `ask` false is a client's request for
        # the last reading with nothing sent (reading_for). `device` answers for the attached
        # mouse it names by model or product id (named_in) and leaves the selection as it is;
        # one naming no attached mouse is a MouseError (asked).
        current = (self.mice.current if request.get("device") is None
                   else self.mice.mouse_for(request, write=False))
        if current is None:
            self.mice.poll_for_mouse()
            # The selected mouse's bindings, kept while it is away (Mice.selected), so the Mouse
            # tab's button rows follow an M key or a game profile while it is gone.
            selected = self.mice.selected
            away = {} if selected is None else self.model_bindings(selected)
            return {"ok": False, "error": self.mice.unattached.none_attached(), **away}
        info = self.reading_for(current, request)
        clusters = current.lighting.clusters if current.lighting else ()
        profile, table = current.mouse.profile, current.mouse.button_table
        info.update({
            "answering": not current.silent,
            "read_at": current.read_wall,
            "battery": current.battery,
            "battery_at": current.battery_at,
            "ok": True,
            "device": str(current.dev.path),
            "name": current.name,
            "product": f"{current.product:04x}",
            # What this model can do, so a front end renders only the sections it has.
            "capabilities": current.capabilities,
            # Why every write to profile 1 is refused, for the window to say before a click.
            "flash_refused": current.mouse.flash_refusal,
            # Why the enable write is refused, for the front ends to leave it out.
            "enable_refused": current.enable_refused,
            # Whether `mouse_rate` sets the rate live alone (Dpi.live_rate).
            "rate_live": current.sets_rate_live,
            # A model not yet run on, whose note asks for a device report (report.untested).
            "untested": untested(current.product),
            # Every attached mouse, for a choice, keyed by product id as the CLI reads it: two of
            # one model would collide.
            "devices": [{"product": f"{m.product:04x}", "name": m.name,
                         "device": str(m.dev.path),
                         "selected": m is self.mice.current,
                         # As this reply's own `answering`: switched off or asleep behind
                         # its receiver, which keeps it attached.
                         "answering": not m.silent}
                        for m in self.mice.attached],
            "clusters": [c.name for c in clusters],
            # 0x0013, which effect_names leaves out, changed nothing on the hardware.
            "effects": effect_names(clusters),
            "input": [str(r.path) for r in current.input],
            # The node and why, when the daemon may not read it (open_mouse_input).
            "input_denied": current.input_denied,
            "color": format_color(current.color) if current.color is not None else None,
            "effect": current.effect,
            # Whose lighting a mouse that waits to claim shows, or None (lighting_shown).
            "lighting_shown": current.lighting_shown,
            # The DPI indicator's color per stage, stage 1 first; none for a model without a table.
            "stage_colors": [format_color(c) for c in STAGE_COLORS.get(current.product, ())],
            # Each of those in a word, for the window's dots; None for a color without one.
            "stage_color_names": [STAGE_COLOR_NAMES.get(c)
                                  for c in STAGE_COLORS.get(current.product, ())],
            "sync_color": self.d.config.mouse_sync_color,
            # The loaded game profile's mouse color, which a synced mouse shows in place of
            # the keyboard's (Daemon.synced_color), or None.
            "profile_mouse_color": mouse_color_json(self.d.profile_color()),
            **self.model_bindings(current.product),
            "buttons_enabled": bool(profile and table and table.all_enabled(profile)),
            "enabled_buttons": table.enabled(profile) if profile and table else [],
            "buttons_fallback": bool(profile and table and table.holds_fallback(profile)),
            "button_table": self.button_table(current),
            # HOST mode: `capable` means a spy HOST mode drives (spy.SPY_MODELS), `armed` that
            # an arm ran to its end and no hand-back has begun since, `spy_buttons` the buttons
            # its masks carry.
            "host": {"wanted": self.d.config.mouse_host_mode,
                     "capable": current.spy is not None,
                     "active": current.host,
                     "error": current.host_error,
                     "armed": current.armed,
                     "spy_buttons": (list(current.its_spy.model.buttons)
                                     if current.host else [])},
            "game_profile": self.mice.dpi.tuning_reply(current),
        })
        return info

    def model_bindings(self, product: int) -> dict:
        """The `mouse` reply's `bindings` of mouse `product`'s model, each model's buttons bound
        apart, and that model (mouse.model_key) as `bindings_model`."""
        model = model_key(product)
        return {"bindings": self.d.games.mouse_bindings(model), "bindings_model": model}

    def button_table(self, attached: AttachedMouse) -> dict | None:
        """The `mouse` reply's `button_table`: the model's buttons in display order, each with
        its name, label, default binding (ButtonTable.defaults) and what profile 1 as last read
        has it do (entry_job, None before a read); whether its event node is read; and `idle`,
        why their bindings do nothing now (buttons_idle_words), None while the daemon runs the
        mouse in HOST mode (Mice.runs_host) and on a model whose event node is read, whatever
        profile 1 holds. None for a model without a table."""
        table, profile = attached.mouse.button_table, attached.mouse.profile
        if table is None:
            return None
        idle = (None if self.mice.runs_host(attached) or table.event_node
                else buttons_idle_words(attached.name, attached.spy is not None))
        defaults = table.defaults
        return {"buttons": [{"name": slot.name, "label": slot.label,
                             "default": defaults[slot.name],
                             "onboard": entry_job(profile.button(slot.index)) if profile
                             else None}
                            for slot in table.buttons],
                "event_node": table.event_node, "idle": idle}

    def reading_for(self, current: AttachedMouse, request: dict) -> dict:
        """The readings `mouse`'s reply starts from: a fresh reading when `request` does not
        say `ask` false, one is due (reading_due) and the mouse, not marked silent, answers;
        else its last (last_reading). A mouse found not answering is marked silent
        (mark_silent), which the reply's `answering` says, and a marked one is rechecked within
        MOUSE_POLL_RETRY unless its receiver said its link is down (take_link_notice)."""
        if (not current.silent and request.get("ask", True) is not False
                and self.reading_due(current)):
            if self.mice.mouse_answers(current):
                # Not the battery once the mouse reports it by itself: nothing is sent to an
                # idle mouse for it, as G HUB sends nothing.
                fresh = self.mice.mouse_call(
                    lambda: current.mouse.describe(battery=not current.battery_reports),
                    target=current)
                if fresh["answering"]:
                    self.keep_reading(current, fresh)
                    return fresh
            self.mice.mark_silent(current)
        if current.silent:
            # Someone is watching: the loop's recheck comes within MOUSE_POLL_RETRY, unless the
            # link is down (tend_mice).
            current.recheck_at = min(current.recheck_at,
                                     time.monotonic() + MOUSE_POLL_RETRY)
        # A read the mouse did not answer holds no readings: the last one's go with its read_at.
        return self.last_reading(current)

    def reading_due(self, attached: AttachedMouse) -> bool:
        """Whether `mouse` should read this mouse, not answer from its last reading. A failed
        write's unknown profile sector (flash_unknown) is settled only by a reading, and one
        taken in the other mode (`host`) is out of date: the hand-back sends outside
        mouse_call, so it moves no `heard_at` (Mice.hand_back), and leaves the rate in force
        unknown (Dpi.out_of_force)."""
        return (attached.reading is None
                or attached.reading.get("host") != attached.host
                or attached.heard_at > attached.read_at
                or time.monotonic() - attached.read_at >= MOUSE_READING_MAX_AGE
                or attached.mouse.flash_unknown)

    def keep_reading(self, attached: AttachedMouse, fresh: dict) -> None:
        """Keep what one reading of a mouse said, for `mouse` to answer from."""
        attached.reading = {key: fresh.get(key) for key in MOUSE_READINGS}
        attached.reading["mode"] = (fresh.get("onboard") or {}).get("mode")
        # The mode the daemon counted it in (reading_due).
        attached.reading["host"] = attached.host
        attached.read_at = time.monotonic()
        attached.read_wall = round(time.time())
        if fresh.get("battery") is not None:
            self.mice.battery.take(attached, fresh["battery"], at=attached.read_wall)

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
