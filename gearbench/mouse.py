"""The mice: which are attached, what each can do, and onboard profile 1.

What a mouse can do is probed from its feature table, except the button table
(BUTTON_MODELS). G6 to G9 run firmware functions and emit nothing, and the mouse has
no 0x1b04 REPROG_CONTROLS_V4 to divert them, so `enable_buttons` rewrites all six
extra buttons in profile 1 to mouse buttons 11 to 16, which nothing on the desktop
uses and the daemon acts on; in HOST mode 0x8110 reports them instead (spy.py).
Profile 1 is the only profile written. In ONBOARD mode a write takes effect at its
commit (onboard.py), and every write backs up the directory and the profile sectors
first and reads the sector back.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard, cast

from .battery import read as read_battery
from .config import ConfigError, counted, read_capped, reason
from .hidpp import (
    VENDOR_ID,
    DeviceGone,
    Feature,
    Hidpp,
    HidppError,
    HidppFailure,
    find_attached,
    not_there,
)
from .onboard import (
    DPI_HIGHEST,
    DPI_LOWEST,
    DPI_STAGES,
    F_ONBOARD,
    MODE_HOST,
    MODE_NAMES,
    MODE_ONBOARD,
    NOOP,
    ROM_SECTOR_BASE,
    OnboardError,
    OnboardProfiles,
    ProfileImage,
    backup_dir,
    dpi_fits,
    entry_mouse_button,
    mouse_button,
    stage_dpi,
)

log = logging.getLogger("gearbench")


@dataclass(frozen=True)
class MouseModel:
    """What this project knows of one mouse model, on every route it answers on."""

    #: For the window's title and the log.
    name: str
    #: Its backup files' prefix in ~/.config/gearbench/backup/, so a restore takes only this
    #: model's files. By model, not product id: the G502 X PLUS cabled is the same mouse as on
    #: its receiver. Two units of one model share it, so a restore takes the model's newest
    #: whichever unit wrote it (the owner's decision of 2026-09-28).
    backup_name: str
    #: Its DPI indicator colors, stage 1 first, from G HUB's device database
    #: (`dpiIndicatorLed`). The firmware owns them: the onboard profile holds no color per stage.
    stage_colors: tuple[int, ...]
    #: Whether EXTRA_BUTTONS is its button table. Those indices are the G502 X PLUS's; written to
    #: another model they would put the wrong entries in the wrong slots. A model without one
    #: reports no `buttons` capability, so the window shows no button section.
    button_table: bool = False
    #: Whether its 0x0020 ConfigChange cookie is trusted (daemon.mice's MOUSE_COOKIE). The G305
    #: has no 0x0020: any other model is configured again at every recheck it answers. A power-on
    #: report or a resume configures in full whatever the cookie says.
    cookie: bool = False


G502X = MouseModel("G502 X PLUS", "g502x", (0xFFFFFF, 0xFF6400, 0x28FFDC, 0xFFFF14, 0xDC14FF),
                   button_table=True, cookie=True)
G305 = MouseModel("G305", "g305", (0xF8E71C, 0xFFFFFF, 0xFD7600, 0xE9168C, 0x00A9E0))

#: Each product id's model, tried in this order, wireless first: what a mouse falls back to
#: when its cable is pulled. The G502 X PLUS is 0x4099 through its receiver (device index 0x01)
#: and 0xC095 cabled (0xFF); the G305 is 0x4074 through its receiver. Read off this hardware on
#: 2026-09-22.
MODEL_OF: dict[int, MouseModel] = {0x4099: G502X, 0xC095: G502X, 0x4074: G305}
PRODUCT_IDS = tuple(MODEL_OF)
# MODEL_OF's fields by product id, as the callers look them up.
MODELS = {product: model.name for product, model in MODEL_OF.items()}
BACKUP_NAMES = {product: model.backup_name for product, model in MODEL_OF.items()}
STAGE_COLORS = {product: model.stage_colors for product, model in MODEL_OF.items()}
BUTTON_MODELS = frozenset(product for product, model in MODEL_OF.items() if model.button_table)
COOKIE_MODELS = frozenset(product for product, model in MODEL_OF.items() if model.cookie)

#: The ids a mouse reports over its cable; the others are its receiver's.
CABLED_IDS = frozenset({0xC095})

#: Cabled, only the vendor HID++ collection, interface 02 as on the keyboard, may be opened:
#: 00 is the pointer and 01 a keyboard collection. A receiver's device has no interface of
#: its own, so its node is matched on the HID id alone.
VENDOR_INTERFACE = 2

F_DPI = 0x2201               # ADJUSTABLE_DPI
F_REPORT_RATE = 0x8060
#: 0x2121 HiResWheel's mode byte: bit 0 HID++ reporting, bit 1 high resolution, bit 2 inverted.
#: hid-logitech-hidpp writes WHEEL_HIRES at each connect over a receiver and scales the wheel
#: down by its multiplier (8 on the G502 X PLUS), so `00` under it scrolls one step in eight.
F_HIRES_WHEEL = 0x2121
WHEEL_HIRES = 0x02

#: The only onboard profile Gearbench writes, and the one the daemon keeps the mouse running
#: (Mice.run_profile_one): G HUB left the G502 X PLUS on profile 2, where it is not in effect.
PROFILE_SECTOR = 0x0001

#: Each model's stage colors in a word, which the window's stage dots say in place of the hex
#: value (the owner's decision of 2026-10-05): the G502 X PLUS's five, then the G305's other
#: four. A color added above with no name here gets a dot that names none.
STAGE_COLOR_NAMES = {
    0xFFFFFF: "white", 0xFF6400: "orange", 0x28FFDC: "cyan", 0xFFFF14: "yellow",
    0xDC14FF: "purple",
    0xF8E71C: "yellow", 0xFD7600: "orange", 0xE9168C: "pink", 0x00A9E0: "blue",
}

#: The widest DPI range of a supported sensor, (lowest, highest): the G502 X PLUS's. The
#: G305's starts at 200. A DPI under the lowest is a stage's number, which the daemon refuses
#: too.
WIDEST_DPI_RANGE = (100, 25600)
#: How long a front end waits for a command that can write onboard profile 1 to the mouse's
#: flash, in seconds, past send_command's 3 s.
PROFILE_WRITE_WAIT = 30.0


#: Button name (config.MOUSE_BUTTONS) -> (onboard index, the mouse button `enable_buttons`
#: writes there). Mouse button n arrives on evdev as BTN_MOUSE (0x110) + n - 1. The index is
#: also the button's bit in 0x8110's mask (spy.mask_bit).
EXTRA_BUTTONS: dict[str, tuple[int, int]] = {
    "g4": (3, 11),
    "g5": (5, 12),
    "g6": (4, 13),
    "g7": (10, 16),
    "g8": (9, 15),
    "g9": (8, 14),
}
BUTTON_CODES: dict[int, str] = {0x110 + n - 1: name for name, (_i, n) in EXTRA_BUTTONS.items()}

#: Button name -> the entry `write_fallback_buttons` writes: the ROM copy's factory entry,
#: except on G9, whose `90 0a` cycles the onboard profiles, and on profile 2 nothing
#: Gearbench writes is in effect. G9 gets the no-op found on Windows, not the empty
#: `ff ff ff ff`: profile 1 holds that at the wheel's indices, 11 and 12, which scroll all the
#: same, so what it does on a control is not known.
FALLBACK_ENTRIES: dict[str, bytes] = {
    "g4": bytes.fromhex("80 01 00 08"),     # mouse button 4, back
    "g5": bytes.fromhex("80 01 00 10"),     # mouse button 5, forward
    "g6": bytes.fromhex("90 07 00 00"),     # DPI shift
    "g7": bytes.fromhex("90 04 00 00"),     # DPI down
    "g8": bytes.fromhex("90 03 00 00"),     # DPI up
    "g9": NOOP,
}


def profile_words(sector: int) -> str:
    """getCurrentProfile's value, the sector the mouse runs, in words."""
    if sector == 0:
        return "no onboard profile (host mode)"
    if sector >= ROM_SECTOR_BASE:
        return f"the factory profile at sector 0x{sector:04x}"
    return f"onboard profile {sector}"


