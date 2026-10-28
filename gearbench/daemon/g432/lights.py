"""The lights of the attached headset where it lists them (gearbench.headset_lights): their
zones and startup effect, read at the attach, the `g432_lights` command, the lighting a G key
bound to it shows (step), and the release as the headset is let go. The controller reads them
again where the attach could not (G432Headset._read_unread).

With Follow the keyboard on (`headset_sync`, config.headset_sync_color) the lights show the
color a synced mouse takes from the leader, at the level it shows it (sync), as a synced mouse
does (the owner's decisions of 2026-10-25): every path that gives a synced mouse its color
gives it here too (Daemon.sync_to_leader), the headset's own effect, its release and a store
are refused meanwhile (SYNC_ON), a G key's step goes to the keyboard's brightness in their
place (_step_keyboard), sync turned off gives the lights back what Gearbench
showed on them before, or the headset's own lighting (sync_changed), and a power-off leaves
them black with their claim held in place of the release (go_dark)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, cast

from ...config import DEFAULT_COLOR, STATIC, format_color, parse_color, read_switch
from ...headset_lights import (
    BRIGHTNESS_STEPS,
    FULL_BRIGHTNESS,
    NO_CONTROL,
    HeadsetZones,
    find_lights,
)
from ...hidpp import HidppError, HidppFailure
from ...keyboard import followed
from ...lighting import EffectParams, effect_order
from ..shared import (
    NOT_SYNCED,
    SYNC_ON,
    failure_reason,
    name_text,
    no_effect,
    scaled,
    switch_value,
    with_note,
)

if TYPE_CHECKING:
    from ...g432 import G432
    from .. import Daemon
    from . import G432Headset

log = logging.getLogger("gearbench")

#: Why a lights request is refused where the headset lists no lights (`find_lights` found
#: none); the model's name goes in.
NO_LIGHTS = "the {} lists no lights"
#: Its refusal where the attach could not read them: the model's name, why, then what it is
#: plugged in by, as "its receiver".
LIGHTS_UNREAD = ("the {}'s lights could not be read when it attached ({}); unplug {} and "
                 "plug it back in")
#: Its refusal of the startup effect where the lights list no such setting, and of a store
#: where Gearbench shows no lighting, the model's name in each; and of more than one change
#: of the lights in a request.
NO_STARTUP = "the {}'s lights list no startup effect"
NOTHING_TO_STORE = ("Gearbench shows no lighting on the {} to store: send it an effect "
                    "first")
ONE_LIGHTS_CHANGE = "give the lights an effect, release, startup or store, one of the four"
#: The effect that shows a zone off, as G HUB's G1 sends the cycle's last step (`01 00`),
#: and the lighting a G key takes where Gearbench has shown none: static in `g432_lights`'
#: default color.
OFF_EFFECT = "disabled"
DEFAULT_SHOWN = {"effect": STATIC, "color": format_color(DEFAULT_COLOR)}
#: The wait for the lights' release as the headset is let go, each of its three tries
#: (HeadsetZones.release's two retries): a shutdown waits on them.
RELEASE_TIMEOUT = 0.3


def _effect_words(name: str, color: str, sent: Iterable[EffectParams]) -> str:
    """The effect `name` as the journal gives it, with `color` where a zone's write of it,
    one of `sent`, took one: cycle's would name a color never sent."""
    return f"{name}, {color}" if any("color" in params for params in sent) else name


def _next_level(now: int, cycle: bool) -> int:
    """A G key's level after `now`, in percent: the cycle's next step below it
    (BRIGHTNESS_STEPS), full after off, or, with `cycle` False, off, or full where it is off."""
    if not cycle:
        return FULL_BRIGHTNESS if now == 0 else 0
    return next((step for step in BRIGHTNESS_STEPS if step < now), FULL_BRIGHTNESS)


