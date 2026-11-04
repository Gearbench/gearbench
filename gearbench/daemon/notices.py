"""The battery_notices command: each device's low-battery notices preset of config.json's
battery_notices (config.BATTERY_PRESETS), listed and set, a keyboard's or a mouse's by its
model, attached or not, and a headset's by its key (the owner's decisions 3 and 10 of
2026-10-28). The notices themselves are each device's own (shared.BatteryNotices)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from ..a50x import MODELS as A50X_MODELS
from ..astro import KEY as A50_KEY, NAME as A50_NAME
from ..config import BATTERY_PRESETS, URGENT, battery_notices_of, keep_under, key_under
from ..g432 import MODELS as G432_MODELS
from ..keyboard import (
    key_name as keyboard_key_name,
    model_ids as keyboard_model_ids,
    model_named as keyboard_named,
)
from ..mouse import (
    key_name as mouse_key_name,
    model_ids as mouse_model_ids,
    model_named as mouse_named,
)
from ..settings_files import ConfigError
from ..wording import word_list, written
from .shared import BatteryNotices, keyboard_model_named, mouse_model_asked, name_text

if TYPE_CHECKING:
    from . import Daemon

#: Each headset's name in a line, by the key config.json keeps its preset under (spatial's
#: keys, which pipewire_headsets.HEADSET_KEYED holds; the tests hold the two alike).
HEADSET_NAMES = {A50_KEY: A50_NAME, **{model.key: model.short for model in G432_MODELS},
                 **{model.key: model.name for model in A50X_MODELS.values()}}
#: The fields a set names its device by: a keyboard's, a mouse's (`--mouse` sends `device`, as
#: for every mouse command) and a headset's.
DEVICE_FIELDS = ("keyboard", "device", "headset")
#: The refusal of a set that names no device or more than one.
ONE_DEVICE = ("battery_notices sets one device's: name a keyboard, a mouse (device) or a "
              "headset")
#: The refusal of 30/15-urgent for a device whose battery the desktop warns of itself
#: (BatteryNotices.desktop_warns), by the desktop's name and the device's (the owner's
#: decisions of 2026-10-28).
DESKTOP_WARNS = ("{} warns of the {}'s battery itself, so 30/15-urgent is refused for it; "
                 "set 30/15, which posts the same two notices")


class Notices:
    """The battery_notices command, over each device's BatteryNotices."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"battery_notices": self._cmd_battery_notices}

    def every_driven(self) -> list[tuple[str, str, BatteryNotices]]:
        """Each device driven, with a battery or without, (its kind, its name, its notices):
        the keyboards, the leader's first, the mice and the headsets attached."""
        found: list[tuple[str, str, BatteryNotices]] = [
            ("keyboard", keyboard.model_name(), keyboard.battery)
            for keyboard in self.d.keyboards.driven()]
        found += [("mouse", attached.name, self.d.mice.battery.notices_of(attached))
                  for attached in self.d.mice.attached]
        found += [("headset", name, headset) for headset in self.d.headsets
                  if (name := headset.attached_name()) is not None]
        return found

    def driven(self) -> list[tuple[str, str, BatteryNotices]]:
        """Each device driven that has a battery (BatteryNotices.battery_found)."""
        return [device for device in self.every_driven() if device[2].battery_found()]

    def entry(self, kind: str | None, name: str, ids: Sequence[str],
              notices: BatteryNotices | None) -> dict:
        """A device's line of the reply: its kind, its model (ids[0]: its model's key, or the
        key config.json keeps its preset under), its name, its preset, whether it is driven,
        with a battery or without (every_driven), whether the kernel gives it a power supply and
        the desktop that warns of its battery itself, while it is driven with one
        (BatteryNotices.kernel_battery, desktop_warns)."""
        return {"kind": kind, "model": ids[0], "name": name,
                "preset": battery_notices_of(self.d.config.battery_notices, ids),
                "attached": notices is not None or any(
                    ids[0] in driven.battery_ids() for _kind, _name, driven in self.every_driven()),
                "kernel_battery": notices is not None and notices.kernel_battery(),
                "desktop_warns": None if notices is None else notices.desktop_warns()}

    def listed(self) -> list[dict]:
        """Each device driven with a battery (driven), then each other one config.json names
        a preset for, by the key its preset is read under (config.key_under)."""
        driven = self.driven()
        devices = [self.entry(kind, name, notices.battery_ids(), notices)
                   for kind, name, notices in driven]
        seen = {key for _kind, _name, notices in driven for key in notices.battery_ids()}
        kept = self.d.config.battery_notices
        for key in sorted(kept.keys() - seen):
            kind, name = kept_kind(key)
            ids = (keyboard_model_ids(key) if kind == "keyboard" else
                   mouse_model_ids(key) if kind == "mouse" else (key,))
            # A hand edit can keep a model under two of its ids; the one read is its line.
            if key_under(kept, ids) == key:
                devices.append(self.entry(kind, name, (key,), None))
        return devices

    def _cmd_battery_notices(self, request: dict) -> dict:
        """With no `value`, each device's preset (listed); with one, the preset of the device
        one of DEVICE_FIELDS names (aimed), saved. 30/15-urgent is refused for a model driven
        whose battery the desktop warns of itself, either mouse's where two of a model are
        attached (DESKTOP_WARNS)."""
        if "value" not in request:
            return {"ok": True, "devices": self.listed()}
        given = name_text(request["value"], "value")
        preset = given.strip().lower()
        if preset not in BATTERY_PRESETS:
            raise ConfigError(f"battery notices are {word_list(BATTERY_PRESETS)}, not "
                              f"{written(given)}")
        kind, name, ids = self.aimed(request)
        # Two mice of one model may be attached, and the desktop may warn of either's battery.
        notices = min((notices for _kind, _name, notices in self.driven()
                       if notices.battery_ids()[:1] == ids[:1]),
                      key=lambda notices: notices.desktop_warns() is None, default=None)
        warns = None if notices is None else notices.desktop_warns()
        if preset == URGENT and warns is not None:
            raise ConfigError(DESKTOP_WARNS.format(warns, name))
        kept = dict(self.d.config.battery_notices)
        keep_under(kept, ids, preset)
        self.d.save_setting(battery_notices=kept)
        return {"ok": True, **self.entry(kind, name, ids, notices)}

    def aimed(self, request: dict) -> tuple[str, str, tuple[str, ...]]:
        """The device a set names, (its kind, its name, its model's ids): a keyboard or a mouse
        model by its name or any of its ids, attached or not, or a headset by its key."""
        given = [field for field in DEVICE_FIELDS if field in request]
        if len(given) != 1:
            raise ConfigError(ONE_DEVICE)
        if given[0] == "keyboard":
            key = keyboard_model_named(request["keyboard"], "keyboard")
            return "keyboard", keyboard_key_name(key), keyboard_model_ids(key)
        if given[0] == "device":
            key = mouse_model_asked(request["device"], (m.label for m in self.d.mice.attached))
            return "mouse", mouse_key_name(key), mouse_model_ids(key)
        key = name_text(request["headset"], "headset").strip().lower()
        if key not in HEADSET_NAMES:
            raise ConfigError(f"headset: {key!r} names no headset Gearbench drives; "
                              f"they are {word_list(list(HEADSET_NAMES), 'and')}")
        return "headset", HEADSET_NAMES[key], (key,)


def kept_kind(key: str) -> tuple[str | None, str]:
    """The kind and name of the device config.json keeps a preset under `key` for: a keyboard
    or a mouse model by any of its ids, or a headset; (None, the key) for any other, which the
    file keeps for a later Gearbench."""
    if keyboard_named(model := keyboard_model_ids(key)[0]) is not None:
        return "keyboard", keyboard_key_name(model)
    if mouse_named(model := mouse_model_ids(key)[0]) is not None:
        return "mouse", mouse_key_name(model)
    if key in HEADSET_NAMES:
        return "headset", HEADSET_NAMES[key]
    return None, key
