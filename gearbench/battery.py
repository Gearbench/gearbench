"""Batteries: one reader for every device, the words and the icon, the low-battery levels
and the voltage curves of each cell that has them.

The devices use three battery features, so the reader asks by feature: the G502 X PLUS
answers a percentage over 0x1004 UnifiedBattery, as the G515 LIGHTSPEED TKL does through its
receiver, the G305 a coarse level over 0x1000 BatteryStatus, and the G915 a voltage and a
status byte over 0x1001 BatteryVoltage, as each model given a cell does. The Astro A50's
base station is not HID++; `astro` puts its percentage and charging bit in the same shape.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass

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
#: A 0x1001 device charging above its cell's `nearly_full_mv` (NEARLY_FULL_MV without a
#: curve), where the charging curve stops meaning much.
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
#: The three in that order, with the function `read` asks each for.
FEATURES = ((F_UNIFIED, 1), (F_STATUS, 0), (F_VOLTAGE, 0))


def has_battery(dev: Hidpp) -> bool:
    """Whether `dev`'s feature table lists one of the battery features `read` asks."""
    return any(dev.feature(feature_id) is not None for feature_id, _function in FEATURES)


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


def read(dev: Hidpp, cell: CellCurves | None, name: str = "device") -> dict | None:
    """What this device can say about its battery, in one shape, or None for no battery
    feature or a refused read; silence and DeviceGone are raised (_ask). `name` is the
    device's, for the journal line of a refusal.

    Every reading has `state` and `approximate`: whether `percent` is a measurement or the
    nearest step the device knows. `percent` is left out where it would be a guess: a
    0x1001 device with no `cell`, or one charging above its cell's `nearly_full_mv`. Only
    0x1004 and 0x1000 readings carry `charging`.
    """
    decoders: dict[int, Callable[[bytes], dict]] = {
        F_UNIFIED: unified, F_STATUS: _status, F_VOLTAGE: lambda reply: _voltage(reply, cell)}
    for feature_id, function in FEATURES:
        feature = dev.feature(feature_id)
        if feature is not None:
            reply = _ask(dev, feature, function, name)
            return None if reply is None else decoders[feature_id](reply)
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


def _voltage(reply: bytes, cell: CellCurves | None) -> dict:
    """0x1001: millivolts, which only mean something against the device's cell. With no
    cell, or above the cell's `nearly_full_mv` while charging,
    the percentage is left out."""
    millivolts = (reply[0] << 8) | reply[1]
    flags = reply[2]
    external = bool(flags & EXTERNAL_POWER)
    charge = flags & CHARGE_STATE
    full = external and charge == CHARGE_FULL
    if not external:
        state = ON_BATTERY
    elif charge == CHARGE_CHARGING:
        nearly_full_mv = NEARLY_FULL_MV if cell is None else cell.nearly_full_mv
        state = NEARLY_FULL if millivolts > nearly_full_mv else CHARGING
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
    if cell is not None and state != NEARLY_FULL:
        reading["percent"] = 100.0 if full else cell.percent(millivolts, external)
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

#: Charging above this, the G915 reads "charging, nearly full" and no percentage: its
#: charging curve ends at 91 % at 4.20 V, so the number stalled at 80 to 91 % (measured
#: 2026-09-22). G HUB's battery.xml also stops trusting the curve here, counting 1 % per
#: 220 s once the cell is stable above 4100 mV (charging_time_slots); this does not. A
#: 0x1001 product with no curve takes it too.
NEARLY_FULL_MV = 4100

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


