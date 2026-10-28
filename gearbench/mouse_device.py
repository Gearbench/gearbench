"""The handle the daemon holds for each mouse attached (Mouse): its DPI, report rate,
battery, mode, the profile it runs, the `mouse` reply's readings, and onboard profile 1.

Profile 1 is the only profile written, and only on a model whose writes are let through
(MouseModel.writes_tested); another's are refused. In ONBOARD mode a write takes effect at its
commit (onboard.py), and every write backs up the directory and the profile sectors first and
reads the sector back.
"""

from __future__ import annotations

import logging
from typing import cast

from . import config
from .battery import has_battery, read as read_battery
from .hidpp import (
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    DeviceGone,
    Hidpp,
    HidppError,
    HidppFailure,
    not_there,
)
from .mouse import (
    BAD_CRC,
    F_HIRES_WHEEL,
    MODEL_OF,
    NO_ONBOARD,
    PROFILE_SECTOR,
    SET_IT_ASIDE,
    ButtonTable,
    SwitchRefused,
    UntestedWrite,
    dpi_in_host_only,
    not_tested,
    switch_refusal_words,
    untested_words,
)
from .onboard import (
    DPI_HIGHEST,
    DPI_LOWEST,
    DPI_STAGES,
    F_ONBOARD,
    MODE_HOST,
    MODE_NAMES,
    MODE_ONBOARD,
    ROM_SECTOR_BASE,
    OnboardError,
    OnboardProfiles,
    ProfileImage,
    dpi_fits,
    stage_dpi,
)
from .sensor import (
    DpiRange,
    MouseError,
    SensorDpi,
    SensorRate,
    dpi_feature,
    next_stage,
    place_stages,
    rate_feature,
)
from .settings_files import ConfigError, backup_dir, read_capped
from .wording import counted, reason

log = logging.getLogger("gearbench")


