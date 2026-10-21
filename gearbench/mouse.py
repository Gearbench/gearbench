"""The mice: which are attached, what each can do, and onboard profile 1.

What a mouse can do is probed from its feature table, except where its MouseModel says
otherwise, its buttons (MouseModel.button_table) among them. On the G502 X PLUS G6 to G9 run
firmware functions and emit nothing, and the mouse has no 0x1b04 REPROG_CONTROLS_V4 to divert
them, so `enable_buttons` rewrites the table's buttons in profile 1 to mouse buttons 11 to 16,
which nothing on the desktop uses and the daemon acts on; in HOST mode 0x8110 reports them
instead (spy.py).
Profile 1 is the only profile written, and only on a model whose writes passed their
test on this hardware (WRITES_TESTED); another's are refused. In ONBOARD mode a write
takes effect at its commit (onboard.py), and every write backs up the directory and the
profile sectors first and reads the sector back.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, TypeGuard, cast

from .battery import (
    G502_LIGHTSPEED_CELL,
    G903_HERO_CELL,
    CellCurves,
    has_battery,
    read as read_battery,
)
from .config import MOUSE_LABELS, ConfigError, ModelKeys, backup_dir, entry_under, read_capped
from .hidpp import (
    DeviceGone,
    Feature,
    Hidpp,
    HidppError,
    HidppFailure,
    find_each_attached,
    not_there,
)
from .onboard import (
    DPI_HIGHEST,
    DPI_LOWEST,
    DPI_STAGES,
    ENTRY_FUNCTION,
    F_ONBOARD,
    MODE_HOST,
    MODE_NAMES,
    MODE_ONBOARD,
    NOOP,
    ROM_SECTOR_BASE,
    OnboardError,
    OnboardProfiles,
    ProfileImage,
    dpi_fits,
    entry_mouse_button,
    mouse_button,
    stage_dpi,
)
from .wording import counted, reason

log = logging.getLogger("gearbench")

#: The mouse button each programmable button sends once `enable_buttons` has written it to
#: profile 1, by name (config.MOUSE_BUTTONS): 11 to 16, which nothing on the desktop uses.
#: Mouse button n arrives on evdev as BTN_MOUSE (0x110) + n - 1. The same on every model, so
#: one evdev reader knows every model's (BUTTON_CODES).
ENABLED_BUTTON = {"g4": 11, "g5": 12, "g6": 13, "g7": 16, "g8": 15, "g9": 14}
BUTTON_CODES: dict[int, str] = {0x110 + n - 1: name for name, n in ENABLED_BUTTON.items()}

#: The binding that does a factory entry's job, by the mouse button it sends: back and
#: forward, which the virtual mouse sends.
BUTTON_BINDINGS = {4: "back", 5: "forward"}
#: The same by the firmware function it runs (onboard.FUNCTION_NAMES): the DPI ones, which the
#: daemon runs itself. Any other job, a profile cycle or the battery's lights, has no binding.
FUNCTION_BINDINGS = {0x03: "dpi-up", 0x04: "dpi-down", 0x05: "dpi-cycle", 0x07: "dpi-shift"}


def default_binding(entry: bytes) -> str:
    """The binding that has a button do what `entry` has it do (BUTTON_BINDINGS,
    FUNCTION_BINDINGS), or "none" for a job no binding does."""
    button = entry_mouse_button(entry)
    if button is not None:
        return BUTTON_BINDINGS.get(button, "none")
    if entry[0] == ENTRY_FUNCTION:
        return FUNCTION_BINDINGS.get(entry[1], "none")
    return "none"


@dataclass(frozen=True)
class ButtonSlot:
    """One programmable button of a mouse model, where its onboard profile holds it."""

    #: Its name (config.MOUSE_BUTTONS), which its bindings are filed under on every model.
    name: str
    #: Its entry's index in the profile's button table, counted from 0, which is also its bit
    #: in 0x8110's mask (spy.mask_bit).
    index: int
    #: The entry `write_fallback_buttons` writes there.
    fallback: bytes
    #: What this model calls it, as "G6 (sniper button)" on the G502 HERO.
    label: str


@dataclass(frozen=True)
class ButtonTable:
    """A mouse model's programmable buttons, the one home for them: another model's indices
    would put the wrong entries in the wrong slots."""

    #: The buttons, in display order.
    buttons: tuple[ButtonSlot, ...]
    #: The wheel tilts' onboard indices, each with the way it scrolls: -1 left, +1 right, as
    #: REL_HWHEEL counts. In HOST mode they report in the spy's mask only (spy.py).
    tilts: tuple[tuple[int, int], ...]
    #: The entries in its profile's button table, getInfo's button count: what 0x8110 fn 0 must
    #: answer before HOST mode is armed (Mice.arm_host).
    count: int
    #: Whether its event node is read for these buttons outside HOST mode, as mouse buttons 11
    #: to 16 after `enable_buttons`: its udev rule grants the node to the gearbench group.
    event_node: bool = False

    @property
    def defaults(self) -> dict[str, str]:
        """Each button's binding where a profile holds none of its own: the one that does its
        fallback entry's job (default_binding), else "none"."""
        return {slot.name: default_binding(slot.fallback) for slot in self.buttons}

    def enabled(self, image: ProfileImage) -> list[str]:
        """The buttons whose entry in `image` already sends the mouse button the daemon reads."""
        return [slot.name for slot in self.buttons
                if entry_mouse_button(image.button(slot.index)) == ENABLED_BUTTON[slot.name]]

    def all_enabled(self, image: ProfileImage) -> bool:
        """True when every button sends the mouse button the daemon reads."""
        return len(self.enabled(image)) == len(self.buttons)

    def enable(self, image: ProfileImage) -> ProfileImage:
        """A copy of `image` with each button bound to its mouse button (ENABLED_BUTTON); only
        those entries and the CRC change."""
        edited = ProfileImage(bytes(image.data))
        for slot in self.buttons:
            edited.set_button(slot.index, mouse_button(ENABLED_BUTTON[slot.name]))
        return edited

    def holds_fallback(self, image: ProfileImage) -> bool:
        """True when every button holds its fallback entry."""
        return all(image.button(slot.index) == slot.fallback for slot in self.buttons)

    def state_words(self, image: ProfileImage) -> str:
        """What `image` holds for the buttons, for the journal's profile 1 line."""
        return ("extra buttons enabled" if self.all_enabled(image)
                else "extra buttons set to the fallback" if self.holds_fallback(image)
                else "extra buttons not enabled")

    def fallback(self, image: ProfileImage) -> ProfileImage:
        """A copy of `image` with each button set to its fallback entry; only those entries and
        the CRC change."""
        edited = ProfileImage(bytes(image.data))
        for slot in self.buttons:
            edited.set_button(slot.index, slot.fallback)
        return edited