@dataclass(frozen=True)
class CellCurves:
    """One cell's two curves, each (millivolts, percent) with both rising, and the charging
    reading above which it gives no percentage."""

    #: Running on battery: charge remaining.
    discharge: tuple[tuple[int, float], ...]
    #: On external power, where the reading sits high for the same charge.
    charging: tuple[tuple[int, float], ...]
    #: Charging above this, the device reads NEARLY_FULL and no percentage.
    nearly_full_mv: int

    def percent(self, millivolts: int, external_power: bool) -> float:
        """Charge remaining, 0 to 100, linear between the curve's points; off either end,
        that end's value."""
        curve = self.charging if external_power else self.discharge
        at = bisect_left(curve, millivolts, key=lambda point: point[0])
        if at == 0:
            return curve[0][1]
        if at == len(curve):
            return curve[-1][1]
        (low_mv, low_pct), (high_mv, high_pct) = curve[at - 1], curve[at]
        span = (millivolts - low_mv) / (high_mv - low_mv)
        return round(low_pct + span * (high_pct - low_pct), 1)


G915_CELL = CellCurves(DISCHARGE_CURVE, CHARGING_CURVE, NEARLY_FULL_MV)

#: The G502 LIGHTSPEED's cell, from its G HUB device package `g502_wireless` (build 869589,
#: `battery.xml`, `<powermodel vid="0x46d" pid="0x407f">`), by the G915's recipe above:
#: discharge over the 902.34 mWh the curve spans. The PRO WIRELESS's package
#: (`pro_wireless_mouse_depot`, `pid="0x4079"`) holds the same two curves, point for point.
G502_LIGHTSPEED_DISCHARGE: tuple[tuple[int, float], ...] = (
    (3479, 0), (3486, 0.1), (3493, 0.3), (3500, 0.4), (3506, 0.5), (3513, 0.6), (3519, 0.8),
    (3525, 0.9), (3531, 1), (3537, 1.2), (3543, 1.3), (3548, 1.4), (3554, 1.6), (3559, 1.7),
    (3564, 1.8), (3569, 2), (3574, 2.1), (3579, 2.2), (3583, 2.4), (3588, 2.5), (3592, 2.6),
    (3596, 2.8), (3600, 2.9), (3604, 3), (3608, 3.2), (3612, 3.3), (3615, 3.4), (3619, 3.6),
    (3622, 3.7), (3625, 3.8), (3628, 4), (3631, 4.1), (3633, 4.2), (3635, 4.4), (3637, 4.5),
    (3639, 4.6), (3641, 4.8), (3643, 4.9), (3644, 5), (3646, 5.2), (3647, 5.3), (3648, 5.4),
    (3649, 5.6), (3650, 5.7), (3651, 5.8), (3652, 6), (3653, 6.1), (3654, 6.4), (3655, 6.5),
    (3656, 6.6), (3657, 6.9), (3658, 7.2), (3659, 7.5), (3660, 7.6), (3661, 7.9), (3662, 8.3),
    (3663, 8.5), (3664, 8.7), (3665, 8.9), (3666, 9.2), (3667, 9.5), (3668, 9.6), (3669, 9.9),
    (3670, 10), (3671, 10.3), (3673, 10.6), (3674, 10.7), (3675, 10.8), (3676, 11), (3677, 11.2),
    (3678, 11.4), (3679, 11.5), (3680, 11.7), (3681, 11.8), (3682, 11.9), (3683, 12.2),
    (3684, 12.3), (3685, 12.5), (3686, 12.7), (3687, 12.9), (3688, 13.2), (3689, 13.3),
    (3690, 13.6), (3691, 13.7), (3692, 14), (3693, 14.2), (3694, 14.4), (3695, 14.6), (3696, 14.8),
    (3697, 15.2), (3698, 15.3), (3699, 15.6), (3700, 15.7), (3701, 16), (3702, 16.3), (3703, 16.4),
    (3704, 16.7), (3705, 17), (3706, 17.2), (3707, 17.5), (3708, 17.6), (3709, 18.1), (3710, 18.2),
    (3711, 18.5), (3712, 18.7), (3713, 19), (3714, 19.3), (3715, 19.7), (3716, 19.8), (3717, 20.2),
    (3718, 20.5), (3719, 20.8), (3720, 21.2), (3721, 21.5), (3722, 21.9), (3723, 22), (3724, 22.4),
    (3725, 22.8), (3726, 23.1), (3727, 23.3), (3728, 23.7), (3729, 23.9), (3730, 24.2),
    (3731, 24.5), (3732, 24.8), (3733, 25.1), (3734, 25.3), (3735, 25.6), (3736, 26), (3737, 26.2),
    (3738, 26.4), (3739, 26.8), (3740, 27), (3741, 27.3), (3742, 27.5), (3743, 27.8), (3744, 28.2),
    (3745, 28.5), (3746, 28.6), (3747, 28.9), (3748, 29.2), (3749, 29.6), (3750, 29.9),
    (3751, 30.3), (3752, 30.7), (3753, 31), (3754, 31.4), (3755, 31.7), (3756, 32), (3757, 32.4),
    (3758, 32.8), (3759, 33.2), (3760, 33.5), (3761, 33.9), (3762, 34.3), (3763, 34.7),
    (3764, 35.1), (3765, 35.5), (3766, 36), (3767, 36.2), (3768, 36.7), (3769, 36.9), (3770, 37.4),
    (3771, 37.8), (3772, 38.1), (3773, 38.5), (3774, 38.9), (3775, 39.2), (3776, 39.4),
    (3777, 39.9), (3778, 40.3), (3779, 40.6), (3780, 41), (3781, 41.4), (3782, 41.7), (3783, 41.9),
    (3784, 42.4), (3785, 42.5), (3786, 42.8), (3787, 43.2), (3788, 43.5), (3789, 43.9),
    (3790, 44.2), (3791, 44.5), (3792, 44.7), (3793, 45.2), (3794, 45.4), (3795, 45.6), (3796, 46),
    (3797, 46.1), (3798, 46.6), (3799, 46.8), (3800, 47.1), (3801, 47.4), (3802, 47.5),
    (3803, 47.8), (3804, 48.1), (3805, 48.4), (3806, 48.7), (3807, 48.9), (3808, 49.2),
    (3809, 49.5), (3810, 49.6), (3811, 50.1), (3812, 50.2), (3813, 50.5), (3814, 50.8), (3815, 51),
    (3816, 51.2), (3817, 51.5), (3818, 51.7), (3819, 52), (3820, 52.2), (3821, 52.5), (3822, 52.6),
    (3823, 52.9), (3824, 53.2), (3825, 53.3), (3826, 53.6), (3827, 53.7), (3828, 54), (3829, 54.3),
    (3830, 54.4), (3831, 54.7), (3832, 54.9), (3833, 55.1), (3834, 55.3), (3835, 55.6),
    (3836, 55.7), (3837, 56), (3838, 56.1), (3839, 56.4), (3840, 56.5), (3841, 56.7), (3842, 57),
    (3843, 57.1), (3844, 57.3), (3845, 57.4), (3846, 57.7), (3847, 57.8), (3848, 58.1),
    (3849, 58.2), (3850, 58.4), (3851, 58.7), (3852, 58.8), (3853, 59), (3854, 59.1), (3855, 59.4),
    (3857, 59.7), (3858, 59.8), (3859, 60.1), (3860, 60.2), (3861, 60.4), (3862, 60.5),
    (3863, 60.7), (3864, 60.8), (3865, 61.1), (3866, 61.2), (3867, 61.4), (3868, 61.7),
    (3870, 61.9), (3871, 62.1), (3872, 62.2), (3873, 62.4), (3874, 62.7), (3875, 62.8),
    (3877, 63.1), (3879, 63.4), (3880, 63.5), (3881, 63.7), (3882, 63.8), (3883, 63.9),
    (3884, 64.1), (3885, 64.2), (3886, 64.4), (3887, 64.7), (3889, 64.8), (3890, 65.1),
    (3891, 65.2), (3892, 65.4), (3893, 65.5), (3894, 65.7), (3895, 65.8), (3896, 66), (3897, 66.1),
    (3898, 66.2), (3899, 66.5), (3901, 66.8), (3902, 67), (3903, 67.1), (3904, 67.2), (3905, 67.4),
    (3906, 67.5), (3907, 67.7), (3908, 67.8), (3909, 68), (3910, 68.1), (3911, 68.3), (3912, 68.4),
    (3913, 68.5), (3914, 68.7), (3915, 68.8), (3916, 69), (3917, 69.1), (3918, 69.3), (3919, 69.5),
    (3920, 69.7), (3921, 69.8), (3922, 70), (3923, 70.1), (3924, 70.3), (3925, 70.4), (3926, 70.6),
    (3927, 70.7), (3928, 70.8), (3929, 71), (3930, 71.1), (3931, 71.4), (3933, 71.7), (3934, 71.9),
    (3935, 72), (3936, 72.1), (3937, 72.3), (3938, 72.4), (3939, 72.6), (3940, 72.7), (3941, 72.9),
    (3942, 73), (3943, 73.2), (3944, 73.3), (3945, 73.5), (3946, 73.6), (3947, 73.7), (3948, 73.9),
    (3949, 74), (3950, 74.2), (3951, 74.3), (3952, 74.5), (3953, 74.6), (3954, 74.8), (3955, 74.9),
    (3956, 75.1), (3957, 75.2), (3958, 75.3), (3959, 75.5), (3960, 75.6), (3961, 75.9),
    (3962, 76.1), (3964, 76.2), (3965, 76.4), (3966, 76.5), (3967, 76.8), (3969, 77), (3970, 77.1),
    (3971, 77.3), (3972, 77.4), (3973, 77.5), (3974, 77.7), (3975, 77.8), (3976, 78), (3977, 78.1),
    (3978, 78.3), (3979, 78.4), (3980, 78.6), (3981, 78.7), (3982, 78.9), (3983, 79), (3984, 79.2),
    (3985, 79.3), (3986, 79.5), (3987, 79.6), (3988, 79.7), (3989, 79.9), (3991, 80.2),
    (3993, 80.5), (3995, 80.6), (3996, 80.8), (3997, 80.9), (3998, 81.1), (3999, 81.2),
    (4000, 81.4), (4001, 81.5), (4002, 81.7), (4003, 81.8), (4004, 82), (4005, 82.1), (4007, 82.3),
    (4008, 82.4), (4009, 82.5), (4010, 82.7), (4011, 82.8), (4012, 83), (4013, 83.1), (4015, 83.3),
    (4016, 83.4), (4017, 83.6), (4018, 83.7), (4019, 83.9), (4021, 84.2), (4022, 84.3),
    (4024, 84.5), (4025, 84.6), (4026, 84.8), (4027, 84.9), (4028, 85.1), (4030, 85.2),
    (4031, 85.4), (4032, 85.5), (4033, 85.7), (4034, 85.8), (4036, 86.1), (4038, 86.3),
    (4039, 86.4), (4040, 86.6), (4042, 86.7), (4043, 86.9), (4044, 87), (4045, 87.2), (4046, 87.3),
    (4048, 87.4), (4049, 87.6), (4050, 87.7), (4051, 87.9), (4052, 88), (4054, 88.2), (4055, 88.3),
    (4056, 88.5), (4057, 88.6), (4059, 88.8), (4060, 88.9), (4061, 89.1), (4062, 89.2),
    (4064, 89.4), (4065, 89.5), (4066, 89.7), (4067, 89.8), (4069, 90), (4070, 90.1), (4071, 90.3),
    (4073, 90.4), (4074, 90.6), (4075, 90.7), (4076, 90.9), (4077, 91), (4079, 91.2), (4080, 91.3),
    (4081, 91.5), (4082, 91.6), (4084, 91.8), (4086, 91.9), (4087, 92.1), (4088, 92.2),
    (4090, 92.4), (4091, 92.5), (4092, 92.7), (4094, 92.8), (4095, 93), (4096, 93.2), (4098, 93.3),
    (4099, 93.5), (4100, 93.6), (4102, 93.8), (4103, 93.9), (4105, 94.1), (4106, 94.2),
    (4108, 94.4), (4109, 94.5), (4110, 94.7), (4112, 94.8), (4113, 95), (4115, 95.1), (4116, 95.3),
    (4117, 95.4), (4119, 95.6), (4121, 95.7), (4122, 95.9), (4124, 96), (4125, 96.2), (4127, 96.3),
    (4128, 96.5), (4130, 96.6), (4131, 96.8), (4133, 96.9), (4135, 97.1), (4136, 97.2),
    (4138, 97.4), (4139, 97.6), (4141, 97.7), (4143, 97.9), (4144, 98), (4146, 98.2), (4148, 98.3),
    (4150, 98.5), (4152, 98.6), (4153, 98.8), (4156, 98.9), (4157, 99.1), (4160, 99.2),
    (4163, 99.4), (4166, 99.5), (4172, 99.7), (4186, 99.9)
)

