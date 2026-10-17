"""Batteries: one reader for every device, the words and the icon, the low-battery levels
and the G915's curves.

The devices use three battery features, so the reader asks by feature: the G502 X PLUS
answers a percentage over 0x1004 UnifiedBattery, the G305 a coarse level over 0x1000
BatteryStatus, and the G915 a voltage and a status byte over 0x1001 BatteryVoltage. The
Astro A50's base station is not HID++; `astro` puts its percentage and charging bit in the
same shape.
"""

from __future__ import annotations

import logging
from bisect import bisect_left

from .hidpp import DeviceGone, Feature, Hidpp, HidppFailure, not_there

log = logging.getLogger("gearbench")

#: One vocabulary for every device, so two devices in one state read one word.
ON_BATTERY = "on battery"
CHARGING = "charging"
FULL = "fully charged"
UNKNOWN = "unknown"
FAULT = "battery fault"
#: On external power and not charging: the kernel's POWER_SUPPLY_STATUS_NOT_CHARGING.
NOT_CHARGING = "not charging"
#: A 0x1001 device charging above NEARLY_FULL_MV, where the G915's curve stops meaning much.
NEARLY_FULL = "charging, nearly full"

#: The status byte of 0x1004 UnifiedBattery, as the kernel's
#: hidpp20_unifiedbattery_map_status reads it: 0 discharging, 1 charging, 2 charging
#: slowly, 3 complete, 4 a charging error. Slow charging is charging: the percentage says
#: how far along. Solaar reads 0x1004 with 0x1000's table, where 4 is still charging; the
#: kernel and OpenLogi's 0x1004 page say it is an error.
UNIFIED_STATES = {
    0: ON_BATTERY,
    1: CHARGING,
    2: CHARGING,
    3: FULL,
    4: FAULT,
}

#: The status byte of 0x1000 BatteryStatus, as the kernel's
#: hidpp20_batterylevel_map_status_capacity reads it: 0 discharging, 1
#: recharging, 2 in its final stage, 3 complete, 4 recharging below the
#: optimal speed; 5 an invalid battery type, 6 a thermal error and 7 another
#: charging error, which the kernel calls not charging.
STATUS_STATES = {
    0: ON_BATTERY,
    1: CHARGING,
    2: CHARGING,
    3: FULL,
    4: CHARGING,
    5: FAULT,
    6: FAULT,
    7: FAULT,
}

#: The two low-battery levels in whole percent, G HUB's LOW and CRITICAL, which the
#: notices, the tray and the window's Battery row share.
LOW_PERCENT, CRITICAL_PERCENT = 30, 15
#: The low-battery notices, daemon.session's for the keyboard and the headset's, in percent:
#: one below each, the second replacing the first, after two readings in a row below it.
#: External power arms them again, and so does a reading at daemon.shared.BATTERY_REARM or
#: more. The tray's low-battery icon goes by the lower one.
BATTERY_WARNINGS = (LOW_PERCENT, CRITICAL_PERCENT)

#: The battery features, in the order tried: 0x1004 is the most direct, and 0x1001, last,
#: needs a curve for the cell.
F_UNIFIED = 0x1004
F_STATUS = 0x1000
F_VOLTAGE = 0x1001


def amount_words(percent: float, approximate: bool) -> str:
    """A percent as every reading shows it: whole, with "about" for a coarse one."""
    return f"{'about ' if approximate else ''}{percent:.0f}%"


def reading_words(reading: dict) -> str:
    """A reading's percent and state in words, "about" for a coarse percent; empty for
    neither. With no percent (nearly full on the cable, or a cell with no curve) the state
    alone, since "?%" reads as a fault."""
    value = reading.get("percent")
    amount = (f"{amount_words(value, bool(reading.get('approximate')))}  "
              if isinstance(value, int | float) else "")
    return f"{amount}{reading.get('state', '')}".strip()