#: The G502 X PLUS's, each index read off its flash: G4 at 3, the paddle G6 at 4, G5 at 5, G9
#: at 8, G8 at 9 and G7 at 10, and the tilts at 6 and 7, left and right, found on Windows by
#: giving each index its own button number and seen on Linux on 2026-09-25; eleven entries
#: (getInfo). The fallback is the ROM copy's factory entry, except on G9, whose `90 0a`
#: cycles the onboard profiles, and on profile 2 nothing Gearbench writes is in effect. G9 gets
#: the no-op found on Windows, not the empty `ff ff ff ff`: profile 1 holds that at the wheel's
#: indices, 11 and 12, which scroll all the same, so what it does on a control is not known.
G502X_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), MOUSE_LABELS["g4"]),   # back
        ButtonSlot("g5", 5, bytes.fromhex("80 01 00 10"), MOUSE_LABELS["g5"]),   # forward
        ButtonSlot("g6", 4, bytes.fromhex("90 07 00 00"), MOUSE_LABELS["g6"]),   # DPI shift
        ButtonSlot("g7", 10, bytes.fromhex("90 04 00 00"), MOUSE_LABELS["g7"]),  # DPI down
        ButtonSlot("g8", 9, bytes.fromhex("90 03 00 00"), MOUSE_LABELS["g8"]),   # DPI up
        ButtonSlot("g9", 8, NOOP, MOUSE_LABELS["g9"]),
    ),
    tilts=((6, -1), (7, +1)),
    count=11,
    event_node=True,
)
#: The G502 X PLUS's slots on the models with its body and onboard layout, the G502 X, the
#: G502 X LIGHTSPEED and the G502 X PLUS MILLENNIUM FALCON, whose event nodes no udev rule
#: grants. The G502 X's factory copy holds the X PLUS's eleven entries index for index
#: (libratbag issue 1452's log of one unit, getInfo 11 buttons). For the other two G HUB places
#: every button where it places the X PLUS's (its shared package and device database) and no
#: source reads a unit: assumed.
G502X_FAMILY_BUTTONS = replace(G502X_BUTTONS, event_node=False)
#: The G502 HERO's, from its factory copy (sector 0x0101 of the owner's G502 SE HERO, read
#: 2026-10-17, which its profile 1 equals) and G HUB's device package, whose slot gN is index
#: N - 1 and whose drawing puts each marker on its printed button: G4 back at 3, G5 forward at
#: 4, G6, the sniper button, at 5 (DPI shift), G7 DPI down at 6, G8 DPI up at 7, G9 behind the
#: wheel at 8 (profile cycle), and the tilts, right at 9 and left at 10; eleven entries
#: (getInfo). Its function entries end `ff ff` where the X PLUS's end `00 00`, and the fallback
#: keeps them; G9 gets the no-op in place of its profile cycle, as on the X PLUS. Its event
#: node, on interface 00, no rule grants.
G502HERO_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), MOUSE_LABELS["g4"]),   # back
        ButtonSlot("g5", 4, bytes.fromhex("80 01 00 10"), MOUSE_LABELS["g5"]),   # forward
        ButtonSlot("g6", 5, bytes.fromhex("90 07 ff ff"), "G6 (sniper button)"),  # DPI shift
        ButtonSlot("g7", 6, bytes.fromhex("90 04 ff ff"), MOUSE_LABELS["g7"]),   # DPI down
        ButtonSlot("g8", 7, bytes.fromhex("90 03 ff ff"), MOUSE_LABELS["g8"]),   # DPI up
        ButtonSlot("g9", 8, NOOP, MOUSE_LABELS["g9"]),
    ),
    tilts=((10, -1), (9, +1)),
    count=11,
)
#: The G502 LIGHTSPEED's, from its factory copy as Solaar issue 2429 dumped it (getInfo 11
#: buttons): the G502 HERO's entries at the HERO's indices, its function entries ending
#: `00 00`, but for index 8, G9, which shows the battery (`90 0c`), a job the fallback keeps.
#: Its body is the HERO's, so the HERO's labels, assumed: no G HUB package for it was read.
G502_LIGHTSPEED_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), MOUSE_LABELS["g4"]),   # back
        ButtonSlot("g5", 4, bytes.fromhex("80 01 00 10"), MOUSE_LABELS["g5"]),   # forward
        ButtonSlot("g6", 5, bytes.fromhex("90 07 00 00"), "G6 (sniper button)"),  # DPI shift
        ButtonSlot("g7", 6, bytes.fromhex("90 04 00 00"), MOUSE_LABELS["g7"]),   # DPI down
        ButtonSlot("g8", 7, bytes.fromhex("90 03 00 00"), MOUSE_LABELS["g8"]),   # DPI up
        ButtonSlot("g9", 8, bytes.fromhex("90 0c 00 00"), MOUSE_LABELS["g9"]),   # battery
    ),
    tilts=((10, -1), (9, +1)),
    count=11,
)
#: The PRO X SUPERLIGHT's, from its firmware's factory template (0x3b908 in MPM25.01_B0018):
#: mouse buttons 1 to 5 at indices 0 to 4, so its side buttons, back and forward, at 3 and 4,
#: named g4 and g5 by G HUB's slot rule; five entries (getInfo); no wheel tilt.
PRO_X_SUPERLIGHT_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), MOUSE_LABELS["g4"]),   # back
        ButtonSlot("g5", 4, bytes.fromhex("80 01 00 10"), MOUSE_LABELS["g5"]),   # forward
    ),
    tilts=(),
    count=5,
)
#: The PRO WIRELESS's, from its firmware's factory template (0x37987 in MPM15.02_B0026, getInfo
#: 8 buttons): back and forward on the left side at 3 and 4, its DPI button at 5 (DPI cycle),
#: back and forward on the right side at 6 and 7, named by G HUB's slot rule and its device
#: database's side buttons, g4 and g5 left, g7 and g8 right; no wheel tilt.
PRO_WIRELESS_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), "G4 (left side, rear)"),     # back
        ButtonSlot("g5", 4, bytes.fromhex("80 01 00 10"), "G5 (left side, front)"),    # forward
        ButtonSlot("g6", 5, bytes.fromhex("90 05 00 00"), "G6 (DPI button)"),          # DPI cycle
        ButtonSlot("g7", 6, bytes.fromhex("80 01 00 08"), "G7 (right side, rear)"),    # back
        ButtonSlot("g8", 7, bytes.fromhex("80 01 00 10"), "G8 (right side, front)"),   # forward
    ),
    tilts=(),
    count=8,
)
#: The G903 LIGHTSPEED HERO's, from its firmware's factory template (0x39243 in
#: MPM23.01_B0013, getInfo 12 buttons): back and forward on the left side at 3 and 4 and on
#: the right side at 5 and 6, DPI down at 7, DPI up at 8, the tilts, left at 9 and right at 10,
#: and the profile cycle at 11, named by G HUB's slot rule and its device database's side
#: buttons, g4 and g5 left, g6 and g7 right; the profile cycle's slot, g12, has no Gearbench
#: name and keeps its job. Which printed label each DPI button has is not read: the first
#: report checks it.
G903_HERO_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), "G4 (left side, rear)"),     # back
        ButtonSlot("g5", 4, bytes.fromhex("80 01 00 10"), "G5 (left side, front)"),    # forward
        ButtonSlot("g6", 5, bytes.fromhex("80 01 00 08"), "G6 (right side, rear)"),    # back
        ButtonSlot("g7", 6, bytes.fromhex("80 01 00 10"), "G7 (right side, front)"),   # forward
        ButtonSlot("g8", 7, bytes.fromhex("90 04 00 00"), "G8 (DPI down)"),
        ButtonSlot("g9", 8, bytes.fromhex("90 03 00 00"), "G9 (DPI up)"),
    ),
    tilts=((9, -1), (10, +1)),
    count=12,
)


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
    #: Its programmable buttons (ButtonTable), or None: a model without one reports no
    #: `buttons` capability, so the window shows no button section.
    button_table: ButtonTable | None = None
    #: Whether HOST mode drives it while `mouse_host_mode` is on, its 0x8110 spy built from
    #: `button_table` (spy.SPY_MODELS); otherwise it runs onboard profile 1.
    host_mode: bool = False
    #: Whether its 0x0020 ConfigChange cookie is trusted (daemon.mice's MOUSE_COOKIE). The G305
    #: has no 0x0020: any other model is configured again at every recheck it answers. A power-on
    #: report or a resume configures in full whatever the cookie says.
    cookie: bool = False
    #: The USB interface its HID++ collection is on, cabled: the only one of its nodes opened
    #: (find_attached). 02 on the G502 X PLUS, as on the keyboard, where 00 is the pointer and
    #: 01 a keyboard collection. A receiver gives its mouse a node with no interface, matched on
    #: the HID id alone.
    interface: int = 2
    #: Whether its onboard profile writes passed their test on this hardware. Any other
    #: model's are refused with nothing written (not_tested, the owner's decision of
    #: 2026-10-17).
    writes_tested: bool = False
    #: Whether its lighting is 0x8070's zones (mouse_lighting.ZoneLighting), which the daemon
    #: drives on such a model alone: the G305 lists 0x8070 and has no lights.
    zone_lighting: bool = False
    #: Whether it claims its lighting only once there is a color to write, not at its setup,
    #: and is released once at its attach with nothing to write (WAITS_TO_CLAIM): each
    #: model with zone lighting (the owner's decision of 2026-10-18, "Wait to claim") and the
    #: G903 LIGHTSPEED HERO ("Wait for a color").
    waits_to_claim: bool = False
    #: Whether its lighting writes passed their test on this hardware: until they do,
    #: `status` says its lighting is under test (lighting_under_test).
    lighting_tested: bool = False
    #: Its own names for effect ids, (id, name) pairs, in place of lighting.EFFECTS', the G502
    #: X PLUS's (EFFECT_NAMES): what its lighting lists and `mouse_effect` takes.
    effect_names: tuple[tuple[int, str], ...] = ()
    #: Whether its 0x8070 stores a zone effect written while the host does not hold the
    #: lighting, and lets go of that hold at a power-state change, so each effect write goes
    #: right after a claim (mouse_lighting.ZoneLighting): one claim at its setup would leave
    #: the writes after its next sleep stored. Read from the PRO WIRELESS's firmware image on
    #: 2026-10-18, and taken for the G502 LIGHTSPEED, whose 0x8070 answers the same; not yet
    #: checked on a mouse.
    claim_each_effect: bool = False
    #: Whether Gearbench has run on one on the hardware. Until it has, the front ends and the
    #: journal ask for a device report from it (report.untested).
    tested: bool = False
    #: The cell its 0x1001 millivolts are read against for a percentage, or None: then it
    #: reports volts and no percentage, since another cell's curve would give a wrong number.
    cell: CellCurves | None = None