G502_LIGHTSPEED_CHARGING: tuple[tuple[int, float], ...] = (
    (3271, 2), (3668, 3), (3786, 5), (3859, 7), (3908, 8), (3929, 10), (3942, 12), (3954, 13),
    (3965, 15), (3976, 17), (3988, 18), (3998, 20), (4007, 22), (4015, 23), (4023, 25), (4029, 27),
    (4036, 28), (4042, 30), (4048, 32), (4054, 33), (4061, 35), (4066, 37), (4073, 38), (4079, 40),
    (4085, 42), (4091, 43), (4098, 45), (4104, 47), (4111, 48), (4118, 50), (4125, 52), (4132, 53),
    (4139, 55), (4147, 57), (4155, 58), (4163, 60), (4172, 62), (4181, 63), (4190, 65), (4197, 67),
    (4200, 68)
)

#: The G903 LIGHTSPEED HERO's cell, from its G HUB device package `g903_hero` (build
#: 869589, `battery.xml`, `<powermodel vid="0x46d" pid="0x4087">`), by the same recipe:
#: discharge over the 2837.09 mWh the curve spans.
G903_HERO_DISCHARGE: tuple[tuple[int, float], ...] = (
    (3061, 0), (3129, 0.2), (3176, 0.3), (3213, 0.4), (3245, 0.6), (3272, 0.7), (3297, 0.8),
    (3319, 1), (3339, 1.1), (3357, 1.3), (3374, 1.4), (3389, 1.5), (3404, 1.7), (3417, 1.8),
    (3430, 2), (3442, 2.1), (3453, 2.3), (3464, 2.4), (3474, 2.5), (3484, 2.7), (3493, 2.8),
    (3502, 3), (3510, 3.1), (3518, 3.3), (3526, 3.4), (3533, 3.6), (3539, 3.7), (3546, 3.9),
    (3552, 4), (3558, 4.2), (3563, 4.3), (3569, 4.5), (3574, 4.6), (3578, 4.8), (3583, 4.9),
    (3587, 5.1), (3591, 5.2), (3595, 5.4), (3598, 5.5), (3601, 5.7), (3604, 5.8), (3606, 6),
    (3609, 6.2), (3611, 6.3), (3614, 6.5), (3616, 6.6), (3618, 6.8), (3619, 6.9), (3621, 7.1),
    (3622, 7.2), (3624, 7.4), (3625, 7.5), (3627, 7.7), (3628, 7.8), (3629, 8), (3630, 8.1),
    (3631, 8.3), (3632, 8.4), (3633, 8.6), (3634, 8.8), (3635, 8.9), (3636, 9.2), (3637, 9.4),
    (3638, 9.5), (3639, 9.8), (3640, 10), (3641, 10.3), (3642, 10.4), (3643, 10.8), (3644, 11.1),
    (3645, 11.2), (3646, 11.5), (3647, 11.7), (3648, 12), (3649, 12.1), (3650, 12.5), (3651, 12.6),
    (3652, 12.8), (3653, 13.1), (3654, 13.2), (3655, 13.5), (3656, 13.7), (3657, 13.8), (3658, 14),
    (3659, 14.1), (3660, 14.3), (3661, 14.6), (3662, 14.8), (3663, 14.9), (3664, 15.1),
    (3665, 15.2), (3666, 15.4), (3667, 15.5), (3668, 15.7), (3669, 16), (3671, 16.2), (3672, 16.5),
    (3673, 16.6), (3674, 16.8), (3675, 16.9), (3676, 17.2), (3677, 17.4), (3678, 17.6),
    (3679, 17.7), (3680, 18), (3681, 18.2), (3682, 18.3), (3683, 18.5), (3684, 18.8), (3685, 19),
    (3686, 19.1), (3687, 19.4), (3688, 19.6), (3689, 19.9), (3690, 20.1), (3691, 20.2),
    (3692, 20.5), (3693, 20.7), (3694, 21), (3695, 21.1), (3696, 21.5), (3697, 21.8), (3698, 22.1),
    (3699, 22.2), (3700, 22.5), (3701, 22.7), (3702, 23), (3703, 23.3), (3704, 23.6), (3705, 24),
    (3706, 24.3), (3707, 24.4), (3708, 24.9), (3709, 25.2), (3710, 25.4), (3711, 25.7),
    (3712, 26.2), (3713, 26.5), (3714, 26.8), (3715, 27.1), (3716, 27.6), (3717, 27.9),
    (3718, 28.2), (3719, 28.5), (3720, 29.1), (3721, 29.5), (3722, 29.8), (3723, 30.2),
    (3724, 30.7), (3725, 31), (3726, 31.7), (3727, 32), (3728, 32.4), (3729, 32.8), (3730, 33.2),
    (3731, 33.7), (3732, 34.2), (3733, 34.5), (3734, 35.1), (3735, 35.6), (3736, 35.9),
    (3737, 36.4), (3738, 37), (3739, 37.2), (3740, 37.7), (3741, 38), (3742, 38.4), (3743, 38.8),
    (3744, 39.1), (3745, 39.6), (3746, 39.9), (3747, 40.2), (3748, 40.5), (3749, 40.8),
    (3750, 41.1), (3751, 41.5), (3752, 41.9), (3753, 42.2), (3754, 42.4), (3755, 42.7),
    (3756, 43.2), (3757, 43.4), (3758, 43.7), (3759, 44), (3760, 44.5), (3761, 44.6), (3762, 44.9),
    (3763, 45.3), (3764, 45.6), (3765, 45.7), (3766, 46.1), (3767, 46.4), (3768, 46.5),
    (3769, 46.9), (3770, 47.2), (3771, 47.5), (3772, 47.7), (3773, 48), (3774, 48.3), (3775, 48.4),
    (3776, 48.8), (3777, 48.9), (3778, 49.2), (3779, 49.6), (3780, 49.7), (3781, 50), (3782, 50.4),
    (3783, 50.5), (3784, 50.7), (3785, 51), (3786, 51.3), (3788, 51.6), (3789, 51.8), (3790, 52.1),
    (3791, 52.3), (3792, 52.6), (3793, 52.8), (3794, 52.9), (3795, 53.3), (3796, 53.6),
    (3797, 53.7), (3798, 53.9), (3799, 54.1), (3800, 54.4), (3801, 54.5), (3802, 54.7), (3803, 55),
    (3804, 55.2), (3805, 55.5), (3806, 55.7), (3807, 55.8), (3808, 56), (3809, 56.1), (3810, 56.5),
    (3811, 56.6), (3812, 56.8), (3813, 56.9), (3814, 57.3), (3815, 57.4), (3816, 57.6),
    (3817, 57.8), (3818, 57.9), (3819, 58.2), (3820, 58.4), (3821, 58.6), (3822, 58.7),
    (3823, 58.9), (3824, 59), (3825, 59.4), (3826, 59.5), (3827, 59.7), (3828, 59.9), (3829, 60),
    (3830, 60.2), (3831, 60.3), (3832, 60.5), (3833, 60.7), (3834, 60.8), (3835, 61), (3836, 61.1),
    (3837, 61.3), (3838, 61.5), (3839, 61.6), (3840, 61.8), (3841, 62), (3842, 62.1), (3843, 62.3),
    (3844, 62.6), (3845, 62.8), (3846, 62.9), (3847, 63.1), (3848, 63.3), (3850, 63.4),
    (3851, 63.6), (3852, 63.7), (3853, 63.9), (3854, 64.2), (3856, 64.4), (3857, 64.6),
    (3858, 64.7), (3859, 64.9), (3860, 65.1), (3861, 65.2), (3862, 65.4), (3863, 65.5),
    (3864, 65.7), (3865, 65.9), (3866, 66), (3867, 66.2), (3868, 66.4), (3870, 66.7), (3872, 66.8),
    (3873, 67), (3874, 67.2), (3875, 67.3), (3876, 67.5), (3877, 67.7), (3879, 67.8), (3880, 68),
    (3881, 68.2), (3882, 68.3), (3883, 68.5), (3884, 68.7), (3885, 68.8), (3886, 69), (3887, 69.1),
    (3889, 69.3), (3890, 69.5), (3891, 69.6), (3893, 69.8), (3894, 70), (3895, 70.1), (3896, 70.3),
    (3897, 70.5), (3898, 70.6), (3899, 70.8), (3901, 71), (3902, 71.1), (3903, 71.3), (3904, 71.4),
    (3905, 71.6), (3906, 71.8), (3908, 71.9), (3909, 72.1), (3910, 72.3), (3912, 72.4),
    (3913, 72.6), (3914, 72.8), (3915, 72.9), (3916, 73.1), (3917, 73.3), (3919, 73.4),
    (3920, 73.6), (3921, 73.8), (3922, 73.9), (3923, 74.1), (3924, 74.3), (3926, 74.4),
    (3927, 74.6), (3928, 74.8), (3930, 74.9), (3931, 75.1), (3932, 75.3), (3934, 75.4),
    (3935, 75.6), (3936, 75.8), (3937, 75.9), (3938, 76.1), (3939, 76.3), (3941, 76.4),
    (3942, 76.6), (3943, 76.8), (3944, 76.9), (3946, 77.1), (3947, 77.3), (3948, 77.4),
    (3950, 77.6), (3951, 77.8), (3952, 77.9), (3953, 78.1), (3955, 78.3), (3956, 78.4),
    (3957, 78.6), (3959, 78.8), (3960, 78.9), (3961, 79.1), (3963, 79.3), (3964, 79.4),
    (3965, 79.6), (3967, 79.8), (3968, 79.9), (3970, 80.1), (3971, 80.3), (3973, 80.4),
    (3974, 80.6), (3976, 80.8), (3977, 80.9), (3979, 81.1), (3980, 81.3), (3982, 81.4),
    (3983, 81.6), (3985, 81.8), (3986, 81.9), (3988, 82.1), (3989, 82.3), (3991, 82.5),
    (3992, 82.6), (3994, 82.8), (3996, 83), (3997, 83.1), (3999, 83.3), (4000, 83.5), (4002, 83.6),
    (4003, 83.8), (4005, 84), (4007, 84.1), (4008, 84.3), (4010, 84.5), (4011, 84.6), (4012, 84.8),
    (4014, 85), (4016, 85.2), (4017, 85.3), (4019, 85.5), (4020, 85.7), (4021, 85.8), (4023, 86),
    (4025, 86.2), (4026, 86.3), (4028, 86.5), (4029, 86.7), (4031, 86.9), (4032, 87), (4033, 87.2),
    (4035, 87.4), (4036, 87.5), (4038, 87.7), (4039, 87.9), (4040, 88.1), (4042, 88.2),
    (4044, 88.4), (4045, 88.6), (4047, 88.7), (4048, 88.9), (4050, 89.1), (4051, 89.2),
    (4053, 89.4), (4054, 89.6), (4056, 89.8), (4057, 89.9), (4059, 90.1), (4060, 90.3),
    (4062, 90.4), (4063, 90.6), (4065, 90.8), (4067, 91), (4068, 91.1), (4070, 91.3), (4071, 91.5),
    (4073, 91.7), (4075, 91.8), (4076, 92), (4078, 92.2), (4080, 92.3), (4081, 92.5), (4083, 92.7),
    (4084, 92.9), (4086, 93), (4088, 93.2), (4089, 93.4), (4091, 93.6), (4093, 93.7), (4094, 93.9),
    (4096, 94.1), (4098, 94.2), (4099, 94.4), (4101, 94.6), (4103, 94.8), (4104, 94.9),
    (4106, 95.1), (4108, 95.3), (4110, 95.5), (4112, 95.6), (4113, 95.8), (4115, 96), (4117, 96.2),
    (4119, 96.3), (4120, 96.5), (4122, 96.7), (4124, 96.8), (4126, 97), (4128, 97.2), (4130, 97.4),
    (4132, 97.5), (4134, 97.7), (4136, 97.9), (4138, 98.1), (4140, 98.2), (4142, 98.4),
    (4145, 98.6), (4148, 98.8), (4150, 98.9), (4154, 99.1), (4157, 99.3), (4162, 99.5),
    (4169, 99.6), (4182, 99.8)
)