def runs_another_profile(running: int | None) -> TypeGuard[int]:
    """Whether `running`, Mouse.running_profile, is a profile other than 1, where nothing
    written to profile 1 is in effect. None, before a read, is not."""
    return running is not None and running != PROFILE_SECTOR


class HostState(enum.Enum):
    """Where a `mouse` reply's `host` puts the mouse (host_state)."""

    #: In HOST mode with the button spy armed: Gearbench drives G4 to G9.
    DRIVEN = enum.auto()
    #: Counted in HOST mode with host mode off or its arm refused, and answering: stuck there
    #: with G4 to G9 dead, `problems`' mouse_left_in_host.
    LEFT_THERE = enum.auto()
    #: The same for a mouse not answering, whose switch waits for it.
    UNTIL_ANSWERED = enum.auto()
    #: Outside HOST mode, without a measured button spy.
    NO_HOST = enum.auto()
    #: Outside HOST mode, with host mode switched off.
    SWITCHED_OFF = enum.auto()
    #: Outside HOST mode, its arm refused.
    REFUSED = enum.auto()
    #: Outside HOST mode with host mode on, before its setup: a mouse that has not answered.
    NOT_YET = enum.auto()


def host_state(host: Mapping[str, object], answering: bool) -> HostState:
    """Where a `mouse` reply's `host` (wanted, capable, active, error) puts the mouse, for the
    CLI and the window alike; `answering` False for a mouse the daemon marks silent."""
    if host.get("active"):
        if host.get("wanted") and not host.get("error"):
            return HostState.DRIVEN
        return HostState.LEFT_THERE if answering else HostState.UNTIL_ANSWERED
    if not host.get("capable"):
        return HostState.NO_HOST
    if not host.get("wanted"):
        return HostState.SWITCHED_OFF
    return HostState.REFUSED if host.get("error") else HostState.NOT_YET


def host_why(wanted: bool, error: object) -> str:
    """Why a mouse counted in HOST mode is not driven there: with host mode off, an earlier
    arm's failure is no longer why."""
    return f"host mode did not take: {error}" if wanted else "host mode is off"


def stuck_words(many: bool = False) -> str:
    """What a mouse stuck in HOST mode, or `many` of them, cannot do, and the step to take."""
    return (f"G4 to G9 and {'their' if many else 'its'} DPI buttons do nothing. Switch "
            f"{'each mouse' if many else 'the mouse'} off and on to try again")