G502X = MouseModel("G502 X PLUS", "g502x", (0xFFFFFF, 0xFF6400, 0x28FFDC, 0xFFFF14, 0xDC14FF),
                   button_table=G502X_BUTTONS, host_mode=True, cookie=True, writes_tested=True,
                   lighting_tested=True, tested=True)
G305 = MouseModel("G305", "g305", (0xF8E71C, 0xFFFFFF, 0xFD7600, 0xE9168C, 0x00A9E0),
                  writes_tested=True, tested=True)
#: Cabled alone. G HUB's device database gives it no DPI indicator colors, and two zones,
#: primary and logo: on the owner's G502 SE HERO the DPI stage lights, lit on the light of
#: the stage in use alone, and the logo (watched 2026-10-18). Its HID++ collection is on
#: interface 01, beside the mouse's own key reports (Solaar's descriptors, `interface=1`;
#: libratbag's logs read report ids 01, 03, 04, 10 and 11 on that node). Its lighting passed
#: its replug check on 2026-10-18 (the owner's decision of 2026-10-18 sets lighting_tested).
#: HOST mode, its button spy at index 0x0d: its image (127.03) keeps the mode in RAM, and on
#: leaving it reloads the running profile and writes that profile's two lighting records,
#: unchanged, into its settings memory, as each switch to ONBOARD mode at an attach already
#: has it do. The owner's decision of 2026-10-18, "Accept it", takes that write; the spy's
#: masks are not yet watched on a HERO. Its onboard profile writes are let through for their
#: hardware test, the owner's decision of 2026-10-21; its event node is not read, so its
#: enable write is refused (AttachedMouse.enable_refused).
G502HERO = MouseModel("G502 HERO", "g502hero", (), button_table=G502HERO_BUTTONS,
                      host_mode=True, interface=1, zone_lighting=True, waits_to_claim=True,
                      writes_tested=True, lighting_tested=True, tested=True)