G903_HERO_CHARGING: tuple[tuple[int, float], ...] = (
    (3378, 0), (3518, 1), (3595, 2), (3673, 3), (3711, 4), (3752, 5), (3762, 6), (3768, 7),
    (3771, 8), (3777, 9), (3782, 10), (3789, 11), (3794, 12), (3803, 13), (3808, 14), (3817, 15),
    (3824, 16), (3830, 17), (3837, 18), (3842, 19), (3848, 20), (3852, 21), (3857, 22), (3860, 23),
    (3863, 24), (3866, 25), (3868, 26), (3871, 27), (3874, 28), (3876, 29), (3879, 30), (3881, 31),
    (3883, 32), (3885, 33), (3888, 34), (3890, 35), (3892, 36), (3894, 37), (3897, 38), (3899, 39),
    (3902, 40), (3904, 41), (3908, 42), (3910, 43), (3913, 44), (3915, 45), (3919, 46), (3923, 47),
    (3925, 48), (3930, 49), (3932, 50), (3937, 51), (3939, 52), (3945, 53), (3948, 54), (3953, 55),
    (3957, 56), (3963, 57), (3967, 58), (3973, 59), (3977, 60), (3984, 61), (3988, 62), (3995, 63),
    (4000, 64), (4007, 65), (4012, 66), (4019, 67), (4024, 68), (4032, 69), (4038, 70), (4046, 71),
    (4052, 72), (4060, 73), (4066, 74), (4075, 75), (4081, 76), (4090, 77), (4100, 78), (4106, 79),
    (4116, 80), (4124, 81), (4136, 82), (4145, 83), (4159, 84), (4168, 85), (4182, 86), (4191, 87),
    (4198, 88)
)