def stuck_in_host(names: list[str], why: str | None = None) -> str:
    """`problems`' mouse_left_in_host and mouse_host_mode's note: the mice `names` counted in
    HOST mode with host mode off or refused, and `why` when it is one mouse's."""
    many = len(names) > 1
    return (f"{' and '.join(names)} {'are' if many else 'is'} stuck in host mode"
            + (f" ({why})" if why else "") + ": " + stuck_words(many))


def written_note(name: str, host: bool = False, running: int | None = None) -> str:
    """The note after a write to onboard profile 1. It names the mouse, since with two
    attached "the mouse" is a guess. `running` is the profile the mouse was last read
    running (Mouse.running_profile): outside HOST mode, on another, the write is not in
    effect (the owner's decision of 2026-10-11)."""
    if host:
        return (f"Written to onboard profile 1 on the {name}. In host mode, Gearbench uses its "
                f"stages and rate at once; the mouse runs it when Gearbench stops.")
    if runs_another_profile(running):
        return (f"Written to onboard profile 1 on the {name}, which runs "
                f"{profile_words(running)}, so it is not in effect until the mouse runs "
                f"profile 1.")
    return f"Written to onboard profile 1 on the {name}; it is in effect now."


def enable_note(name: str, host: bool = False, running: int | None = None) -> str:
    """written_note for the enable write. In HOST mode the spy reports G4 to G9 whatever
    profile 1 holds."""
    if host:
        return (f"Written to onboard profile 1 on the {name}. Nothing changes while "
                f"Gearbench runs; once it stops, G4 to G9 send buttons 11 to 16, which do "
                f"nothing.")
    return written_note(name, running=running)


def fallback_note(name: str, host: bool = False, running: int | None = None) -> str:
    """written_note for the fallback write. On another profile G4 to G9 do what that one
    holds, so the note says nothing of the factory functions."""
    if host:
        return (f"Written to onboard profile 1 on the {name}. G4 to G9 keep their bindings "
                f"while Gearbench runs; when it stops, G4 to G8 act as from the factory and G9 "
                f"does nothing.")
    if runs_another_profile(running):
        return written_note(name, running=running)
    return (written_note(name) + " G4 to G8 act as from the factory and G9 does nothing, "
            "instead of their bindings.")


#: The refusal to write over a profile 1 whose CRC fails. "Before switching the mouse off":
#: what the mouse does at power-on with such a sector is not known.
BAD_CRC = ("onboard profile 1 has a bad CRC, so nothing is written over it. Write the "
           "newest good backup over it before switching the mouse off: Restore profile "
           "backup… (or `gearbench-ctl mouse-restore`); see RECOVERY in gearbench-ctl(1).")
#: Ends each refusal of the newest backup's file, which every restore takes until it is moved.
SET_IT_ASIDE = (". Set it aside, since each restore takes the newest backup; see RECOVERY in "
                "gearbench-ctl(1).")


class MouseError(RuntimeError):
    @classmethod
    def unreached(cls, failure: HidppFailure) -> MouseError:
        """The error for a read or write the kernel failed with an errno that does not mean the
        node is gone, in the words of the OSError behind it: the failure's own text, "write to
        the device failed: [Errno 71] Protocol error", gives the errno and says it failed."""
        return cls(f"the mouse could not be reached ({reason(failure.__cause__ or failure)})")


@dataclass(frozen=True)
class DpiRange:
    minimum: int
    maximum: int
    step: int

    def clamp(self, value: int) -> int:
        value = max(self.minimum, min(self.maximum, value))
        if self.step > 1:
            value = self.minimum + round((value - self.minimum) / self.step) * self.step
        return min(value, self.maximum)


def find_attached_mice() -> list[tuple[Path, int, int]]:
    """Every known mouse attached now, in PRODUCT_IDS order. Two mice on separate receivers
    each have a hidraw node with its own product id (measured 2026-09-22)."""
    found = []
    for product in PRODUCT_IDS:
        one = find_attached(VENDOR_ID, product, interface=VENDOR_INTERFACE)
        if one is not None:
            found.append(one)
    return found


def enabled_buttons(image: ProfileImage) -> list[str]:
    """The buttons whose entry already sends the mouse button the daemon reads."""
    return [name for name, (index, button) in EXTRA_BUTTONS.items()
            if entry_mouse_button(image.button(index)) == button]


def buttons_enabled(image: ProfileImage) -> bool:
    """True when all six buttons send the mouse buttons the daemon reads."""
    return len(enabled_buttons(image)) == len(EXTRA_BUTTONS)


def enable_extra_buttons(image: ProfileImage) -> ProfileImage:
    """A copy of `image` with G4-G9 bound to mouse buttons 11-16; only those six entries and
    the CRC change."""
    edited = ProfileImage(bytes(image.data))
    for index, button in EXTRA_BUTTONS.values():
        edited.set_button(index, mouse_button(button))
    return edited


def buttons_fallback(image: ProfileImage) -> bool:
    """True when all six hold their FALLBACK_ENTRIES."""
    return all(image.button(EXTRA_BUTTONS[name][0]) == entry
               for name, entry in FALLBACK_ENTRIES.items())


def fallback_extra_buttons(image: ProfileImage) -> ProfileImage:
    """A copy of `image` with G4-G9 set to FALLBACK_ENTRIES; only those six entries and the CRC
    change."""
    edited = ProfileImage(bytes(image.data))
    for name, (index, _button) in EXTRA_BUTTONS.items():
        edited.set_button(index, FALLBACK_ENTRIES[name])
    return edited