#: The G502 X PLUS without lighting: G HUB's device database gives it the X PLUS's
#: capabilities and DPI indicator colors less lighting, and Solaar's listing of it (issue
#: 2560) the X PLUS's features at the same versions, less 0x8071, 0x8081 and 0x00D0. No source
#: gives its button count or factory button entries, so its buttons are the X PLUS's, assumed,
#: and its cookie is not trusted until a unit is read. Its cabled interface, 02, is the X
#: PLUS's, assumed. HOST mode as on the X PLUS, whose 0x8100 and 0x8110 versions Solaar lists
#: for it; that the mode write is volatile here too is assumed.
G502X_LIGHTSPEED = MouseModel("G502 X LIGHTSPEED", "g502xls", G502X.stage_colors,
                              button_table=G502X_FAMILY_BUTTONS, host_mode=True)
#: Five standard buttons, and no lighting but the DPI indicator its firmware owns. Its 0x8070
#: is hidden and flagged for engineering in its firmware's feature table (25.01.B0018), and
#: answered Solaar with error 5, so the model is kept out of ZONE_LIGHTING and the daemon
#: never asks it; the device report reads it, as it reads any 0x8070. HOST mode for its back
#: and forward buttons and a report rate set live, which its firmware refuses in ONBOARD mode
#: (the owner's decision of 2026-10-18, "Yes, host mode"): its image keeps the mode in RAM.
PRO_X_SUPERLIGHT = MouseModel("PRO X SUPERLIGHT", "proxsuperlight",
                              (0xFFFF00, 0xFFFFFF, 0xFF9900, 0xFF0293, 0x8D40FF),
                              button_table=PRO_X_SUPERLIGHT_BUTTONS, host_mode=True)
#: Cabled alone: the G502 X PLUS's body and onboard layout on the G502 HERO's route, its HID++
#: on interface 01 beside the mouse's keyboard, consumer and system reports, under hid-generic
#: (Solaar issue 1827's descriptor; libratbag issue 1452's log). Profile format 4, where the X
#: PLUS's is 5, so a backup prefix of its own; no lights and no battery. Its factory copy holds
#: the X PLUS's eleven entries (one unit's log). HOST mode as on the X PLUS, whose 0x8100 and
#: 0x8110 versions both listings give it; that the mode write is volatile here too is assumed.
G502X_WIRED = MouseModel("G502 X", "g502xwired", G502X.stage_colors,
                         button_table=G502X_FAMILY_BUTTONS, host_mode=True, interface=1)
#: A licensed edition of the G502 X PLUS with ids and a firmware line of its own (MPM 35.00,
#: the cable's USB configuration string in one published probe). G HUB's device database gives
#: it the X PLUS's capabilities, zones, effects and DPI indicator colors, and no source reads
#: its feature table, so all of it is the X PLUS's, assumed: lighting through 0x8071, as the X
#: PLUS's and as volatile, under test; the X PLUS's buttons; no trusted cookie until a unit is
#: read; its cable's three interfaces are the X PLUS's layout by that probe's lsusb. G HUB
#: names its effects 0x0F and 0x10, the X PLUS's two signature effects, for its own
#: animations: "Millennium Falcon" both, the second the power-saving one (its strings
#: package's SIGNATURE_EFFECT_G502X_WL_FRAME_ACTIVE and _INACTIVE, read 2026-10-18). HOST mode
#: as on the X PLUS, assumed.
G502X_FALCON = MouseModel("G502 X PLUS MILLENNIUM FALCON", "g502xfalcon", G502X.stage_colors,
                          button_table=G502X_FAMILY_BUTTONS, host_mode=True,
                          effect_names=((0x0F, "millennium-falcon"),
                                        (0x10, "millennium-falcon-inactive")))
#: The G502 HERO's body on a LIGHTSPEED 1 receiver, and on its cable. G HUB's device database
#: gives it no DPI indicator colors and two zones, primary and logo; its 0x8070 answers as the
#: PRO WIRELESS's does, byte for byte (libratbag issue 1089), so it is taken to store an
#: effect as that mouse's firmware does. No source names its cable's HID++ interface: 02 is
#: assumed, the PRO WIRELESS's and the G903 LIGHTSPEED HERO's. HOST mode, its layout settled
#: by Solaar issue 2429 (the owner's decision of 2026-10-18: on, as untested); no image of
#: its firmware was read, so what leaving HOST mode writes is its first report's to say.
G502_LIGHTSPEED = MouseModel("G502 LIGHTSPEED", "g502ls", (),
                             button_table=G502_LIGHTSPEED_BUTTONS, host_mode=True,
                             zone_lighting=True, waits_to_claim=True, claim_each_effect=True,
                             cell=G502_LIGHTSPEED_CELL)
#: An ambidextrous mouse on a LIGHTSPEED 1 receiver, with two zones, primary and logo, and no
#: DPI indicator colors in G HUB's device database. Its firmware image (MPM15.02_B0026) stores
#: a zone effect written outside host control, and gives its cable's vendor collection
#: interface 02. HOST mode, its button spy at index 0x0a, as untested (the owner's decision of
#: 2026-10-18, "Per model", which unblocked it): its image keeps the mode in RAM and reloads
#: the running profile on leaving it, and each model's buttons bind on their own, so its DPI
#: button keeps its job, dpi-cycle, and its right side's back and forward theirs.
PRO_WIRELESS = MouseModel("PRO WIRELESS", "prowireless", (), button_table=PRO_WIRELESS_BUTTONS,
                          host_mode=True, zone_lighting=True, waits_to_claim=True,
                          claim_each_effect=True, cell=G502_LIGHTSPEED_CELL)