class HeadsetLighting:
    """The attached headset's lights, and what is kept of them across a replug."""

    def __init__(self, daemon: Daemon, g432: G432Headset) -> None:
        self.d = daemon
        self.g432 = g432
        #: The lights' zones on a headset that lists them; whether Gearbench holds them
        #: (from the first fn 8 `01` answered till the release or a return with Follow the
        #: keyboard off: returned), the effect and color it last wrote, and why their writes
        #: are refused, the first claim's answer on a headset with no host control.
        self.zones: HeadsetZones | None = None
        self.claimed = False
        self.shown: dict | None = None
        self.refused: str | None = None
        #: Set where a return dropped the claim (returned): a headset that kept its power
        #: may still show the effect (not checked on a G933), so the release still goes
        #: (_may_hold).
        self._release_owed = False
        #: Whether the lights show their startup effect as the headset switches on, as last
        #: read, None on lights that list no such setting.
        self.startup: bool | None = None
        #: By product id, the effect and color the daemon last stored in the lights (`store`),
        #: kept across a replug, since nothing reads what a headset keeps; none for an id
        #: whose store failed, which may have reached it.
        self._stored: dict[int, dict] = {}
        #: By product id, the effect and color Gearbench last showed, which a G key that takes
        #: the lighting shows again (step), kept across a replug; and the brightness of the
        #: lighting it shows, in percent, which a G key steps (BRIGHTNESS_STEPS).
        self._last_shown: dict[int, dict] = {}
        self.brightness = FULL_BRIGHTNESS
        #: Why the lights could not be read at the attach, None where they were or have been
        #: since (G432Headset._read_unread).
        self.unread: str | None = None
        #: With Follow the keyboard on: the color the lights took last, at its level, None
        #: while not known; whether they are owed the synced color (give_owed); and whether
        #: NOT_SYNCED was said since the attach.
        self.sync_shown: int | None = None
        self.sync_owed = False
        self._not_synced_said = False
        #: Set at a power-off by go_dark: let_go keeps the claim in place of the release.
        self._kept_dark = False

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"g432_lights": self._cmd_g432_lights, "headset_sync": self._cmd_headset_sync}

    def read(self) -> None:
        """Read the lights' zones, None for a headset that lists none, and their startup
        effect, and forget why an earlier read failed; nothing is kept from a read that
        fails. Raises HidppFailure."""
        zones = find_lights(cast("G432", self.g432.dev))
        startup = None if zones is None else zones.startup()
        self.zones, self.startup, self.unread = zones, startup, None
        # What they show is not known: a synced headset takes its color at the next
        # give_owed.
        self.sync_owed = True

    def read_at_attach(self) -> None:
        """The attach's read (read), a failure said in the journal and kept as `unread`, so
        the controller reads them again later. Never raises."""
        try:
            self.read()
        except HidppFailure as exc:
            self.unread = failure_reason(exc)
            log.info("%s: its lights could not be read: %s", self.g432.model.key, self.unread)

    def let_go(self) -> str | None:
        """At the headset's teardown: the release, where the lights may be Gearbench's
        (_may_hold), so the headset shows its own lighting again, or after go_dark nothing,
        the claim kept; and what was read of them forgotten. Where the claim was kept, what
        the teardown's line says of the lights: dark where they took the black or the
        headset is away, else not left dark; None where it was not kept. Never raises: a
        node gone fails the release at once."""
        kept = self.zones is not None and self.claimed and self._kept_dark
        said = None
        if kept:
            dark = self.sync_shown == 0x000000 or self.g432.away()
            said = ("its lights left dark with the keyboard, their claim held" if dark
                    else "its lights not left dark, their claim held")
        if self.zones is not None and self._may_hold() and not kept:
            with contextlib.suppress(HidppFailure, OSError):
                self.zones.release(timeout=RELEASE_TIMEOUT)
        self.zones, self.claimed, self.shown = None, False, None
        self.refused = self.unread = self.startup = self.sync_shown = None
        self._not_synced_said = self._kept_dark = self._release_owed = False
        return said

    def _may_hold(self) -> bool:
        """Whether the lights may be Gearbench's, so the release goes: held, or held before a
        return that dropped the claim (returned)."""
        return self.claimed or self._release_owed

    def go_dark(self) -> None:
        """At a power-off while the lights follow the keyboard (config.headset_sync_color):
        black on them (sync), and their claim kept at let_go, so they do not light up in the
        headset's own lighting with the machine off, as a synced mouse stays dark with its
        claim held (the owner's decision of 2026-10-26). Lights it does not drive and a
        headset away are sent nothing, as sync sends them nothing."""
        self.sync(0x000000)
        self._kept_dark = self.d.config.headset_sync_color

    def reply(self) -> dict | None:
        """The `g432` reply's `lights`: on a headset that lists them, its zones and the effects
        they all take, whether Gearbench holds them, what it last wrote and at what brightness,
        in percent, 0 off (a G key's), each null while it shows none, as after a return with
        Follow the keyboard off (returned), why a write is refused,
        whether they show their startup effect, null where they list no such setting, and what
        the daemon last stored in them, null for none known, and whether they follow the
        keyboard that leads (config.headset_sync_color), `shown` staying what Gearbench
        showed on them before, which sync turned off gives back; null on another."""
        if self.zones is None:
            return None
        return {"zones": [zone.name for zone in self.zones.clusters],
                "effects": self.effects(), "claimed": self.claimed, "shown": self.shown,
                "brightness": None if self.shown is None else self.brightness,
                "refused": self.refused, "startup": self.startup,
                "stored": self._stored.get(cast("G432", self.g432.dev).product),
                "synced": self.d.config.headset_sync_color}

    def effects(self) -> list[str]:
        """The effects every zone takes, by name, in every Effect row's order (effect_order),
        the headset's own in the first zone's."""
        zones = cast("HeadsetZones", self.zones).clusters
        return effect_order(effect.name for effect in zones[0].effects
                            if all(zone.has_effect(effect.name) for zone in zones))

    def _cmd_g432_lights(self, request: dict) -> dict:
        """The lights: `effect`, by name, with `color` where it takes one, either left out
        taken from the lighting shown (_filled), on every zone, each right after its claim;
        or with `release` the lights back to the headset's own;
        or `startup`, true or false, their startup effect (_set_startup); or with `store` the
        lighting shown kept in the headset (_store). More than one of the four is refused
        with nothing sent, and while the lights follow the keyboard all but `startup`
        (SYNC_ON). The first claim a headset with no host control refuses ends every write
        (NO_CONTROL), the effect never sent."""
        if (refused := self.refusal()) is not None:
            return refused
        zones = cast("HeadsetZones", self.zones)
        startup, store = request.get("startup"), request.get("store") is True
        if sum((request.get("effect") is not None, request.get("release") is True,
                startup is not None, store)) > 1:
            return {"ok": False, "error": ONE_LIGHTS_CHANGE}
        if startup is not None:
            return self._set_startup(zones, read_switch(startup, "g432_lights.startup"))
        if (refused := self.sync_refusal()) is not None:
            return refused
        if store:
            return self._store(zones)
        if request.get("release") is True:
            # Nothing to give back before a claim.
            if not self._may_hold():
                return self.g432.reply()
            return self.g432.call(self._release) or self.g432.reply()
        return self.set_effect(zones, self._filled(request))

    def _last_lighting(self) -> dict:
        """The effect and color Gearbench shows, else the last it showed on this product,
        else DEFAULT_SHOWN; only with a headset attached."""
        return (self.shown or self._last_shown.get(cast("G432", self.g432.dev).product)
                or DEFAULT_SHOWN)

    def _filled(self, request: dict) -> dict:
        """`g432_lights`' `effect` and `color`, what it leaves out of them taken from
        _last_lighting (the owner's decision of 2026-10-27): an effect alone keeps the color
        shown, as the keyboard's effect keeps its layer's, and a color alone recolors the
        effect shown. A request with neither goes on as it is, for set_effect to refuse."""
        given = {key: request[key] for key in ("effect", "color") if key in request}
        return {**self._last_lighting(), **given} if given else request

    def refusal(self) -> dict | None:
        """Why no write goes to the lights: no headset, lights the attach could not read or
        that it does not list, or lights that took no host control (NO_CONTROL); None where
        one goes."""
        if self.g432.dev is None:
            return {"ok": False, "error": self.g432.missing()}
        if self.zones is None:
            return {"ok": False, "error": self.unread_words()
                    or NO_LIGHTS.format(self.g432.model.short)}
        if self.refused is not None:
            return {"ok": False, "error": self.refused}
        return None

    def unread_words(self) -> str | None:
        """Why the attached headset's lights are not known: LIGHTS_UNREAD's sentence, with the
        cause the attach's read failed with and the replug, which a write's refusal and the
        `g432` reply's `lights_unread` give; None where they were read."""
        if self.unread is None:
            return None
        model = self.g432.model
        return LIGHTS_UNREAD.format(model.short, self.unread, f"its {model.plugged_by}")

    def sync_refusal(self) -> dict | None:
        """Why the headset's own lighting is refused, while its lights follow the keyboard
        that leads (SYNC_ON); None while they do not."""
        if not self.d.config.headset_sync_color:
            return None
        leader = followed(self.d.per_keyboard.leader())
        return {"ok": False,
                "error": SYNC_ON.format(device="headset", leader=leader, command="headset-sync")}

    def set_effect(self, zones: HeadsetZones, request: dict,
                   percent: int = FULL_BRIGHTNESS) -> dict:
        """`g432_lights` with `effect`: on every zone, with `color`, each right after its
        claim, the first claim alone (_first_claim); a G key's lighting at `percent` of its
        brightness (zone_writes), which a reply gives till the next effect."""
        model = self.g432.model
        name = name_text(request.get("effect"), "g432_lights.effect")
        color = parse_color(request["color"], "g432_lights.color")
        effects = self.effects()
        wanted = [name, OFF_EFFECT] if percent == 0 else [name]
        if (missing := next((effect for effect in wanted if effect not in effects),
                            None)) is not None:
            return {"ok": False, "error": no_effect(model.short, missing, effects,
                                                    none="its zones share none")}
        writes = self.zone_writes(zones, name, color, percent)
        product = cast("G432", self.g432.dev).product

        def write() -> None:
            if not self._hold(zones):
                return
            for index, effect, params in writes:
                zones.set_effect(index, effect, wait=True, **params)
            shown = self.shown = {"effect": name, "color": format_color(color)}
            self._last_shown[product], self.brightness = shown, percent
            log.info("%s: lights set to %s%s", model.key,
                     _effect_words(name, format_color(color),
                                   (params for _index, _effect, params in writes)),
                     "" if percent == FULL_BRIGHTNESS else f", at {percent}%")

        refused = self.g432.call(write)
        if self.refused is not None:
            return {"ok": False, "error": self.refused}
        return refused or self.g432.reply()

    @staticmethod
    def zone_writes(zones: HeadsetZones, name: str, color: int,
                    percent: int) -> list[tuple[int, str, EffectParams]]:
        """Each zone's effect and its parameters for `name` in `color` at `percent` of its
        brightness, and at 0 the zone off (OFF_EFFECT): the color scaled where the effect
        has one, breathe's too, else the intensity, as cycle's. Measured on a G933 on
        2026-10-25: breathe at intensity 50, 25 and 12 looked no dimmer, breathe at full in
        its color cut to 12 % was clearly dimmer, and static dimmed at each step of its
        color; cycle's intensity is not yet checked on one."""
        writes: list[tuple[int, str, EffectParams]] = []
        for zone in zones.clusters:
            if percent == 0:
                writes.append((zone.index, OFF_EFFECT, {}))
                continue
            params = zones.effect_params(zone.index, name, color)
            if "color" in params:
                params["color"] = scaled(params["color"], percent)
            elif "intensity" in params:
                params["intensity"] = params["intensity"] * percent // FULL_BRIGHTNESS
            writes.append((zone.index, name, params))
        return writes

    def step(self, cycle: bool) -> dict:
        """The lighting Gearbench shows at the brightness cycle's next step (BRIGHTNESS_STEPS),
        or, with `cycle` False, off, or at full where it is off. Where Gearbench shows none,
        it takes the lighting: the last effect it showed, by product id, or the default,
        static in DEFAULT_COLOR, at the step after full, or off (the owner's answer of
        2026-10-25). While the lights follow the keyboard, the keyboard takes the step in
        their place (_step_keyboard)."""
        if (refused := self.refusal()) is not None:
            return refused
        if self.d.config.headset_sync_color:
            return self._step_keyboard(cycle)
        percent = _next_level(FULL_BRIGHTNESS if self.shown is None else self.brightness, cycle)
        return self.set_effect(cast("HeadsetZones", self.zones), self._last_lighting(), percent)

    def _step_keyboard(self, cycle: bool) -> dict:
        """The leading keyboard's brightness at the next level after the one it shows
        (_next_level), set as the keyboard's own `brightness` command sets it, so the synced
        lights and mice follow (the owner's decision of 2026-10-26)."""
        level = _next_level(self.d.per_keyboard.brightness(), cycle)
        return self.d.handle_command({"command": "brightness", "value": level})

    def _set_startup(self, zones: HeadsetZones, on: bool) -> dict:
        """`g432_lights` with `startup`: the startup effect's setting written, then read
        back (HeadsetZones.set_startup). Refused where the lights list no such setting, and,
        with nothing sent, on a model not tested on this hardware (kept_refusal)."""
        model = self.g432.model
        if self.startup is None:
            return {"ok": False, "error": NO_STARTUP.format(model.short)}

        def write() -> None:
            # A write that fails keeps the setting read before: the next attach reads it.
            self.startup = zones.set_startup(on)
            log.info("%s: startup effect set %s, read back %s", model.key,
                     "on" if on else "off", "on" if self.startup else "off")

        return self.g432.checked_call(write) or self.g432.reply()

    def _store(self, zones: HeadsetZones) -> dict:
        """`g432_lights` with `store`: the effect and color Gearbench shows now written to
        every zone for the headset to keep (HeadsetZones.store_effect), the color as shown,
        not corrected, as G HUB stores it, and remembered as the one stored. Refused where
        Gearbench shows no lighting, and with nothing sent, the claim included, on a model
        not tested on this hardware (HeadsetZones.store_effect)."""
        model, shown = self.g432.model, self.shown
        if shown is None:
            return {"ok": False, "error": NOTHING_TO_STORE.format(model.short)}
        name, color = shown["effect"], parse_color(shown["color"], "g432_lights.color")
        writes = [(zone.index, zones.effect_params(zone.index, name, color))
                  for zone in zones.clusters]
        product = cast("G432", self.g432.dev).product

        def write() -> None:
            # Unknown until every zone answers: a write that fails may have reached one.
            self._stored.pop(product, None)
            for index, params in writes:
                zones.store_effect(index, name, **params)
            self._stored[product] = dict(shown)
            log.info("%s: lights stored as %s", model.key, _effect_words(
                name, shown["color"], (params for _index, params in writes)))

        return self.g432.checked_call(write) or self.g432.reply()

    def _hold(self, zones: HeadsetZones) -> bool:
        """The first claim, alone, where Gearbench does not hold the lights yet
        (_first_claim): whether Gearbench holds them, False for lights with no host control.
        Raises HidppFailure."""
        if not self.claimed:
            # Held from its answer: an effect that fails after it leaves them the host's.
            self.claimed = self._first_claim(zones)
        return self.claimed

    def _first_claim(self, zones: HeadsetZones) -> bool:
        """The first claim, alone: True once answered. A refusal for want of host control
        makes the lights' writes refused (NO_CONTROL) before any effect is sent, said once,
        and is False. Raises HidppFailure for any other failure."""
        try:
            zones.claim()
        except HidppError as exc:
            # fn 8 answered as no such function: this 0x8070 has no host control.
            if not exc.invalid_function:
                raise
            self.refused = NO_CONTROL.format(self.g432.model.short)
            log.info("%s: its lights take no host control, so Gearbench does not write "
                     "them: %s", self.g432.model.key, failure_reason(exc))
            return False
        return True

    def _release(self) -> None:
        cast("HeadsetZones", self.zones).release()
        self.claimed, self.shown, self._release_owed = False, None, False
        log.info("%s: lights given back to the headset", self.g432.model.key)

    # -- following the keyboard that leads -----------------------------------

    def sync(self, color: int, repeat: bool = True) -> None:
        """While the lights follow the keyboard (config.headset_sync_color), show `color`, the
        one a synced mouse takes from the leader (Daemon.synced_color), or black as the
        keyboard goes dark, at the level a synced mouse shows it (Keyboard.at_level): static
        on every zone that has it, each right after its claim, as a synced mouse takes it. A
        headset that shows it already is sent nothing, nor one away, which is owed it as it
        comes back (returned); one whose write fails is owed it (give_owed), but for a drag's
        preview (`repeat` False). Lights Gearbench does not drive are left as they are
        (not_driven), said once an attach. Never raises into the keyboard's paths that call
        it."""
        if not self.d.config.headset_sync_color:
            return
        zones = self.zones
        if zones is None or self.refused is not None:
            if (said := self.not_driven()) is not None and not self._not_synced_said:
                self._not_synced_said = True
                log.info("%s", said)
            return
        color = self.d.keyboard.at_level(color)
        if self.sync_shown == color or self.g432.away():
            return
        writes = [(zone.index, zones.effect_params(zone.index, STATIC, color))
                  for zone in zones.clusters if zone.has_effect(STATIC)]
        # Taken first: a write that finds the node gone lets the headset go (call).
        model = self.g432.model

        def write() -> None:
            # Not known until every zone answers.
            self.sync_shown = None
            if not self._hold(zones):
                return
            for index, params in writes:
                zones.set_effect(index, STATIC, wait=True, **params)
            self.sync_shown = color

        refused = self.g432.call(write)
        if refused is None:
            return
        self.sync_owed = self.sync_owed or repeat
        # A drag sends many previews.
        log.log(logging.INFO if repeat else logging.DEBUG,
                "%s: its lights did not take the keyboard's color: %s", model.key,
                refused["error"])

    def not_driven(self) -> str | None:
        """NOT_SYNCED's words for an attached headset whose lights Gearbench does not drive,
        none listed or no host control (NO_CONTROL); None for lights it drives, lights the
        attach could not read, which it reads again, and no headset."""
        if self.g432.dev is None or self.unread is not None:
            return None
        if self.zones is not None and self.refused is None:
            return None
        return NOT_SYNCED.format(self.g432.model.short)

    def returned(self) -> None:
        """The headset is back from away, or the machine from a suspend: its lights may have
        lost what they showed with its RAM, and the claim. A synced headset is owed its color
        again (give_owed). On another, what Gearbench showed is forgotten, not sent again, so
        the reply gives the headset's own lighting and a G key's step starts at full with the
        effect last shown (step), the owner's decision of 2026-10-27; the release still goes
        where they were held (_may_hold)."""
        self.sync_shown, self.sync_owed = None, True
        if self.d.config.headset_sync_color:
            return
        self._release_owed = self._may_hold()
        self.claimed, self.shown = False, None

    def give_owed(self) -> None:
        """The color a synced headset is owed (sync_owed), sent once: after the attach's read,
        at a return and after each battery read that goes through (G432Headset.tend), never
        at every pass, so a write that keeps failing is tried again no faster."""
        if self.sync_owed:
            self.sync_owed = False
            self.sync(self.d.synced_color())

    def sync_changed(self, on: bool) -> None:
        """Act on Follow the keyboard turned on or off: on, the lights take the synced color
        (sync); off, they get back the effect and color Gearbench showed on them before, at
        their brightness, or, where it showed none, the headset's own lighting through the
        release. A headset away is sent nothing at sync off, since it shows its own lighting
        as it switches on, which its return then gives (returned). Either way the desktop's
        idleness is followed as the switches say (Session.watch_idle_for_both)."""
        self.d.session.watch_idle_for_both(paint=False)
        if on:
            self.sync(self.d.synced_color())
            return
        self.sync_shown = None
        if not self.claimed or self.g432.away():
            return
        # Taken first: a write that finds the node gone lets the headset go (call).
        model, zones = self.g432.model, cast("HeadsetZones", self.zones)
        reply = (self.set_effect(zones, self.shown, self.brightness) if self.shown is not None
                 else self.g432.call(self._release) or {"ok": True})
        if not reply["ok"]:
            log.info("%s: its own lighting could not be given back: %s", model.key,
                     reply["error"])

    def _cmd_headset_sync(self, request: dict) -> dict:
        """Follow the keyboard on or off for the lights of every headset and every profile
        (config.headset_sync_color), saved before it acts, so a refused save changes nothing,
        then acted on (sync_changed). On, the reply gives the color the lights follow, before
        the keyboard's brightness scales it, and a note where the headset attached has lights
        Gearbench does not drive (not_driven)."""
        value = switch_value(request.get("value"), "headset_sync")
        was = self.d.config.headset_sync_color
        self.d.save_setting(headset_sync_color=value)
        if value or was:
            self.sync_changed(value)
        reply: dict = {"ok": True, "headset_sync_color": value}
        if not value:
            return reply
        reply["color"] = format_color(self.d.synced_color())
        return with_note(reply, self.not_driven())