def stage_index(stages: list[int], wanted: int | None, previous: int | None,
                previous_index: int, what: str) -> int:
    """Which stage the default or the shift lands on. A `wanted` that is not a stage raises,
    since picking another would hide the mistake. `None`, which the window sends when it
    edits the stage in this slot, keeps the previous value if it is still a stage, else its
    position, clamped to the list."""
    if wanted is not None:
        if wanted not in stages:
            among = "the one stage" if len(stages) == 1 else "one of the stages"
            raise MouseError(f"{what} DPI {wanted} is not {among}, "
                             f"{', '.join(map(str, stages))}")
        return stages.index(wanted)
    if previous in stages:
        return stages.index(previous)
    return min(previous_index, len(stages) - 1)


def place_stages(stages: list[int], default: int | None, shift: int | None,
                 old: list[int], old_default: int, old_shift: int,
                 dpi_range: DpiRange | None) -> tuple[list[int], int, int]:
    """A new stage list, snapped to the sensor's step, and the indices its default and shift
    land on, for profile 1 and a game profile alike. `default` and `shift` are DPI values, or
    None to keep the old one's DPI if it is still a stage, else its index, clamped
    (stage_index); `old_default` and `old_shift` index `old`, an index past its end taken as
    its last stage, as stage_dpi shows it (the owner's decision of 2026-10-11). A value
    outside the sensor's range raises MouseError: clamped, a typing slip would be written to
    flash as the range's end."""

    def snap[V: int | None](value: V) -> V | int:
        if not dpi_range or value is None:
            return value
        if not dpi_range.minimum <= value <= dpi_range.maximum:
            raise MouseError(f"DPI {value} is outside the sensor's range, "
                             f"{dpi_range.minimum} to {dpi_range.maximum}")
        return dpi_range.clamp(value)

    snapped = [snap(s) for s in stages]
    default, shift = snap(default), snap(shift)
    if not 1 <= len(snapped) <= DPI_STAGES:
        raise MouseError(f"between 1 and {DPI_STAGES} DPI stages, not {len(snapped)}")
    for value in snapped:
        if not dpi_fits(value):
            raise MouseError(f"DPI {value} is out of range")
    if len(set(snapped)) != len(snapped):
        raise MouseError(f"DPI stages must be distinct; got {', '.join(map(str, snapped))} "
                         f"after snapping")
    if old:
        # A byte naming an unused slot past the stages: the stage it is shown as keeps its
        # place, where the raw index would land on a stage the user never chose.
        old_default, old_shift = min(old_default, len(old) - 1), min(old_shift, len(old) - 1)
    was_default, was_shift = stage_dpi(old, old_default), stage_dpi(old, old_shift)
    return (snapped,
            stage_index(snapped, default, was_default, old_default, "default"),
            stage_index(snapped, shift, was_shift, old_shift, "shift"))


def backup_name(product: int) -> str:
    """The prefix of this model's backup files. A model not in `BACKUP_NAMES`
    gets its product id, so two models can never share one."""
    return BACKUP_NAMES.get(product, f"mouse-{product:04x}")