def icon_name(reading: dict | None, cabled: bool = False) -> str:
    """A reading's battery icon, the one the window and the tray show, a name the Adwaita
    and Yaru themes both have. The level is the ten nearest the whole percent
    amount_words shows, halfway going up. A charging cell with no percent (nearly full on
    the cable) is drawn at 90, and with `cabled`, a keyboard on its cable with no reading,
    as the adapter; only the tray passes `cabled`, so the window draws that case as a
    missing battery."""
    reading = reading or {}
    state, percent = reading.get("state"), reading.get("percent")
    charging = state in (CHARGING, NEARLY_FULL)
    if state == FAULT:
        return "battery-caution-symbolic"
    if state == FULL:
        return "battery-level-100-charged-symbolic"
    if not isinstance(percent, int | float):
        if charging:
            return "battery-level-90-charging-symbolic"
        return "ac-adapter-symbolic" if cabled else "battery-missing-symbolic"
    # The themes stop at 100, and a 0x1004 or 0x1000 percent byte is passed on as it comes.
    level = min(100, (round(percent) + 5) // 10 * 10)
    if charging:
        # Adwaita has no 100 % charging icon; Yaru has both.
        return ("battery-level-100-charged-symbolic" if level == 100
                else f"battery-level-{level}-charging-symbolic")
    plugged = "-plugged-in" if state == NOT_CHARGING else ""
    return f"battery-level-{level}{plugged}-symbolic"


def read(dev: Hidpp, product: int | None = None, name: str = "device") -> dict | None:
    """What this device can say about its battery, in one shape, or None for no battery
    feature or a refused read; silence and DeviceGone are raised (_ask). `name` is the
    device's, for the journal line of a refusal.

    Every reading has `state` and `approximate`: whether `percent` is a measurement or the
    nearest step the device knows. `percent` is left out where it would be a guess: a
    0x1001 device with no curve, or one charging above NEARLY_FULL_MV. Only 0x1004 and
    0x1000 readings carry `charging`.
    """
    for feature_id, function, decode in (
            (F_UNIFIED, 1, unified),
            (F_STATUS, 0, _status),
            (F_VOLTAGE, 0, lambda reply: _voltage(reply, product))):
        feature = dev.feature(feature_id)
        if feature is not None:
            reply = _ask(dev, feature, function, name)
            return None if reply is None else decode(reply)
    return None


def unified(data: bytes) -> dict:
    """A 0x1004 status, [percent, level, status, external power], in the one shape. It is
    both getStatus's reply (function 1) and the report the device sends when it changes
    (function 0, the kernel's EVENT_UNIFIED_BATTERY_STATUS_EVENT)."""
    state = UNIFIED_STATES.get(data[2], UNKNOWN)
    return {
        "percent": float(data[0]),
        "state": state,
        "charging": state == CHARGING,
        "approximate": False,
    }


def astro(percent: int, charging: bool, docked: bool) -> dict:
    """The A50 base station's battery, `A50.battery()`'s (percent, charging),
    in the one shape. `docked` is bit 0 of 54: a full headset on the base has
    the charging bit clear."""
    if percent >= 100 and (charging or docked):
        state = FULL
    else:
        state = CHARGING if charging else ON_BATTERY
    return {"percent": float(percent), "state": state, "charging": charging,
            "approximate": False}


def _status(reply: bytes) -> dict:
    """0x1000: a level, the next level down, and a status code. The level is one of a
    handful the firmware knows, so it sits at 90 a long time and then steps; `next_level`
    shows how coarse it is."""
    state = STATUS_STATES.get(reply[2], UNKNOWN)
    return {
        "percent": float(reply[0]),
        "next_level": float(reply[1]),
        "state": state,
        "charging": state == CHARGING,
        "approximate": True,
    }


def _voltage(reply: bytes, product: int | None) -> dict:
    """0x1001: millivolts, which only mean something against a curve. With no curve for
    the product, or above NEARLY_FULL_MV while charging, the percentage is left out."""
    millivolts = (reply[0] << 8) | reply[1]
    flags = reply[2]
    external = bool(flags & EXTERNAL_POWER)
    charge = flags & CHARGE_STATE
    full = external and charge == CHARGE_FULL
    if not external:
        state = ON_BATTERY
    elif charge == CHARGE_CHARGING:
        state = NEARLY_FULL if millivolts > NEARLY_FULL_MV else CHARGING
    else:
        state = CHARGE_STATES.get(charge, UNKNOWN)
    reading = {
        "millivolts": millivolts,
        "volts": round(millivolts / 1000, 2),
        "external_power": external,
        "full": full,
        "critical": bool(flags & CRITICAL),
        "state": state,
        "approximate": False,
    }
    if product in CURVES and state != NEARLY_FULL:
        reading["percent"] = 100.0 if full else percent(millivolts, external)
    return reading


def _ask(dev: Hidpp, feature: Feature, function: int, name: str) -> bytes | None:
    """One read, or None when the device refuses: a battery it will not talk about does
    not make the device unusable. Silence (a timeout, or a receiver that cannot reach the
    device) and DeviceGone are raised: they say whether the device is there, and the
    caller acts on that."""
    try:
        return dev.request(feature.index, function)
    except DeviceGone:
        raise
    except HidppFailure as exc:
        if not_there(exc):
            raise
        log.debug("the %s refused its battery read: %s", name, exc)
        return None

#: Bit 7 of the status byte: the keyboard is on external power.
EXTERNAL_POWER = 0x80
#: The low three bits, meaningful only while bit 7 is set: 0 charging, 1 full, 2 not
#: charging, anything else unknown (the kernel's hidpp20_battery_map_status_voltage).
#: Full reads 100, above the charging curve's 91, as G HUB reports the G915
#: (`fullyCharged: true`, `percentage: 100`). Bits 3 and 4, fast and trickle charge,
#: change none of this.
CHARGE_STATE = 0x07
CHARGE_CHARGING = 0x00
CHARGE_FULL = 0x01
CHARGE_STATES = {CHARGE_FULL: FULL, 0x02: NOT_CHARGING}
#: Bit 5: the cell is critically low.
CRITICAL = 0x20

#: Charging above this, the keyboard reads "charging, nearly full" and no percentage: the
#: charging curve ends at 91 % at 4.20 V, so the number stalled at 80 to 91 % (measured
#: 2026-09-22). G HUB's battery.xml also stops trusting the curve here, counting 1 % per
#: 220 s once the cell is stable above 4100 mV (charging_time_slots); this does not.
NEARLY_FULL_MV = 4100

#: Product ids the curves below are for: the G915 is 0xC33E cabled and 0xC541 through its
#: receiver, one cell. Another 0x1001 device reports volts and no percentage, since another
#: cell's curve would give a wrong number.
CURVES = frozenset({0xC33E, 0xC541})

#: Running on battery: (millivolts, percent remaining), rising. The G915's curves are
#: Logitech's own, from G HUB's decrypted device depot (`<powermodel vid="0x46d"
#: pid="0xc33e">`, "Harpy Wireless"), generated from that file, not edited by hand. A
#: lithium cell's curve is steep at both ends and flat in the middle: 3.60 V is 5 % left,
#: where a straight line from 3.5 to 4.2 V would say 14. The depot gives discharge as
#: (minutes, volts, mWh consumed), converted to percent of the 5510 mWh the curve spans.
#: In the file's order, a point is dropped when its voltage or percentage equals the last
#: point kept.
DISCHARGE_CURVE: tuple[tuple[int, float], ...] = (
    (3400, 0), (3405, 0.5), (3410, 1), (3415, 1.5), (3420, 2), (3425, 2.5),
    (3430, 3), (3435, 3.5), (3445, 3.7), (3455, 3.8), (3470, 3.9), (3480,
    4), (3490, 4.1), (3500, 4.2), (3510, 4.3), (3520, 4.4), (3530, 4.5),
    (3540, 4.6), (3550, 4.7), (3560, 4.8), (3570, 4.9), (3580, 5), (3590,
    5.1), (3600, 5.2), (3610, 5.3), (3620, 5.4), (3630, 5.5), (3640, 5.6),
    (3645, 5.7), (3654, 7), (3657, 7.1), (3661, 7.2), (3664, 7.3), (3668,
    7.6), (3672, 8.7), (3675, 9.8), (3679, 11), (3682, 12.1), (3686, 13.3),
    (3690, 14.4), (3693, 15.6), (3697, 16.7), (3700, 17.8), (3704, 19),
    (3708, 20.1), (3711, 21.3), (3715, 22.4), (3718, 23.6), (3722, 24.7),
    (3726, 25.9), (3729, 27), (3733, 28.1), (3736, 29.3), (3740, 30.4),
    (3744, 31.6), (3747, 32.7), (3751, 33.9), (3754, 35), (3758, 36.1),
    (3762, 37.3), (3765, 38.4), (3769, 39.6), (3772, 40.7), (3776, 41.9),
    (3780, 43), (3783, 44.1), (3787, 45.3), (3790, 46.4), (3794, 47.6),
    (3798, 48.7), (3801, 49.9), (3805, 51), (3808, 52.1), (3812, 53.3),
    (3816, 54.4), (3819, 55.6), (3823, 56.7), (3826, 57.9), (3830, 58),
    (3836, 60), (3842, 61), (3848, 63), (3854, 63.8), (3860, 64.6), (3866,
    65.4), (3872, 66.2), (3878, 67), (3884, 67.8), (3890, 68.6), (3896,
    69.4), (3902, 70.2), (3908, 71), (3914, 71.9), (3920, 72.7), (3926,
    73.5), (3932, 74.3), (3938, 75.1), (3944, 75.9), (3950, 76.7), (3956,
    77.5), (3962, 78.3), (3968, 79.1), (3974, 80), (3980, 80.8), (3986,
    81.6), (3992, 82.4), (3998, 83.2), (4004, 84), (4010, 84.8), (4016,
    85.6), (4022, 86.4), (4028, 87.2), (4034, 88), (4040, 88.9), (4046,
    89.7), (4052, 90.5), (4058, 91.3), (4064, 92.1), (4070, 92.9), (4076,
    93.7), (4082, 94.5), (4088, 95.3), (4094, 96.1), (4100, 97), (4106,
    97.8), (4112, 98.6), (4154, 99), (4200, 100)
)

#: On external power, where the reading sits high for the same charge: the depot's
#: (volts, percent) points, dropped as above.
CHARGING_CURVE: tuple[tuple[int, float], ...] = (
    (3400, 0), (3419, 1), (3512, 2), (3600, 3), (3677, 4), (3762, 5), (3800,
    7), (3801, 9), (3806, 10), (3813, 11), (3817, 12), (3823, 13), (3828,
    14), (3836, 15), (3839, 16), (3848, 17), (3850, 18), (3857, 19), (3862,
    20), (3867, 21), (3872, 22), (3874, 23), (3876, 24), (3880, 26), (3887,
    29), (3891, 31), (3894, 32), (3897, 33), (3901, 35), (3902, 36), (3905,
    37), (3907, 38), (3911, 40), (3914, 41), (3916, 42), (3918, 43), (3921,
    44), (3922, 45), (3925, 46), (3927, 47), (3931, 50), (3935, 51), (3939,
    52), (3946, 53), (3952, 54), (3957, 55), (3959, 56), (3964, 57), (3972,
    58), (3974, 59), (3981, 60), (3984, 61), (3989, 62), (3999, 64), (4005,
    65), (4009, 66), (4014, 67), (4019, 68), (4030, 70), (4041, 72), (4043,
    73), (4048, 74), (4060, 76), (4070, 77), (4080, 78), (4090, 79), (4100,
    80), (4110, 81), (4120, 82), (4130, 83), (4140, 84), (4150, 86), (4160,
    87), (4170, 88), (4180, 89), (4190, 90), (4200, 91)
)


def percent(millivolts: int, external_power: bool) -> float:
    """Charge remaining, 0 to 100, linear between the curve's points; off either end,
    that end's value."""
    curve = CHARGING_CURVE if external_power else DISCHARGE_CURVE
    at = bisect_left(curve, millivolts, key=lambda point: point[0])
    if at == 0:
        return curve[0][1]
    if at == len(curve):
        return curve[-1][1]
    (low_mv, low_pct), (high_mv, high_pct) = curve[at - 1], curve[at]
    span = (millivolts - low_mv) / (high_mv - low_mv)
    return round(low_pct + span * (high_pct - low_pct), 1)