class Mouse:
    def __init__(self, dev: Hidpp, product: int) -> None:
        self.dev = dev
        #: The product id that answered, its model's key in MODEL_OF.
        self.product = product
        #: Its model, for what its feature table cannot say.
        self.model = model = MODEL_OF[product]
        self.name = model.name
        #: Its model's programmable buttons, or None (MouseModel.button_table).
        self.button_table = model.button_table
        self.backup_name = model.backup_name
        #: 0x2201, 0x2202 (sensor.dpi_feature), or None.
        self._dpi = dpi_feature(dev)
        #: The level byte the last set_dpi sent; the daemon repaints after a nonzero one.
        self.dpi_level_sent = 0
        #: 0x8060, 0x8061 (sensor.rate_feature), or None.
        self._rate = rate_feature(dev)
        #: 0x2121, or None; read by Mice.keep_wheel.
        self.wheel = dev.feature(F_HIRES_WHEEL)
        #: Whether its feature table lists a battery feature (battery.has_battery): the wired
        #: G502 HERO and G502 X list none.
        self.battery_feature = has_battery(dev)
        # Sensor 0's list.
        self.dpi_range: DpiRange | None = (self._dpi.read_range() if self._dpi is not None
                                           else None)
        self.onboard: OnboardProfiles | None = None
        #: Profile 1 as last read from the mouse.
        self.profile: ProfileImage | None = None
        #: A write failed and the sector could not be read back, so what it holds is not known.
        #: `profile` keeps the read before; describe and switch_to_profile_one read again.
        #: Cleared only by a read that works, or a bad sector would show as the old profile, CRC
        #: ok.
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

    def ensure_onboard_mode(self, timeout: float = REQUEST_TIMEOUT,
                            retries: int = REQUEST_RETRIES) -> str | None:
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

    def get_mode(self, timeout: float = REQUEST_TIMEOUT,
                 retries: int = REQUEST_RETRIES) -> int | None:
        """0x8100 fn 2: the mode byte as answered, not checked, so maybe neither MODE_ONBOARD
        nor MODE_HOST; None for a mouse without 0x8100."""
        if self.onboard is None:
            return None
        return self.onboard.get_mode(timeout=timeout, retries=retries)

    def set_mode(self, mode: int, timeout: float = REQUEST_TIMEOUT,
                 retries: int = REQUEST_RETRIES) -> None:
        """0x8100 fn 1: `01` ONBOARD, `02` HOST. It writes no flash: measured on the G502 X
        PLUS, which a power cycle takes out of HOST mode."""
        if self.onboard is not None:
            self.onboard.set_mode(mode, timeout=timeout, retries=retries)

    def current_profile(self, timeout: float = REQUEST_TIMEOUT,
                        retries: int = REQUEST_RETRIES) -> int | None:
        """The sector the mouse runs (profile_words), kept as `running_profile`."""
        if self.onboard is None:
            return None
        self.running_profile = self.onboard.current_profile(timeout=timeout, retries=retries)
        return self.running_profile

    def switch_to_profile_one(self, timeout: float = REQUEST_TIMEOUT,
                              retries: int = REQUEST_RETRIES) -> int | None:
        """Run profile 1: G HUB's 0x8100 fn 3 `00 01`, no flash write. Returns what the mouse runs
        after it. It lands on the profile's default DPI stage (measured on the G502 X PLUS
        2026-09-24), so the caller puts a remembered DPI back. SwitchRefused, with no switch sent,
        where switch_refusal has a reason. A profile 1 a failed write left unknown is read first:
        switch_refusal would ask the copy from before the write, CRC ok, of a sector that may
        fail it now."""
        if self.onboard is None:
            return None
        self._reread_unknown_sector()
        refused = self.switch_refusal()
        if refused is not None:
            raise SwitchRefused(refused)
        self.onboard.set_current_profile(PROFILE_SECTOR, timeout=timeout, retries=retries)
        return self.current_profile(timeout, retries)

    def switch_refusal(self) -> str | None:
        """switch_refusal_words for this mouse: why it is not to be switched to profile 1, and
        what to do, or None."""
        # Asked of a mouse with 0x8100 alone, whose profile 1 __init__ read.
        return switch_refusal_words(self.model.writes_tested,
                                    cast(ProfileImage, self.profile).crc_ok)

    # -- DPI -----------------------------------------------------------------

    def _dpi_feature(self) -> SensorDpi:
        if self._dpi is None:
            raise MouseError(f"the {self.name} has no adjustable DPI")
        return self._dpi

    def get_dpi(self, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> int:
        return self._dpi_feature().get(timeout, retries)

    def set_dpi(self, value: int, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES,
                indicate: bool = True, stages: list[int] | None = None) -> int:
        """Set the live DPI (0x2201 setSensorDpi, or 0x2202's write). 0x2201's last byte is the
        level: the 1-based stage in `stages`, a game profile's own, or profile 1's when None
        (_dpi_level), as G HUB sends it, `00 04 b0 02` for 1200 at stage 2, else 0 (the owner's
        decision of 2026-09-24). A nonzero level flashes the G502 X PLUS's lights in the stage's
        color (STAGE_COLORS) for 3 to 4 s and leaves them dark, which the daemon repaints
        (DPI_FLASH_REPAINT): measured in ONBOARD mode on 2026-09-24 and in HOST mode on
        2026-09-25. `indicate` False sends 0, for a DPI put back rather than chosen
        (Dpi.restore_dpi). 0x2202's write has no level and is refused outside host mode
        (sensor.py), which the refusal says."""
        dpi = self._dpi_feature()
        target = self.dpi_range.clamp(value) if self.dpi_range else value
        if not dpi_fits(target):
            # Nothing clamps without a range (dpi_range), and a range the mouse lists
            # may start at 0.
            made = "" if target == value else f"its sensor's range and step make it {target}, and "
            raise MouseError(f"DPI {value} is outside what the {self.name} takes: {made}a "
                             f"DPI is {DPI_LOWEST} to {DPI_HIGHEST}")
        level = self._dpi_level(target, stages) if indicate and dpi.stage_level else 0
        try:
            dpi.set(target, level, timeout, retries)
        except HidppError as exc:
            if exc.unreachable or not dpi.host_only or self.get_mode() != MODE_ONBOARD:
                raise
            raise MouseError(dpi_in_host_only(self.name, target, exc)) from None
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

    def step_dpi(self, direction: int, stages: list[int] | None = None,
                 wrap: bool = False) -> int:
        """Move to the next stage up (+1) or down (-1) from the nearest, of `stages`, profile
        1's by default, as sensor.next_stage picks it: at an end it stays, or with `wrap` goes
        round to the other end, as a DPI cycle button does."""
        choices = self.dpi_stages if stages is None else stages
        if not choices:
            raise MouseError(f"the {self.name} has no DPI stages to step through")
        # The DPI each stage lands on, as set_dpi clamps it.
        landed = [self.dpi_range.clamp(c) if self.dpi_range else c for c in choices]
        return self.set_dpi(choices[next_stage(landed, self.get_dpi(), direction, wrap)],
                            stages=stages)

    # -- report rate -----------------------------------------------------------

    #: The rates report_rates gives, cached once read: the bitmap is fixed for the model.
    _rates: list[int] | None = None

    def report_rates(self) -> list[int]:
        """The rates in Hz, highest first, that the mouse lists (SensorRate.rates) and
        Gearbench sets (config.REPORT_RATES), on either feature, so no front end offers a
        rate the daemon refuses; asked once per handle. A mouse with neither feature raises
        MouseError (_rate_feature)."""
        if self._rates is None:
            self._rates = [hz for hz in self._rate_feature().rates() if hz in config.REPORT_RATES]
        return list(self._rates)

    def _rate_feature(self) -> SensorRate:
        if self._rate is None:
            raise MouseError(f"the {self.name} has no adjustable report rate")
        return self._rate

    def get_report_rate(self) -> int | None:
        """The rate in force in Hz, None where 0x8061 answers a bit no source names."""
        return self._rate_feature().get()

    def set_report_rate(self, hz: int) -> str:
        """Set the report rate; returns "onboard" or "live" for where it went.

        The firmware refuses a live 0x8060 write while an onboard profile runs, so in ONBOARD
        mode profile 1's byte is rewritten; the live write is for a mouse left in HOST mode or
        one without 0x8100. The daemon's HOST mode does both (store_report_rate, then
        set_live_report_rate), so profile 1 keeps the rate the next arm sends.
        """
        self.check_rate(hz)
        if self.onboard is not None and self.onboard.get_mode() != MODE_HOST:
            self.store_report_rate(hz)
            return "onboard"
        try:
            self.set_live_report_rate(hz)
        except HidppError as exc:
            if exc.unreachable:
                raise           # a mouse its receiver cannot reach, not a refusal (mouse_call)
            refused = f"the {self.name} refused {hz} Hz live ({exc}); nothing was written"
            if self.onboard is None or self.flash_refusal is not None:
                # A model whose writes are not tested has profile 1 written in no mode.
                raise MouseError(refused) from None
            raise MouseError(f"{refused}; the rate is kept in the onboard profile, which this "
                             f"command writes in onboard mode") from None
        return "live"

    def check_rate(self, hz: int) -> None:
        """Refuse, in words, a rate report_rates does not give, or any rate on a mouse with
        neither 0x8060 nor 0x8061."""
        available = self.report_rates()
        if hz not in available:
            takes = ", ".join(map(str, available)) or f"none of {config.RATE_CHOICES}"
            raise MouseError(f"{hz} Hz is not supported; the {self.name} takes {takes} Hz")

    def store_report_rate(self, hz: int) -> ProfileImage:
        """Rewrite profile 1's rate byte through write_profile, whatever the mode."""
        self.check_rate(hz)
        image = self._edited_profile("rate")
        # check_rate lets through only REPORT_RATES, each of which the profile's byte holds.
        image.report_rate = hz
        return self.write_profile(image)

    def set_live_report_rate(self, hz: int, timeout: float = REQUEST_TIMEOUT,
                             retries: int = REQUEST_RETRIES) -> None:
        """0x8060 fn 2, the interval in ms, or 0x8061 fn 3: in effect at once in HOST mode,
        refused while an onboard profile runs. Not stored, so the daemon sends it at every arm
        of HOST mode."""
        self._rate_feature().set_live(hz, timeout, retries)

    # -- the wheel ---------------------------------------------------------------

    def wheel_mode(self, timeout: float = REQUEST_TIMEOUT,
                   retries: int = REQUEST_RETRIES) -> int | None:
        """0x2121 fn 1: the wheel's mode byte (F_HIRES_WHEEL), or None without 0x2121."""
        if self.wheel is None:
            return None
        return self.dev.request(self.wheel.index, 1, timeout=timeout, retries=retries)[0]

    def set_wheel_mode(self, mode: int, timeout: float = REQUEST_TIMEOUT,
                       retries: int = REQUEST_RETRIES) -> None:
        """0x2121 fn 2, in effect at once."""
        if self.wheel is not None:
            self.dev.request(self.wheel.index, 2, bytes([mode]), timeout=timeout,
                             retries=retries)

    # -- onboard profile -------------------------------------------------------

    def _edited_profile(self, write: UntestedWrite) -> ProfileImage:
        """A fresh copy of profile 1 to edit, read from the mouse now, not `self.profile`: an
        edit rewrites the whole sector, so a stale copy would put back what changed since.
        `write` is the edit, for its refusal on a model whose writes are not tested."""
        self._writable(write)
        image = self._read_profile_one()
        if not image.crc_ok:
            raise MouseError(BAD_CRC)
        return ProfileImage(bytes(image.data))

    def _read_profile_one(self) -> ProfileImage:
        """Sector 0x0001 read now and taken up, bad CRC or not, since `gearbench-ctl mouse` shows
        what it holds. A short read is a MouseError."""
        # Asked of a mouse with 0x8100 alone: an edit is refused without it (_writable), and
        # `flash_unknown` is set only by a write's re-read.
        try:
            image = ProfileImage(cast(OnboardProfiles, self.onboard).read_sector(PROFILE_SECTOR))
        except OnboardError as exc:
            # As in write_profile: uncaught, a short read is an internal error with a traceback.
            raise MouseError(str(exc)) from None
        self._take_sector(image)
        return image

    def _writable(self, write: UntestedWrite) -> OnboardProfiles:
        """The onboard profile feature `write` goes through, refused (write_refusal) before
        profile 1 is read or backed up for it."""
        refused = self.write_refusal(write)
        if refused is not None:
            raise MouseError(refused)
        return cast(OnboardProfiles, self.onboard)

    def write_refusal(self, write: UntestedWrite) -> str | None:
        """Why `write` to profile 1 is refused before anything is read for it, or None: on a
        mouse without onboard profiles, and on a model whose writes are not tested on this
        hardware (MouseModel.writes_tested, not_tested). A caller that reads the mouse before
        the write, as the factory stages' ROM read does, asks it first."""
        if self.onboard is None:
            return NO_ONBOARD.format(self.name)
        if self.model.writes_tested:
            return None
        return not_tested(self.name, write, self.dpi_host_only)

    @property
    def flash_refusal(self) -> str | None:
        """Why every write to profile 1 is refused, with nothing written, for the window to say
        before a click (untested_words), or None on a model whose writes are tested."""
        return None if self.model.writes_tested else untested_words(self.name)

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
        write back (the owner's decision of 2026-10-03). Otherwise the write's HidppFailure is
        re-raised as it came, or the re-read's DeviceGone where that read finds the node gone, so
        the caller can tell a mouse switched off from one that has gone. A write, done or failed,
        that left the sector as it was sets its backup aside (_set_aside_unchanged). Only a
        restore passes `over_bad_crc`: it writes a backup that does not depend on the sector as
        it was.
        """
        # Each caller refused a mouse without the feature first.
        onboard = cast(OnboardProfiles, self.onboard)
        # Its name in _set_aside_unchanged's journal line.
        write = "restore" if over_bad_crc else "write"
        try:
            current = onboard.backup_all(self.backup_name)
        except (OSError, OnboardError, HidppFailure) as exc:
            # A later sector stopped the backup, but sector 0x0001's read worked: what an
            # earlier write left unknown is settled, and _set_aside_unchanged can compare.
            made = onboard.backed_up.get(PROFILE_SECTOR)
            if made is not None:
                self._take_sector(ProfileImage(made[1]))
                self._set_aside_unchanged(onboard, write)
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
            now = onboard.write_sector(PROFILE_SECTOR, written)
        except (OnboardError, HidppFailure) as exc:
            if self._reread_after_failure(onboard) != written:
                # It compares: a sector the failed write changed keeps the backup the newest.
                self._set_aside_unchanged(onboard, write)
                if isinstance(exc, OnboardError):
                    raise MouseError(str(exc)) from None
                raise
            # The cause alone: what follows "; " is the failure's advice, as "try it again",
            # for a write that did not take.
            log.info("the write of sector 0x%04x failed (%s), but it reads back as written",
                     PROFILE_SECTOR, str(exc).partition("; ")[0])
            # _reread_after_failure took up the sector it answered with.
        else:
            self.profile = ProfileImage(now)
        # It compares: a backup that differs from the sector now stays the newest.
        self._set_aside_unchanged(onboard, write)
        return cast(ProfileImage, self.profile)

    def _reread_after_failure(self, onboard: OnboardProfiles) -> bytes | None:
        """Take up what the sector holds now, after a write that failed, and return it. A re-read
        that fails too returns None and keeps the old profile, marked unknown, so `gearbench-ctl
        mouse`, the stage editor and the DPI shift still have one. One that finds the node gone
        raises its DeviceGone, the sector marked unknown as well."""
        # Unknown until the read works (_take_sector).
        self.flash_unknown = True
        try:
            self._take_sector(ProfileImage(now := onboard.read_sector(PROFILE_SECTOR)))
        except DeviceGone:
            # Raised as the write's own failure, it would be worded as a mouse switched off or
            # refusing, not one that went away (Mice.mouse_call).
            raise
        except (OnboardError, HidppFailure):
            return None
        return now

    def enable_buttons(self) -> ProfileImage:
        """Bind the model's buttons to mouse buttons 11 to 16 in profile 1
        (ButtonTable.enable)."""
        # Sent for a model with a table alone (ProfileWrites.no_button_table).
        table = cast(ButtonTable, self.button_table)
        return self.write_profile(table.enable(self._edited_profile("buttons")))

    def write_fallback_buttons(self) -> ProfileImage:
        """Set the model's buttons to their fallback entries in profile 1
        (ButtonTable.fallback)."""
        table = cast(ButtonTable, self.button_table)
        return self.write_profile(table.fallback(self._edited_profile("buttons")))

    def restore_backup(self) -> tuple[ProfileImage, str]:
        """Write the newest backup of profile 1 back. Returns the image and its path. As any
        write (write_profile), one that leaves the sector as it was, failed or done, sets its own
        backup aside (_set_aside_unchanged), if that is a `.bin`: over a bad CRC it is a
        `.bad-read`, which stays."""
        onboard = self._writable("restore")
        path = onboard.newest_backup(self.backup_name, PROFILE_SECTOR)
        if path is None:
            raise MouseError(f"the {self.name} has no backup of profile 1 to restore in "
                             f"{backup_dir()}; see RECOVERY in gearbench-ctl(1)")
        size = onboard.info.sector_size
        try:
            # Capped at the sector's size, which is all a backup of it can hold.
            data = read_capped(path, size)
        except ConfigError as exc:
            raise MouseError(f"{exc}{SET_IT_ASIDE}") from None
        except OSError as exc:
            # The reason alone: an OSError from the open quotes the path again after it.
            raise MouseError(f"could not read {path}: {reason(exc)}{SET_IT_ASIDE}") from None
        if len(data) != size:
            raise MouseError(f"{path} is {counted(len(data), 'byte')}, the sector is "
                             f"{size:,}{SET_IT_ASIDE}")
        # A sector's size holds a profile: __init__ read one from it.
        image = ProfileImage(data)
        if not image.crc_ok:
            raise MouseError(f"{path} fails its CRC; not writing it{SET_IT_ASIDE}")
        return self.write_profile(image, over_bad_crc=True), str(path)

    def _set_aside_unchanged(self, onboard: OnboardProfiles, write: str) -> None:
        """After a write, done or failed, that left the sector as it was, rename the backup it
        made to `<name>.unchanged` (OnboardProfiles.set_aside), when it is a `.bin`. Left the
        newest, it would be what the next restore writes: after a failed restore, the state it
        was to replace (the owner's decision of 2026-09-28); after another write, the bytes the
        sector already holds, so no restore would reach the backup before it (the owner's
        decisions of 2026-10-24 for a write done and of 2026-10-25 for one that failed).
        `write` names it in the journal."""
        # write_profile backed the profile sector up and took it up before each call.
        path, held = onboard.backed_up[PROFILE_SECTOR]
        if self.flash_unknown or cast(ProfileImage, self.profile).data != held:
            return
        try:
            aside = onboard.set_aside(path)
        except OSError as exc:
            log.warning("could not set %s aside (%s); set it aside by hand, or the next "
                        "mouse-restore writes it back", path, reason(exc))
            return
        if aside is None:
            return
        log.info("the %s left sector 0x%04x as it was; its backup of it is now %s, so "
                 "the next restore takes the one before", write, PROFILE_SECTOR, aside)

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
        """Write a new stage list, lowest first whatever the order given. `default` and `shift`
        are DPI values, not indices, snapped as the stages are."""
        image = self._edited_profile("stages")
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
    def dpi_host_only(self) -> bool:
        """Whether its firmware takes a DPI in host mode alone (sensor.ExtendedDpi): outside
        it, its onboard profile sets the DPI, and nothing Gearbench sends does."""
        return self._dpi is not None and self._dpi.host_only

    @property
    def capabilities(self) -> list[str]:
        """What this mouse can do, probed, for a front end to render from; `lighting`, which
        this does not probe, is added by AttachedMouse.capabilities."""
        able = []
        if self._dpi is not None:
            able.append("dpi")
        if self._rate is not None:
            able.append("report_rate")
        if self.onboard is not None:
            able += ["onboard", "dpi_stages"]
        if self.button_table is not None:
            able.append("buttons")
        if self.battery_feature:
            able.append("battery")
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
                out["dpi_range"] = self.dpi_range.reply

        def rate() -> None:
            out["report_rate"] = self.get_report_rate()
            out["report_rates"] = self.report_rates()

        def charge() -> None:
            if battery:
                out["battery"] = read_battery(self.dev, self.model.cell, self.name)

        def mode() -> None:
            out["mode"] = self.get_mode()

        for read in (dpi, rate, charge, mode, self.current_profile, self._reread_unknown_sector):
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

    def _reread_unknown_sector(self) -> None:
        """The read of a sector a failed write left unknown (`flash_unknown`), before describe
        shows it and switch_to_profile_one goes to it; nothing where it is known. A read that
        fails is raised, and the sector stays unknown."""
        if self.flash_unknown:
            self._read_profile_one()