#: Not the older G903 (046d:c086 cabled, 4067 through its receiver), which G HUB calls G903 and
#: this model G903 LIGHTSPEED; the name adds HERO, as the device's own does. Its lighting is
#: 0x8071's two clusters with no 0x8081, and no G HUB DPI indicator colors. It waits to claim
#: its lighting, whose claim hands the host its power modes too, so a mouse given no color
#: keeps its own power saving (the owner's decision of 2026-10-18, "Wait for a color"). Its
#: firmware image (MPM23.01_B0013) gives its cable's vendor collection interface 02. HOST mode,
#: its button spy at index 0x09, as untested, as on the PRO WIRELESS: its image keeps the mode
#: in RAM. Its profile button, entry 11, reports in the spy's mask alone there and has no
#: binding, since no onboard profile runs in HOST mode to cycle.
G903_HERO = MouseModel("G903 LIGHTSPEED HERO", "g903hero", (), button_table=G903_HERO_BUTTONS,
                       host_mode=True, waits_to_claim=True, cell=G903_HERO_CELL)

#: Each product id's model, tried in this order, wireless first: what a mouse falls back to
#: when its cable is pulled. The G502 X PLUS is 0x4099 through its receiver (device index 0x01)
#: and 0xC095 cabled (0xFF); the G305 is 0x4074 through its receiver. Read off this hardware on
#: 2026-09-22, and the G502 HERO's 0xC08B off a G502 SE HERO on 2026-10-17. The G502 X
#: LIGHTSPEED's and the PRO X SUPERLIGHT's are G HUB's device database's and Solaar's, each
#: through a c547 receiver and on its cable, and the G502 X's 0xC099, cabled alone, theirs too;
#: the G502 X PLUS MILLENNIUM FALCON's 0x40B2 and 0xC09E are G HUB's device database's alone;
#: the G502 LIGHTSPEED's, the PRO WIRELESS's and the G903 LIGHTSPEED HERO's are G HUB's and
#: Solaar's, through a c539 receiver and on its cable; none yet read off this hardware.
MODEL_OF: dict[int, MouseModel] = {0x4099: G502X, 0xC095: G502X, 0x4074: G305,
                                   0xC08B: G502HERO,
                                   0x409F: G502X_LIGHTSPEED, 0xC098: G502X_LIGHTSPEED,
                                   0x4093: PRO_X_SUPERLIGHT, 0xC094: PRO_X_SUPERLIGHT,
                                   0xC099: G502X_WIRED,
                                   0x40B2: G502X_FALCON, 0xC09E: G502X_FALCON,
                                   0x407F: G502_LIGHTSPEED, 0xC08D: G502_LIGHTSPEED,
                                   0x4079: PRO_WIRELESS, 0xC088: PRO_WIRELESS,
                                   0x4087: G903_HERO, 0xC091: G903_HERO}
PRODUCT_IDS = tuple(MODEL_OF)
# MODEL_OF's fields by product id, as the callers look them up.
MODELS = {product: model.name for product, model in MODEL_OF.items()}
BACKUP_NAMES = {product: model.backup_name for product, model in MODEL_OF.items()}
STAGE_COLORS = {product: model.stage_colors for product, model in MODEL_OF.items()}
COOKIE_MODELS = frozenset(product for product, model in MODEL_OF.items() if model.cookie)
WRITES_TESTED = frozenset(product for product, model in MODEL_OF.items()
                          if model.writes_tested)
ZONE_LIGHTING = frozenset(product for product, model in MODEL_OF.items()
                          if model.zone_lighting)
WAITS_TO_CLAIM = frozenset(product for product, model in MODEL_OF.items()
                           if model.waits_to_claim)
LIGHTING_TESTED = frozenset(product for product, model in MODEL_OF.items()
                            if model.lighting_tested)
EFFECT_NAMES = {product: dict(model.effect_names) for product, model in MODEL_OF.items()
                if model.effect_names}
CLAIM_EACH_EFFECT = frozenset(product for product, model in MODEL_OF.items()
                              if model.claim_each_effect)
CELLS = {product: model.cell for product, model in MODEL_OF.items() if model.cell is not None}

#: The names a model's bindings are kept under in config.json and a game profile's file (the
#: owner's decision of 2026-10-18, "Per model"), as keyboard.KEYING names a keyboard model: its
#: product ids, by the first, its key, "4099" for the G502 X PLUS on its receiver and its
#: cable, so a c095 written by hand is the X PLUS's.
KEYING = ModelKeys(MODEL_OF)
MODEL_IDS = KEYING.ids
key_of, model_key, model_ids, model_named = (KEYING.key_of, KEYING.model_key, KEYING.model_ids,
                                             KEYING.model_named)


def key_name(key: str) -> str:
    """The mouse model a file names `key`, one of its product ids, by name, for a line or a
    label: "mouse c999" for an id no model has."""
    model = MODEL_OF.get(int(key, 16))
    return f"mouse {key}" if model is None else model.name


def bindings_of(entries: Mapping[str, Mapping[str, str]], key: str) -> dict[str, str]:
    """What the buttons of the mouse model named `key` are bound to in a profile's `entries`
    (config.Profile.mouse), in its table's order: each its own binding there, under any of
    the model's ids, else its default (ButtonTable.defaults). Empty for a model with no
    table and for any id that is not a model's key, another of a model's ids included."""
    model = model_named(key)
    table = model.button_table if model is not None else None
    if table is None:
        return {}
    own = entry_under(entries, model_ids(key)) or {}
    return {name: own.get(name, default) for name, default in table.defaults.items()}


#: The ids a mouse reports over its cable; the others are its receiver's.
CABLED_IDS = frozenset({0xC095, 0xC08B,
                        0xC098,
                        0xC094,
                        0xC099,
                        0xC09E,
                        0xC08D,
                        0xC088,
                        0xC091})

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
#: four, then the PRO X SUPERLIGHT's other four. A color added above with no name here gets a
#: dot that names none.
STAGE_COLOR_NAMES = {
    0xFFFFFF: "white", 0xFF6400: "orange", 0x28FFDC: "cyan", 0xFFFF14: "yellow",
    0xDC14FF: "purple",
    0xF8E71C: "yellow", 0xFD7600: "orange", 0xE9168C: "pink", 0x00A9E0: "blue",
    0xFFFF00: "yellow", 0xFF9900: "orange", 0xFF0293: "pink", 0x8D40FF: "purple",
}

