"""The device report's verdict (`gearbench-ctl report`): for a node it read of a model this
build drives, which of the model record's assumptions the reads confirm and which differ. It
reads the node's sections, as `report.gather` gives them or a posted report holds them, and
sends nothing.

Three kinds of assumption are held against the reads: each feature a field of the record
has the daemon use (`assumed_features`), listed in the feature table or not; each zone the
record's board draws a key at, in 0x8081's zone bitmap, or on a board lit by areas each
area's zone among 0x8070's; and the battery: whether the feature it is read over gives
millivolts, which need the record's cell, and where it does, whether a reading the decoder
gave a percent, other than a full cell's 100, lies within the curve it was read on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .battery import CHARGING, FULL, MILLIVOLT_READERS, READERS, UNKNOWN, CellCurves
from .g432 import LISTED_WORDS, HeadsetModel
from .keyboard import KeyboardModel
from .keycontrol import F_CONTROL_LIST, F_FILES, F_KEY_CONTROL
from .lighting import F_BRIGHTNESS, F_PERKEY, F_RGB, ZONES_A_PAGE
from .modifiers import F_MODIFIERS
from .mouse import F_CONFIG_CHANGE, MouseModel
from .mouse_lighting import F_COLOR_LED
from .onboard import F_ONBOARD
from .report import ModelRecord, cell_of, model_record, run_words, runs
from .spy import F_SPY
from .wording import counted

#: One assumption held against the reads: whether they confirm it, and it in words.
Check = tuple[bool, str]


def keyboard_features(model: KeyboardModel) -> list[tuple[int, str]]:
    """The features a keyboard's record has the daemon use, each with what for."""
    found = ([(F_COLOR_LED, "its lighting's zones")] if model.zone_lighting
             else [(F_RGB, "its lighting's effects"), (F_PERKEY, "its per-key colors")])
    if model.keeps_brightness or model.reads_brightness:
        found.append((F_BRIGHTNESS, "its brightness, read at each setup"))
    if model.g_key_defaults:
        found.append((F_ONBOARD, "the onboard mode its G keys type their own keys in"))
    if model.custom_assignments:
        found += [(F_KEY_CONTROL, "its custom assignments"),
                  (F_FILES, "the file its custom assignments are written in")]
        if not model.key_ids:
            found.append((F_CONTROL_LIST, f"its {model.own_key}s' key ids"))
    if model.fn_keys:
        found.append((F_MODIFIERS, "its FN key's reports"))
    if model.probe not in dict(found):
        found.append((model.probe, "finding it"))
    return found


def mouse_features(model: MouseModel) -> list[tuple[int, str]]:
    """The features a mouse's record has the daemon use, each with what for."""
    found = [(F_COLOR_LED, "its lighting's zones")] if model.zone_lighting else []
    if model.host_mode:
        found += [(F_ONBOARD, "the switch to host mode"), (F_SPY, "host mode's button reports")]
    if model.cookie:
        found.append((F_CONFIG_CHANGE, "the cookie that says its setup was kept"))
    return found


def headset_features(model: HeadsetModel) -> list[tuple[int, str]]:
    """The features a HID++ headset's record has the daemon use, in g432.LISTED_WORDS'
    words (`HeadsetModel.used_features`, which `G432.find_features` looks up too)."""
    return [(feature, LISTED_WORDS[feature][0]) for feature in model.used_features]


def assumed_features(model: ModelRecord) -> list[tuple[int, str]]:
    """The features `model`'s record has the daemon use, by its family; none for a family
    whose node has no HID++ collection, the A50 X's."""
    if isinstance(model, KeyboardModel):
        return keyboard_features(model)
    if isinstance(model, MouseModel):
        return mouse_features(model)
    if isinstance(model, HeadsetModel):
        return headset_features(model)
    return []


def feature_check(feature: int, purpose: str, listed: frozenset[int]) -> Check:
    """Whether the table lists a feature the record has the daemon use."""
    found = feature in listed
    return found, f"0x{feature:04x}, for {purpose}, is {'' if found else 'not '}listed"