class Mouse:
    def __init__(self, dev: Hidpp, product: int) -> None:
        self.dev = dev
        #: Which model answered. Only the things that cannot be probed depend on
        #: it, which is the button table.
        self.product = product
        self.name = MODELS.get(product, f"Logitech {product:04x}")
        self.backup_name = backup_name(product)
        self._dpi = dev.feature(F_DPI)
        #: The level byte the last set_dpi sent; the daemon repaints after a nonzero one.
        self.dpi_level_sent = 0
        self._rate = dev.feature(F_REPORT_RATE)
        #: 0x2121, or None; read by Mice.keep_wheel.
        self.wheel = dev.feature(F_HIRES_WHEEL)
        self.dpi_range = self._read_dpi_range(self._dpi) if self._dpi is not None else None
        self.onboard: OnboardProfiles | None = None
        #: Profile 1 as last read from the mouse.
        self.profile: ProfileImage | None = None
        #: A write failed and the sector could not be read back, so what it holds is not known.
        #: `profile` keeps the read before; describe reads again. Cleared only by a read that
        #: works, or a bad sector would show as the old profile, CRC ok.
        self.flash_unknown = False
        #: Profile 1 writes started (write_profile). The daemon puts back what a write moved
        #: only after one started: a refusal before one wrote the mouse nothing.
        self.sector_writes = 0
        #: The profile the mouse was last read running (current_profile), None before a read.
        self.running_profile: int | None = None
        if dev.feature(F_ONBOARD) is not None:
            self.onboard = OnboardProfiles(dev)
            self.profile = ProfileImage(self.onboard.read_sector(PROFILE_SECTOR))

    # -- onboard mode --------------------------------------------------------

    def ensure_onboard_mode(self, timeout: float = 1.0, retries: int = 2) -> str | None:
        """Switch the mouse to ONBOARD mode from any other mode, as G HUB or a crashed run can
        leave it in HOST mode; returns what was done, or None. Mice.run_profile_one then settles
        the profile: G HUB leaves the G502 X PLUS on profile 2."""
        if self.onboard is None:
            return None
        mode = self.onboard.get_mode(timeout=timeout, retries=retries)
        if mode == MODE_ONBOARD:
            return None
        self.onboard.set_mode(MODE_ONBOARD, timeout=timeout, retries=retries)
        return f"was in {MODE_NAMES.get(mode, hex(mode))} mode; switched to onboard"

    def get_mode(self, timeout: float = 1.0, retries: int = 2) -> int | None:
        """0x8100 fn 2: the mode byte as answered, not checked, so maybe neither MODE_ONBOARD
        nor MODE_HOST; None for a mouse without 0x8100."""
        if self.onboard is None:
            return None
        return self.onboard.get_mode(timeout=timeout, retries=retries)

    def set_mode(self, mode: int, timeout: float = 1.0, retries: int = 2) -> None:
        """0x8100 fn 1: `01` ONBOARD, `02` HOST. It writes no flash."""
        if self.onboard is not None:
            self.onboard.set_mode(mode, timeout=timeout, retries=retries)

    def current_profile(self, timeout: float = 1.0, retries: int = 2) -> int | None:
        """The sector the mouse runs (profile_words), kept as `running_profile`."""
        if self.onboard is None:
            return None
        self.running_profile = self.onboard.current_profile(timeout=timeout, retries=retries)
        return self.running_profile

    def switch_to_profile_one(self, timeout: float = 1.0, retries: int = 2) -> int | None:
        """Run profile 1: G HUB's 0x8100 fn 3 `00 01`, no flash write. Returns what the mouse runs
        after it. It lands on the profile's default DPI stage (measured on the G502 X PLUS
        2026-09-24), so the caller puts a remembered DPI back."""
        if self.onboard is None:
            return None
        self.onboard.set_current_profile(PROFILE_SECTOR, timeout=timeout, retries=retries)
        return self.current_profile(timeout, retries)

    # -- DPI -----------------------------------------------------------------

    def _read_dpi_range(self, dpi: Feature) -> DpiRange | None:
        """Decode getSensorDpiList: 16-bit values after the sensor index, where one with the top
        bits 0xe000 is a range marker carrying the step, between the minimum and the maximum."""
        reply = self.dev.request(dpi.index, 1, bytes([0x00]), long=True)
        values = [int.from_bytes(reply[i:i + 2], "big") for i in range(1, 15, 2)]
        for i, value in enumerate(values):
            if value & 0xE000 == 0xE000 and i and i + 1 < len(values):
                return DpiRange(values[i - 1], values[i + 1], value & 0x1FFF)
        usable = [v for v in values if v and v & 0xE000 != 0xE000]
        return DpiRange(min(usable), max(usable), 1) if usable else None

    def get_dpi(self, timeout: float = 1.0, retries: int = 2) -> int:
        if self._dpi is None:
            raise MouseError("this mouse has no adjustable DPI")
        return int.from_bytes(self.dev.request(self._dpi.index, 2, bytes([0x00]),
                                               timeout=timeout, retries=retries)[1:3], "big")

    def set_dpi(self, value: int, timeout: float = 1.0, retries: int = 2,
                indicate: bool = True, stages: list[int] | None = None) -> int:
        """Set the live DPI (0x2201 setSensorDpi). The last byte is the level: the 1-based stage
        in `stages`, a game profile's own, or profile 1's when None (_dpi_level), as G HUB sends
        it, `00 04 b0 02` for 1200 at stage 2, else 0 (the owner's decision of 2026-09-24). A
        nonzero level flashes the G502 X PLUS's lights in the stage's color (STAGE_COLORS) for
        3 to 4 s and leaves them dark, which the daemon repaints (DPI_FLASH_REPAINT): measured in
        ONBOARD mode on 2026-09-24 and in HOST mode on 2026-09-25. `indicate` False sends 0, for
        a DPI put back rather than chosen (Dpi.restore_dpi)."""
        if self._dpi is None:
            raise MouseError("this mouse has no adjustable DPI")
        target = self.dpi_range.clamp(value) if self.dpi_range else value
        if not dpi_fits(target):
            # Nothing clamps without a range (_read_dpi_range), and a range the mouse lists
            # may start at 0.
            made = "" if target == value else f"its sensor's range and step make it {target}, and "
            raise MouseError(f"DPI {value} is out of range for the {self.name}: {made}a DPI is "
                             f"{DPI_LOWEST} to {DPI_HIGHEST}")
        level = self._dpi_level(target, stages) if indicate else 0
        self.dev.request(
            self._dpi.index, 3,
            bytes([0x00, target >> 8, target & 0xFF, level]), long=True,
            timeout=timeout, retries=retries,
        )
        self.dpi_level_sent = level
        return target

    def _dpi_level(self, target: int, stages: list[int] | None) -> int:
        """`target`'s 1-based stage, or 0. In a game profile's own `stages`, which have no gap
        and may equal profile 1's, it is the position; with None, it is the slot in profile 1,
        as the mouse counts them, unused slots included."""
        if stages is not None:
            return stages.index(target) + 1 if target in stages else 0
        if self.profile is None:
            return 0
        return next((s + 1 for s in range(DPI_STAGES) if self.profile.dpi_slot(s) == target), 0)

    @property
    def dpi_stages(self) -> list[int]:
        return self.profile.dpi_stages if self.profile else []

    @property
    def shift_dpi(self) -> int | None:
        """The stage the DPI-shift paddle drops to, from the profile."""
        if self.profile is None:
            return None
        return stage_dpi(self.profile.dpi_stages, self.profile.shift_dpi_index)

    def step_dpi(self, direction: int, stages: list[int] | None = None) -> int:
        """Move to the next stage up (+1) or down (-1) from the nearest, of
        `stages`, profile 1's by default."""
        choices = self.dpi_stages if stages is None else stages
        if not choices:
            raise MouseError(f"the {self.name} has no DPI stages to step through")
        current = self.get_dpi()
        nearest = min(range(len(choices)), key=lambda i: abs(choices[i] - current))
        target = max(0, min(len(choices) - 1, nearest + direction))
        return self.set_dpi(choices[target], stages=stages)

    # -- report rate -----------------------------------------------------------

    #: The rates the mouse lists, cached once read: the bitmap is fixed for the model.
    _rates: list[int] | None = None

    def report_rates(self) -> list[int]:
        """Supported rates in Hz, highest first, asked once per handle. The device reports a bitmap
        of intervals: bit n set means n+1 ms is supported."""
        if self._rate is None:
            return []
        if self._rates is None:
            bitmap = self.dev.request(self._rate.index, 0)[0]
            self._rates = [1000 // (bit + 1) for bit in range(8) if bitmap >> bit & 1]
        return list(self._rates)

    def _rate_feature(self) -> Feature:
        if self._rate is None:
            raise MouseError("this mouse has no adjustable report rate")
        return self._rate

    def get_report_rate(self) -> int:
        interval = self.dev.request(self._rate_feature().index, 1)[0]
        return 1000 // max(interval, 1)

    def set_report_rate(self, hz: int) -> str:
        """Set the report rate; returns "onboard" or "live" for where it went.

        The firmware refuses a live 0x8060 write while an onboard profile runs, so in ONBOARD
        mode profile 1's byte is rewritten; the live write is for a mouse left in HOST mode or
        one without 0x8100. The daemon's HOST mode does both (store_report_rate, then
        set_live_report_rate), so profile 1 keeps the rate the next arm sends.
        """
        self._check_rate(hz)
        if self.onboard is not None and self.onboard.get_mode() != MODE_HOST:
            self.store_report_rate(hz)
            return "onboard"
        try:
            self.set_live_report_rate(hz)
        except HidppError as exc:
            if exc.unreachable:
                raise           # a mouse its receiver cannot reach, not a refusal (mouse_call)
            refused = f"the {self.name} refused {hz} Hz live ({exc}); nothing was written"
            if self.onboard is None:
                raise MouseError(refused) from None
            raise MouseError(f"{refused}. The rate is kept in the onboard profile, which this "
                             f"command writes in onboard mode") from None
        return "live"

    def _check_rate(self, hz: int) -> None:
        self._rate_feature()
        available = self.report_rates()
        if hz not in available:
            raise MouseError(f"{hz} Hz is not supported; this mouse takes "
                             f"{', '.join(map(str, available))} Hz")

    def store_report_rate(self, hz: int) -> ProfileImage:
        """Rewrite profile 1's rate byte through write_profile, whatever the mode."""
        self._check_rate(hz)
        image = self._edited_profile()
        try:
            image.report_rate = hz
        except OnboardError as exc:
            # A rate the mouse lists that the profile's byte cannot hold, as 333 Hz (bit 2).
            raise MouseError(str(exc)) from None
        return self.write_profile(image)

    def set_live_report_rate(self, hz: int, timeout: float = 1.0, retries: int = 2) -> None:
        """0x8060 fn 2, the interval in ms: in effect at once in HOST mode, refused while an onboard
        profile runs. Not stored, so the daemon sends it at every arm of HOST mode."""
        self.dev.request(self._rate_feature().index, 2, bytes([1000 // hz]),
                         timeout=timeout, retries=retries)

    # -- the wheel ---------------------------------------------------------------

    def wheel_mode(self, timeout: float = 1.0, retries: int = 2) -> int | None:
        """0x2121 fn 1: the wheel's mode byte (F_HIRES_WHEEL), or None without 0x2121."""
        if self.wheel is None:
            return None
        return self.dev.request(self.wheel.index, 1, timeout=timeout, retries=retries)[0]

    def set_wheel_mode(self, mode: int, timeout: float = 1.0, retries: int = 2) -> None:
        """0x2121 fn 2, in effect at once."""
        if self.wheel is not None:
            self.dev.request(self.wheel.index, 2, bytes([mode]), timeout=timeout,
                             retries=retries)

    # -- onboard profile -------------------------------------------------------

    def _edited_profile(self) -> ProfileImage:
        """A fresh copy of profile 1 to edit, read from the mouse now, not `self.profile`: an
        edit rewrites the whole sector, so a stale copy would put back what changed since."""
        if self.onboard is None:
            raise MouseError("this mouse has no onboard profile feature")
        try:
            image = ProfileImage(self.onboard.read_sector(PROFILE_SECTOR))
        except OnboardError as exc:
            # As in write_profile: uncaught, a short read is an internal error with a traceback.
            raise MouseError(str(exc)) from None
        # What the sector holds now, bad or not: `gearbench-ctl mouse` shows it.
        self._take_sector(image)
        if not image.crc_ok:
            raise MouseError(BAD_CRC)
        return ProfileImage(bytes(image.data))

    def _take_sector(self, image: ProfileImage) -> None:
        """Adopt a read of sector 0x0001 that worked: the sector is known now."""
        self.profile = image
        self.flash_unknown = False

    def write_profile(self, image: ProfileImage, over_bad_crc: bool = False) -> ProfileImage:
        """Back up, refuse on a bad current CRC, write, verify, and adopt the result.

        A failed write can still have changed the sector, so a failure reads it again
        (_reread_after_failure). When that read finds the image written, as after a commit whose
        answer or first read-back was lost, the write counts as done and returns as a success:
        raised, a restore would leave the state it replaced as the newest backup, for a retry to
        write back (the owner's decision of 2026-10-03). Otherwise a HidppFailure is re-raised as
        it came, so the caller can tell a mouse switched off from one that has gone. Only a
        restore passes `over_bad_crc`: it writes a backup that does not depend on the sector as
        it was.
        """
        if self.onboard is None:
            raise MouseError("this mouse has no onboard profile feature")
        try:
            current = self.onboard.backup_all(self.backup_name)
        except (OSError, OnboardError, HidppFailure) as exc:
            # A later sector stopped the backup, but sector 0x0001's read worked: what an
            # earlier write left unknown is settled, and _set_aside_unchanged can compare.
            made = self.onboard.backed_up.get(PROFILE_SECTOR)
            if made is not None:
                self._take_sector(ProfileImage(made[1]))
            if isinstance(exc, OSError):
                raise MouseError(f"could not back up the profile sectors to {backup_dir()} "
                                 f"({reason(exc)}); nothing was written") from None
            if isinstance(exc, OnboardError):
                raise MouseError(reason(exc)) from None
            raise
        found = ProfileImage(current[PROFILE_SECTOR])
        self._take_sector(found)
        if not over_bad_crc and not found.crc_ok:
            raise MouseError(BAD_CRC)
        written = image.to_bytes()
        try:
            # write_sector verifies byte for byte, so it returns what the sector holds now.
            self.sector_writes += 1
            now = self.onboard.write_sector(PROFILE_SECTOR, written)
        except (OnboardError, HidppFailure) as exc:
            if self._reread_after_failure(self.onboard) != written:
                if isinstance(exc, OnboardError):
                    raise MouseError(str(exc)) from None
                raise
            log.info("the write of sector 0x%04x failed (%s), but it reads back as written",
                     PROFILE_SECTOR, exc)
            # _reread_after_failure took up the sector it answered with.
            return cast(ProfileImage, self.profile)
        self.profile = ProfileImage(now)
        return self.profile

    def _reread_after_failure(self, onboard: OnboardProfiles) -> bytes | None:
        """Take up what the sector holds now, after a write that failed, and return it. A re-read
        that fails too returns None and keeps the old profile, marked unknown, so `gearbench-ctl
        mouse`, the stage editor and the DPI shift still have one."""
        try:
            now = onboard.read_sector(PROFILE_SECTOR)
        except (OnboardError, HidppFailure):
            self.flash_unknown = True
            return None
        self.profile = ProfileImage(now)
        return now

    def enable_buttons(self) -> ProfileImage:
        """Bind G4-G9 to mouse buttons 11-16 in profile 1."""
        return self.write_profile(enable_extra_buttons(self._edited_profile()))

    def write_fallback_buttons(self) -> ProfileImage:
        """Set G4-G9 to the fallback in profile 1, FALLBACK_ENTRIES."""
        return self.write_profile(fallback_extra_buttons(self._edited_profile()))

    def restore_backup(self) -> tuple[ProfileImage, str]:
        """Write the newest backup of profile 1 back. Returns the image and its path. One that
        fails with the sector as it was sets its own backup aside (_set_aside_unchanged), if
        that is a `.bin`: over a bad CRC it is a `.bad-read`, which stays."""
        if self.onboard is None:
            raise MouseError("this mouse has no onboard profile feature")
        path = self.onboard.newest_backup(self.backup_name, PROFILE_SECTOR)
        if path is None:
            raise MouseError(f"no backup of sector 0x{PROFILE_SECTOR:04x} found")
        size = self.onboard.info.sector_size
        try:
            # Capped at the sector's size, which is all a backup of it can hold.
            data = read_capped(path, size)
        except ConfigError as exc:
            raise MouseError(f"{exc}{SET_IT_ASIDE}") from None
        except OSError as exc:
            # The reason alone: an OSError from the open quotes the path again after it.
            raise MouseError(f"cannot read {path}: {reason(exc)}{SET_IT_ASIDE}") from None
        if len(data) != size:
            raise MouseError(f"{path} is {counted(len(data), 'byte')}, the sector is "
                             f"{size}{SET_IT_ASIDE}")
        # A sector's size holds a profile: __init__ read one from it.
        image = ProfileImage(data)
        if not image.crc_ok:
            raise MouseError(f"{path} has a bad CRC; not writing it{SET_IT_ASIDE}")
        try:
            return self.write_profile(image, over_bad_crc=True), str(path)
        except (MouseError, HidppFailure):
            self._set_aside_unchanged(self.onboard)
            raise

    def _set_aside_unchanged(self, onboard: OnboardProfiles) -> None:
        """After a failed restore that left the sector as it was, rename the backup it made to
        `<name>.unchanged` (OnboardProfiles.set_aside), when it is a `.bin`. Left the newest,
        it would be what the next restore writes: the state this one was to replace (the
        owner's decision of 2026-09-28)."""
        made = onboard.backed_up.get(PROFILE_SECTOR)
        if made is None or self.flash_unknown or self.profile is None:
            return
        path, held = made
        if self.profile.data != held:
            return
        try:
            aside = onboard.set_aside(path)
        except OSError as exc:
            log.warning("could not set %s aside (%s); set it aside by hand, or the next "
                        "mouse-restore writes it back", path, reason(exc))
            return
        if aside is None:
            return
        log.info("the restore left sector 0x%04x as it was; its backup of it is now %s, so "
                 "the next restore takes the one before", PROFILE_SECTOR, aside)

    def factory_profile(self) -> ProfileImage:
        """Profile 1's factory copy, read from the mouse's ROM: the first sector after
        ROM_SECTOR_BASE, which nothing writes. Its CRC field is 0xffff by construction."""
        if self.onboard is None or not self.onboard.info.rom_profile_count:
            raise MouseError(f"the {self.name} has no factory profile to read")
        try:
            return ProfileImage(self.onboard.read_sector(ROM_SECTOR_BASE + PROFILE_SECTOR))
        except OnboardError as exc:
            raise MouseError(str(exc)) from None

    def set_dpi_stages(self, stages: list[int], default: int | None = None,
                       shift: int | None = None) -> ProfileImage:
        """Write a new stage list. `default` and `shift` are DPI values, not indices, snapped
        as the stages are."""
        image = self._edited_profile()
        snapped, default_index, shift_index = place_stages(
            stages, default, shift, image.dpi_stages, image.default_dpi_index,
            image.shift_dpi_index, self.dpi_range)
        image.dpi_stages = snapped
        image.default_dpi_index = default_index
        image.shift_dpi_index = shift_index
        return self.write_profile(image)

    # -- what the front ends show --------------------------------------------

    def button_words(self, image: ProfileImage) -> list[str]:
        """What each of `image`'s base buttons does, as many as getInfo says this mouse has.
        `image` is one this mouse's 0x8100 read or wrote."""
        # So the mouse has 0x8100: one without raises at _edited_profile.
        onboard = cast(OnboardProfiles, self.onboard)
        return image.describe(onboard.info.button_count)["buttons"]

    @property
    def capabilities(self) -> list[str]:
        """What this mouse can do, probed, for a front end to render from. `buttons` means a model
        in BUTTON_MODELS. `lighting` is added by the daemon's `mouse` reply
        (MouseReply._cmd_mouse) for clusters that enumerate through 0x8071: the G305 lists
        0x8070, reports one zone and accepts a color write, and has no lights."""
        able = []
        if self._dpi is not None:
            able.append("dpi")
        if self._rate is not None:
            able.append("report_rate")
        if self.onboard is not None:
            able += ["onboard", "dpi_stages"]
        if self.product in BUTTON_MODELS:
            able.append("buttons")
        return able

    def describe(self, ask: bool = True, battery: bool = True) -> dict:
        """The readings the `mouse` reply starts from (MouseReply adds G4 to G9's state and the
        rest), a missing reading left as None, or [] for `report_rates`.

        A refused reading is skipped; DeviceGone is let through, so the caller tears the mouse
        down. The first `not_there` ends the device reads and clears `answering`: a mouse
        switched off behind its receiver would otherwise cost the request budget per reading on
        the daemon's only thread. `ask` False reads nothing from the device; `battery` False
        skips the battery, which the daemon takes from the mouse's own reports.
        """
        out: dict = {"dpi": None, "dpi_range": None, "report_rate": None,
                     "report_rates": [], "battery": None,
                     "onboard": None, "answering": ask}

        def dpi() -> None:
            out["dpi"] = self.get_dpi()
            if self.dpi_range:
                out["dpi_range"] = [self.dpi_range.minimum, self.dpi_range.maximum,
                                    self.dpi_range.step]

        def rate() -> None:
            out["report_rate"] = self.get_report_rate()
            out["report_rates"] = self.report_rates()

        def charge() -> None:
            if battery:
                out["battery"] = read_battery(self.dev, name=self.name)

        def mode() -> None:
            out["mode"] = self.get_mode()

        for read in (dpi, rate, charge, mode, self.current_profile):
            if not out["answering"]:
                break
            try:
                read()
            except DeviceGone:
                raise
            except (HidppFailure, MouseError) as exc:
                if not_there(exc):
                    out["answering"] = False
        mode_value = out.pop("mode", None)
        if self.flash_unknown and out["answering"] and not self._reread_unknown_sector():
            out["answering"] = False
        if self.onboard is not None and self.profile is not None:
            out["onboard"] = {
                "mode": MODE_NAMES.get(mode_value, mode_value),
                "sector_size": self.onboard.info.sector_size,
                "profile_count": self.onboard.info.profile_count,
                **self.profile.describe(self.onboard.info.button_count),
                "flash_unknown": self.flash_unknown,
                # As last read; on profile 2 none of the above is in effect.
                "running_profile": self.running_profile,
            }
        return out

    def _reread_unknown_sector(self) -> bool:
        """describe's re-read of a sector a failed write left unknown. False when the mouse
        is not there."""
        # `flash_unknown`, which describe checks first, is set only by a write's re-read
        # (_reread_after_failure), on a mouse with 0x8100.
        onboard = cast(OnboardProfiles, self.onboard)
        try:
            self._take_sector(ProfileImage(onboard.read_sector(PROFILE_SECTOR)))
        except DeviceGone:
            raise
        except (OnboardError, HidppFailure) as exc:
            return not not_there(exc)
        return True