#: The widest DPI range of a supported sensor, (lowest, highest): the G502 X PLUS's. The
#: G305's starts at 200. A DPI under the lowest is a stage's number, which the daemon refuses
#: too.
WIDEST_DPI_RANGE = (100, 25600)
#: How long a front end waits for a command that can write onboard profile 1 to the mouse's
#: flash, in seconds, past send_command's 3 s.
PROFILE_WRITE_WAIT = 30.0


def profile_words(sector: int) -> str:
    """getCurrentProfile's value, the sector the mouse runs, in words."""
    if sector == 0:
        return "no onboard profile (host mode)"
    if sector >= ROM_SECTOR_BASE:
        return f"the factory profile at sector 0x{sector:04x}"
    return f"onboard profile {sector}"


def profile_jobs(table: dict | None) -> str:
    """Each button of a `mouse` reply's `button_table` and its job in profile 1, as "G4 back,
    G5 forward", or "" where none was read: the words for a model whose writes are refused,
    whose buttons need not be the G502 X PLUS's G4 to G9."""
    return ", ".join(f"{str(b.get('name')).upper()} {b['onboard']}"
                     for b in (table or {}).get("buttons") or [] if b.get("onboard"))


def runs_another_profile(running: int | None) -> TypeGuard[int]:
    """Whether `running`, Mouse.running_profile, is a profile other than 1, where nothing
    written to profile 1 is in effect. None, before a read, is not."""
    return running is not None and running != PROFILE_SECTOR


class HostState(enum.Enum):
    """Where a `mouse` reply's `host` puts the mouse (host_state)."""

    #: In HOST mode with the button spy armed: Gearbench drives its programmable buttons.
    DRIVEN = enum.auto()
    #: Counted in HOST mode with host mode off or its arm refused, and answering: stuck there
    #: with its programmable buttons dead, `problems`' mouse_left_in_host.
    LEFT_THERE = enum.auto()
    #: The same for a mouse not answering, whose switch waits for it.
    UNTIL_ANSWERED = enum.auto()
    #: Counted in HOST mode with host mode on, not answering, and its last arm not run to its
    #: end: its setup waits for it.
    NOT_ARMED = enum.auto()
    #: Outside HOST mode, without a measured button spy.
    NO_HOST = enum.auto()
    #: Outside HOST mode, with host mode switched off.
    SWITCHED_OFF = enum.auto()
    #: Outside HOST mode, its arm refused.
    REFUSED = enum.auto()
    #: Outside HOST mode with host mode on, before its setup: a mouse that has not answered.
    NOT_YET = enum.auto()


def host_state(host: Mapping[str, object], answering: bool) -> HostState:
    """Where a `mouse` reply's `host` (wanted, capable, active, error, armed) puts the mouse,
    for the CLI and the window alike; `answering` False for a mouse the daemon marks silent."""
    if host.get("active"):
        if host.get("wanted") and not host.get("error"):
            # `armed` False: an arm or a hand-back cut short, or no arm yet since its attach.
            if not answering and host.get("armed") is False:
                return HostState.NOT_ARMED
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
    return (f"{'their' if many else 'its'} programmable buttons do nothing. Switch "
            f"{'each mouse' if many else 'the mouse'} off and on to try again")


def stuck_in_host(names: list[str], why: str | None = None) -> str:
    """`problems`' mouse_left_in_host and mouse_host_mode's note: the mice `names` counted in
    HOST mode with host mode off or refused, and `why` when it is one mouse's."""
    many = len(names) > 1
    return (f"{' and '.join(names)} {'are' if many else 'is'} stuck in host mode"
            + (f" ({why})" if why else "") + ": " + stuck_words(many))


def buttons_idle_words(name: str, host_mode: bool) -> str:
    """Why the bindings of `name`'s buttons do nothing now, for the Buttons group and
    `gearbench-ctl mouse`, which give each button's onboard job under it, on a mouse whose
    event node is not read: `host_mode` False for a mouse HOST mode cannot drive, its model
    without host mode (MouseModel.host_mode) or the mouse without a button spy
    (AttachedMouse.spy), True for one outside HOST mode."""
    if not host_mode:
        return f"Gearbench cannot read the {name}'s buttons"
    return f"the {name}'s bindings work in host mode only"


def live_rate_note(name: str) -> str:
    """`mouse_rate`'s note on a mouse in HOST mode whose onboard profile is not written (Mouse
    .flash_refusal): the rate went live alone, and is kept for its model (Dpi.remember_rate)."""
    return (f"Set live on the {name}, and again each time Gearbench sets it up in host mode. "
            f"Its onboard profile, which Gearbench does not write yet, keeps its own rate for "
            f"when Gearbench stops.")


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


#: What to do about a profile 1 whose CRC fails. "Before switching the mouse off": what the
#: mouse does at power-on with such a sector is not known.
WRITE_A_BACKUP = ("Write the newest good backup over it before switching the mouse off: "
                  "Restore profile backup… (or `gearbench-ctl mouse-restore`); see RECOVERY in "
                  "gearbench-ctl(1)")
#: The refusal to write over a profile 1 whose CRC fails.
BAD_CRC = f"onboard profile 1 has a bad CRC, so nothing is written over it. {WRITE_A_BACKUP}."
#: Why a mouse on another profile is not switched to a profile 1 whose CRC fails, and what to do
#: (Mouse.switch_refusal).
NOT_SWITCHED = f"onboard profile 1 has a bad CRC, so the mouse is not switched to it. {WRITE_A_BACKUP}"


def untested_words(name: str) -> str:
    """Why every write to the onboard profile of a model whose writes are not tested on this
    hardware (MouseModel.writes_tested) is refused: the window says it before a click, and
    not_tested after one."""
    return (f"Gearbench does not write the {name}'s onboard profile until that is tested on "
            f"this hardware")


#: The writes to the onboard profile that not_tested refuses, each with what holds in its
#: place, said after the reason. The rate's is refused outside host mode alone: in host mode
#: the daemon sets the rate live on such a model (AttachedMouse.sets_rate_live), and every
#: model whose writes are not tested has host mode.
UntestedWrite = Literal["rate", "stages", "buttons", "restore"]
UNTESTED_KEEPS: dict[UntestedWrite, str] = {
    "rate": "its report rate stays as that profile holds it; in host mode it is set live",
    "stages": ("its DPI stages stay as that profile holds them. Its DPI is set live with "
               "`gearbench-ctl mouse-dpi` or the Mouse tab's DPI row"),
    "buttons": "outside host mode, and without Gearbench, its buttons do what that profile holds",
    "restore": "that profile stays as the mouse holds it",
}