#: Each mouse cell's charging curve rises to its last point, 4.20 V at 68 % on the G502
#: LIGHTSPEED's and 4.198 V at 88 % on the G903's, where the charger holds the voltage while
#: the rest of the charge goes in, so a reading there says nothing of how far along it is.
#: A unit can hold a few millivolts under that point, where the number would stall: the
#: G502 LIGHTSPEED's file counts a voltage that moves within 3 mV a minute as held. So a
#: mouse reads NEARLY_FULL from its curve's voltage at the percent where G HUB's
#: charging_time_slots begin, 66 % and 86 %; from there G HUB raises the percent by time,
#: which this does not.
#: The last reading under 66 %, which lies at 4193.5 mV, between the G502 LIGHTSPEED curve's
#: 4190 at 65 % and 4197 at 67.
G502_LIGHTSPEED_SLOTS_FROM_MV = 4193
#: One millivolt under the G903 curve's 4182 mV point at 86 %.
G903_HERO_SLOTS_FROM_MV = 4181
G502_LIGHTSPEED_CELL = CellCurves(G502_LIGHTSPEED_DISCHARGE, G502_LIGHTSPEED_CHARGING,
                                  nearly_full_mv=G502_LIGHTSPEED_SLOTS_FROM_MV)
G903_HERO_CELL = CellCurves(G903_HERO_DISCHARGE, G903_HERO_CHARGING,
                            nearly_full_mv=G903_HERO_SLOTS_FROM_MV)