def key_zones_check(model: KeyboardModel, keys: Mapping | None) -> Check | None:
    """Whether 0x8081's zone bitmap (report.per_key) has each zone the board draws a key at;
    None where the bitmap was not read, or a page of it that holds one of those zones was
    refused."""
    if keys is None:
        return None
    refused = {page["page"] for page in keys["pages"] if "refused" in page}
    if any(zone // ZONES_A_PAGE in refused for zone in model.board.zones):
        return None
    lit = {zone for first, last in keys["zones"] for zone in range(first, last + 1)}
    missing = sorted(model.board.zones - lit)
    if missing:
        zones = "zone" if len(missing) == 1 else "zones"
        return False, (f"0x8081's zone bitmap lacks the board's key {zones} "
                       f"{run_words(runs(missing))}")
    return True, (f"0x8081's zone bitmap has each of the board's "
                  f"{counted(len(model.board.zones), 'key zone')}")


def area_zones_check(model: KeyboardModel, zones: Mapping | None) -> Check | None:
    """Whether 0x8070 (report.zones) has the zone of each of the board's areas
    (KeyboardModel.area_zones); None where its zones were not read."""
    if zones is None:
        return None
    missing = sorted(set(model.area_zones) - {zone["index"] for zone in zones["zones"]})
    if missing:
        which = ("the zone of one of the board's areas" if len(missing) == 1
                 else "the zones of the board's areas")
        return False, f"0x8070 lacks {which}, {run_words(runs(missing))}"
    return True, f"0x8070 has the zone of each of the board's {counted(len(model.area_zones), 'area')}"


def battery_feature(listed: frozenset[int], reads: Sequence[Mapping]) -> int | None:
    """The battery feature report.battery_reading read or tried: the first of battery.READERS
    the table lists whose root lookup in `reads` did not answer index 0, None for none. A
    lookup that failed answered no index, so the feature it was for is kept."""
    absent = {int(read["params"][:4], 16) for read in reads
              if read["feature"] == "0x0000" and read["function"] == 0
              and read.get("reply", "")[:2] == "00"}
    return next((feature for feature, _function, _decode in READERS
                 if feature in listed and feature not in absent), None)


def battery_check(cell: CellCurves | None, feature: int | None,
                  reading: Mapping | None) -> Check | None:
    """Whether the battery feature the report read (battery_feature) fits the record's
    cell: one that gives millivolts needs a cell, and a reading the decoder gave a percent,
    other than a full cell's 100, lies within the curve it was read on; one that gives its
    own charge needs none. None for no battery feature and no cell."""
    if feature is None:
        return None if cell is None else (
            False, "no battery feature is found, where the record has a cell for one")
    named = f"0x{feature:04x}"
    if feature not in MILLIVOLT_READERS:
        return cell is None, (f"its battery is read over {named} as a charge of its own"
                              + ("" if cell is None else ", so the record's cell is not used"))
    if cell is None:
        return False, (f"its battery is read over {named} in millivolts, and the record has "
                       "no cell to read them against")
    # The curve the decoder read its percent off (CellCurves.percent): none where it gave
    # none, and none for a full cell's 100, which no curve gives; an empty one where a posted
    # report's reading was decoded against another build's cell.
    if (reading is None or "percent" not in reading or reading["state"] == FULL
            or not (curve := cell.curve(reading["state"] == CHARGING))):
        return True, f"its battery is read over {named} in millivolts, which the record's cell reads"
    low, high = curve[0][0], curve[-1][0]
    within = low <= reading["millivolts"] <= high
    state = "in an unknown charge state" if reading["state"] == UNKNOWN else reading["state"]
    return within, (f"its battery's {reading['volts']} V, {state}, is "
                    f"{'within' if within else 'outside'} the record's cell's curve for it, "
                    f"{low / 1000:g} to {high / 1000:g} V")


def verdict(node: Mapping) -> dict | None:
    """The verdict on a node's part of the report: its model record's name, and each
    assumption the reads confirm and each they differ from, in words. None for a model this
    build does not drive, and where nothing read speaks to what its record assumes, as for a
    node not read. A section not read checks nothing: the features and the battery need the
    feature table."""
    product = int(node["product"], 16)
    model = model_record(product)
    if model is None:
        return None
    found: list[Check | None] = []
    if "features" in node:
        listed = frozenset(int(row["id"], 16) for row in node["features"])
        found += [feature_check(feature, purpose, listed)
                  for feature, purpose in assumed_features(model)]
        found.append(battery_check(cell_of(product),
                                   battery_feature(listed, node.get("reads", ())),
                                   node.get("battery")))
    if isinstance(model, KeyboardModel):
        found.append(area_zones_check(model, node.get("zones")) if model.zone_lighting
                     else key_zones_check(model, node.get("per_key")))
    checks = [check for check in found if check is not None]
    if not checks:
        return None
    return {"record": model.name, "confirmed": [words for held, words in checks if held],
            "differs": [words for held, words in checks if not held]}


def with_verdict(node: dict) -> dict:
    """`node` with its `verdict`, where it has one."""
    found = verdict(node)
    return node if found is None else {**node, "verdict": found}