def not_tested(name: str, write: UntestedWrite) -> str:
    """The refusal of `write` to the onboard profile of a model whose writes are not tested on
    this hardware (MouseModel.writes_tested)."""
    return f"nothing was written: {untested_words(name)}, so {UNTESTED_KEEPS[write]}."


def lighting_under_test(name: str) -> str:
    """`status`'s words for an attached mouse whose lighting is driven before its writes
    passed their test on this hardware (MouseModel.lighting_tested)."""
    return (f"the {name}'s lighting is under test: whether a color sent to it stays after a "
            f"replug is not yet checked on this hardware")


#: Whose lighting a mouse that waits to claim its lighting shows (AttachedMouse.lighting_shown,
#: `mouse`'s `lighting_shown` and `status`'s `mouse_lighting_shown`): its own, or Gearbench's.
OWN_LIGHTING, GEARBENCH_LIGHTING = "own", "gearbench"


def lighting_shown_words(name: str, shown: str) -> str:
    """`status`'s words for whose lighting an attached mouse that waits to claim its lighting
    shows: `shown` is OWN_LIGHTING or GEARBENCH_LIGHTING."""
    if shown == OWN_LIGHTING:
        return (f"the {name} shows its own lighting until Gearbench sends it a color or an "
                f"effect, set for it or synced with the keyboard")
    return f"the {name} shows the lighting Gearbench sends it"


def not_switched_untested(crc_ok: bool) -> str:
    """Why a model whose writes are not tested is not switched to profile 1: whether the
    switch persists on it is not known, so it is treated as a write that may (the owner's rule
    of 2026-10-17). `crc_ok` False adds what profile 1's bad CRC means, with no restore offered,
    the words for a blank one, as on a mouse G HUB never wrote."""
    said = "Gearbench does not switch it until that is tested on this hardware"
    return said if crc_ok else (f"{said}. Its profile 1 fails its CRC, as a blank one does, "
                                f"and nothing needs doing")


def switch_refusal_words(writes_tested: bool, crc_ok: bool) -> str | None:
    """Why a mouse is not to be switched to profile 1, and what to do, or None: a model whose
    writes are not tested (not_switched_untested), or profile 1 as last read failing its CRC,
    since what a mouse runs from such a sector is not known (the owner's decision of
    2026-10-17: that switch is never sent)."""
    if not writes_tested:
        return not_switched_untested(crc_ok)
    return None if crc_ok else NOT_SWITCHED


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


class SwitchRefused(MouseError):
    """A switch to profile 1 not sent, in words that say what to do (Mouse.switch_refusal)."""


def other_profile_words(name: str, running: int, refused: str | None, error: str | None,
                        silent: bool) -> str:
    """`problems`' mouse_other_profile: `name`, the mouse as `problems` names it, runs
    `running`, a profile other than 1. `refused` is Mouse.switch_refusal's words, asked at each
    `problems`, so a restore that mends profile 1 ends them; `error` why the last switch did
    not take, or None."""
    said = f"{name} runs {profile_words(running)}, not profile 1"
    if refused is not None and running:
        # No switch goes to a mouse in host mode (0), so its words are the ones below.
        return f"{said}: {refused}"
    # One not answering is set up in full once it answers (tend_mice), the switch with it; one
    # left in host mode (0) stays there until a power-on.
    again = ("it switches back when the mouse is next switched on" if error is None
             else f"{error}. Gearbench tries again once the mouse answers" if silent and running
             else f"{error}. Switch the mouse off and on to try again")
    return f"{said}, so what profile 1 holds is not in effect; {again}"


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


def dpi_range(reply: bytes) -> DpiRange | None:
    """Decode getSensorDpiList's reply: 16-bit values after the sensor index, where one with
    the top bits 0xe000 is a range marker carrying the step, between the minimum and the
    maximum; None for a list with no usable value."""
    values = [int.from_bytes(reply[i:i + 2], "big") for i in range(1, 15, 2)]
    for i, value in enumerate(values):
        if value & 0xE000 == 0xE000 and i and i + 1 < len(values):
            return DpiRange(values[i - 1], values[i + 1], value & 0x1FFF)
    usable = [v for v in values if v and v & 0xE000 != 0xE000]
    return DpiRange(min(usable), max(usable), 1) if usable else None


def report_rates(bitmap: int) -> list[int]:
    """The rates in Hz, highest first, that getReportRateList's bitmap of intervals lists: bit
    n set means n+1 ms is supported."""
    return [1000 // (bit + 1) for bit in range(8) if bitmap >> bit & 1]


def find_attached_mice() -> list[tuple[Path, int, int]]:
    """Every known mouse attached now, in PRODUCT_IDS order. Two mice on separate receivers
    each have a hidraw node with its own product id (measured 2026-09-22)."""
    return find_each_attached({product: MODEL_OF[product].interface
                               for product in PRODUCT_IDS})


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
        #: Which model answered (MODEL_OF), for what its feature table cannot say.
        self.product = product
        self.name = MODELS.get(product, f"Logitech {product:04x}")
        model = MODEL_OF.get(product)
        #: Its model's programmable buttons, or None (MouseModel.button_table).
        self.button_table = model.button_table if model is not None else None
        self.backup_name = backup_name(product)
        self._dpi = dev.feature(F_DPI)
        #: The level byte the last set_dpi sent; the daemon repaints after a nonzero one.
        self.dpi_level_sent = 0
        self._rate = dev.feature(F_REPORT_RATE)
        #: 0x2121, or None; read by Mice.keep_wheel.
        self.wheel = dev.feature(F_HIRES_WHEEL)
        #: Whether its feature table lists a battery feature (battery.has_battery): the wired
        #: G502 HERO and G502 X list none.
        self.battery_feature = has_battery(dev)
        # Sensor 0's getSensorDpiList.
        self.dpi_range = (dpi_range(dev.request(self._dpi.index, 1, bytes([0x00]), long=True))
                          if self._dpi is not None else None)
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
        """0x8100 fn 1: `01` ONBOARD, `02` HOST. It writes no flash: measured on the G502 X
        PLUS, which a power cycle takes out of HOST mode."""
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
        2026-09-24), so the caller puts a remembered DPI back. SwitchRefused, with nothing sent,
        where switch_refusal has a reason."""
        if self.onboard is None:
            return None
        refused = self.switch_refusal()
        if refused is not None:
            raise SwitchRefused(refused)
        self.onboard.set_current_profile(PROFILE_SECTOR, timeout=timeout, retries=retries)
        return self.current_profile(timeout, retries)

    def switch_refusal(self) -> str | None:
        """switch_refusal_words for this mouse: why it is not to be switched to profile 1, and
        what to do, or None."""
        # Asked of a mouse with 0x8100 alone, whose profile 1 __init__ read.
        return switch_refusal_words(self.product in WRITES_TESTED,
                                    cast(ProfileImage, self.profile).crc_ok)

    # -- DPI -----------------------------------------------------------------

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
            # Nothing clamps without a range (dpi_range), and a range the mouse lists
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

    def step_dpi(self, direction: int, stages: list[int] | None = None,
                 wrap: bool = False) -> int:
        """Move to the next stage up (+1) or down (-1) from the nearest, of
        `stages`, profile 1's by default: at an end it stays, or with `wrap` goes round to the
        other end, as a DPI cycle button does."""
        choices = self.dpi_stages if stages is None else stages
        if not choices:
            raise MouseError(f"the {self.name} has no DPI stages to step through")
        current = self.get_dpi()
        nearest = min(range(len(choices)), key=lambda i: abs(choices[i] - current))
        step = nearest + direction
        target = step % len(choices) if wrap else max(0, min(len(choices) - 1, step))
        return self.set_dpi(choices[target], stages=stages)

    # -- report rate -----------------------------------------------------------

    #: The rates the mouse lists, cached once read: the bitmap is fixed for the model.
    _rates: list[int] | None = None

    def report_rates(self) -> list[int]:
        """Supported rates in Hz, highest first (report_rates), asked once per handle."""
        if self._rate is None:
            return []
        if self._rates is None:
            self._rates = report_rates(self.dev.request(self._rate.index, 0)[0])
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
            if self.onboard is None:
                raise MouseError(refused) from None
            raise MouseError(f"{refused}. The rate is kept in the onboard profile, which this "
                             f"command writes in onboard mode") from None
        return "live"

    def check_rate(self, hz: int) -> None:
        """Refuse a rate the mouse does not list, or one on a mouse without 0x8060, in words."""
        self._rate_feature()
        available = self.report_rates()
        if hz not in available:
            raise MouseError(f"{hz} Hz is not supported; this mouse takes "
                             f"{', '.join(map(str, available))} Hz")

    def store_report_rate(self, hz: int) -> ProfileImage:
        """Rewrite profile 1's rate byte through write_profile, whatever the mode."""
        self.check_rate(hz)
        image = self._edited_profile("rate")
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

    def _edited_profile(self, write: UntestedWrite) -> ProfileImage:
        """A fresh copy of profile 1 to edit, read from the mouse now, not `self.profile`: an
        edit rewrites the whole sector, so a stale copy would put back what changed since.
        `write` is the edit, for its refusal on a model whose writes are not tested."""
        onboard = self._writable(write)
        try:
            image = ProfileImage(onboard.read_sector(PROFILE_SECTOR))
        except OnboardError as exc:
            # As in write_profile: uncaught, a short read is an internal error with a traceback.
            raise MouseError(str(exc)) from None
        # What the sector holds now, bad or not: `gearbench-ctl mouse` shows it.
        self._take_sector(image)
        if not image.crc_ok:
            raise MouseError(BAD_CRC)
        return ProfileImage(bytes(image.data))

    def _writable(self, write: UntestedWrite) -> OnboardProfiles:
        """The onboard profile feature `write` goes through, refused on a mouse without one and
        on a model whose writes are not tested on this hardware (WRITES_TESTED), before profile
        1 is read or backed up for it."""
        if self.onboard is None:
            raise MouseError("this mouse has no onboard profile feature")
        if self.product not in WRITES_TESTED:
            raise MouseError(not_tested(self.name, write))
        return self.onboard

    @property
    def flash_refusal(self) -> str | None:
        """Why every write to profile 1 is refused, with nothing written, for the window to say
        before a click (untested_words), or None on a model whose writes are tested."""
        return None if self.product in WRITES_TESTED else untested_words(self.name)

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
        # Each caller refused a mouse without the feature first.
        onboard = cast(OnboardProfiles, self.onboard)
        try:
            current = onboard.backup_all(self.backup_name)
        except (OSError, OnboardError, HidppFailure) as exc:
            # A later sector stopped the backup, but sector 0x0001's read worked: what an
            # earlier write left unknown is settled, and _set_aside_unchanged can compare.
            made = onboard.backed_up.get(PROFILE_SECTOR)
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
            now = onboard.write_sector(PROFILE_SECTOR, written)
        except (OnboardError, HidppFailure) as exc:
            if self._reread_after_failure(onboard) != written:
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
        """Write the newest backup of profile 1 back. Returns the image and its path. One that
        fails with the sector as it was sets its own backup aside (_set_aside_unchanged), if
        that is a `.bin`: over a bad CRC it is a `.bad-read`, which stays."""
        onboard = self._writable("restore")
        path = onboard.newest_backup(self.backup_name, PROFILE_SECTOR)
        if path is None:
            raise MouseError(f"no backup of sector 0x{PROFILE_SECTOR:04x} found")
        size = onboard.info.sector_size
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
            self._set_aside_unchanged(onboard)
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
    def capabilities(self) -> list[str]:
        """What this mouse can do, probed, for a front end to render from. `buttons` means a model
        with a button table, and `battery`, last, one whose feature table lists a battery
        feature (battery_feature). `lighting` is added by the daemon's `mouse` reply
        (MouseReply._cmd_mouse) for clusters that enumerate through 0x8071, or 0x8070's zones
        on a model in ZONE_LIGHTING: the G305 lists 0x8070, reports one zone and accepts a
        color write, and has no lights."""
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
                out["dpi_range"] = [self.dpi_range.minimum, self.dpi_range.maximum,
                                    self.dpi_range.step]

        def rate() -> None:
            out["report_rate"] = self.get_report_rate()
            out["report_rates"] = self.report_rates()

        def charge() -> None:
            if battery:
                out["battery"] = read_battery(self.dev, CELLS.get(self.product), self.name)

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
