"""The mice: their models, which are attached, and the words the daemon, the CLI and the
window share. The handle the daemon holds for each mouse, and its writes to onboard profile 1,
are mouse_device.py's.

What a mouse can do is probed from its feature table, except where its MouseModel says
otherwise, its buttons (MouseModel.button_table) among them. On the G502 X PLUS G6 to G9 run
firmware functions and emit nothing, and the mouse has no 0x1b04 REPROG_CONTROLS_V4 to divert
them, so `Mouse.enable_buttons` rewrites the table's buttons in profile 1 to mouse buttons 11
to 16, which nothing on the desktop uses and the daemon acts on; in HOST mode 0x8110 reports
them instead (spy.py).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, TypeGuard

from .battery import G502_LIGHTSPEED_CELL, G903_HERO_CELL, CellCurves
from .config import MOUSE_LABELS, ModelKeys, entry_under
from .hidpp import HidppError, find_each_attached
from .onboard import (
    ENTRY_FUNCTION,
    NOOP,
    ROM_SECTOR_BASE,
    ProfileImage,
    entry_mouse_button,
    mouse_button,
)
from .sensor import MouseError
from .wording import word_list

#: The mouse button each programmable button sends once `Mouse.enable_buttons` has written it
#: to profile 1, by name (config.MOUSE_BUTTONS): 11 to 16, which nothing on the desktop uses.
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
    #: The entry `Mouse.write_fallback_buttons` writes there.
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
    #: to 16 after `Mouse.enable_buttons`: its udev rule grants the node to the gearbench group.
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
        those entries change, and the copy keeps the old CRC until `to_bytes`."""
        edited = ProfileImage(bytes(image.data))
        for slot in self.buttons:
            edited.set_button(slot.index, mouse_button(ENABLED_BUTTON[slot.name]))
        return edited

    def holds_fallback(self, image: ProfileImage) -> bool:
        """True when every button holds its fallback entry."""
        return all(image.button(slot.index) == slot.fallback for slot in self.buttons)

    def state_words(self, image: ProfileImage) -> str:
        """What `image` holds for the buttons, for the journal's profile 1 line."""
        if self.all_enabled(image):
            return "extra buttons enabled"
        if self.holds_fallback(image):
            return "extra buttons set to the fallback"
        enabled = self.enabled(image)
        return f"extra buttons {partly_enabled(enabled) if enabled else 'not enabled'}"

    def fallback(self, image: ProfileImage) -> ProfileImage:
        """A copy of `image` with each button set to its fallback entry; only those entries
        change, and the copy keeps the old CRC until `to_bytes`."""
        edited = ProfileImage(bytes(image.data))
        for slot in self.buttons:
            edited.set_button(slot.index, slot.fallback)
        return edited


#: The G502 X PLUS's, each index read off its flash, found on Windows by giving each index its
#: own button number and seen on Linux on 2026-09-25; eleven entries (getInfo). The fallback
#: is the ROM copy's factory entry, except on G9, whose `90 0a` cycles the onboard profiles,
#: and on profile 2 nothing Gearbench writes is in effect. G9 gets the no-op found on Windows,
#: not the empty `ff ff ff ff`: profile 1 holds that at the wheel's indices, 11 and 12, which
#: scroll all the same, so what it does on a control is not known.
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
#: N - 1 and whose drawing puts each marker on its printed button; eleven entries (getInfo).
#: Its function entries end `ff ff` where the X PLUS's end `00 00`, and the fallback keeps
#: them; G9 gets the no-op in place of its profile cycle, as on the X PLUS. Its event node, on
#: interface 00, no rule grants.
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
#: 8 buttons), named by G HUB's slot rule and its device database's side buttons.
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
#: MPM23.01_B0013, getInfo 12 buttons), named by G HUB's slot rule and its device database's
#: side buttons; the profile cycle at 11, slot g12, has no Gearbench name and keeps its job.
#: Which printed label each DPI button has is not read: the first report checks it.
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

#: The G203 LIGHTSYNC's, from libratbag issue 1059's dump of one (six buttons, button 5 its
#: DPI button's DPI cycle) and issue 1894's of another (the same): back and forward at 3 and 4,
#: the DPI button behind the wheel at 5, named by G HUB's slot rule; six entries (getInfo, by
#: libratbag's button count). Its factory entries are in no source: the fallbacks are the ones
#: that do those jobs, the DPI cycle's ending `00 00` as the PRO WIRELESS's does, assumed.
G203_LIGHTSYNC_BUTTONS = ButtonTable(
    buttons=(
        ButtonSlot("g4", 3, bytes.fromhex("80 01 00 08"), MOUSE_LABELS["g4"]),   # back
        ButtonSlot("g5", 4, bytes.fromhex("80 01 00 10"), MOUSE_LABELS["g5"]),   # forward
        ButtonSlot("g6", 5, bytes.fromhex("90 05 00 00"), "G6 (DPI button)"),    # DPI cycle
    ),
    tilts=(),
    count=6,
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
    #: Whether its onboard profile writes are let through: once they pass their test on this
    #: hardware, or by the owner's decision for that test. Any other model's are refused with
    #: nothing sent (not_tested, the owner's decision of 2026-10-17).
    writes_tested: bool = False
    #: Whether its lighting is 0x8070's zones (mouse_lighting.ZoneLighting), which the daemon
    #: drives on such a model alone: the G305 lists 0x8070 and has no lights.
    zone_lighting: bool = False
    #: Whether it claims its lighting only once there is a color to write, not at its setup,
    #: and is released once at its attach with nothing to write (WAITS_TO_CLAIM): each
    #: model with zone lighting (the owner's decision of 2026-10-18, "Wait to claim"), the
    #: G903 LIGHTSPEED HERO ("Wait for a color") and the G203 LIGHTSYNC.
    waits_to_claim: bool = False
    #: Whether its lighting writes passed their test on this hardware: until they do,
    #: `status` says its lighting is not yet tested (lighting_under_test).
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
#: The HERO 2 mice on a LIGHTSPEED receiver, 046d:c54d (Solaar's listings): five buttons and no
#: lighting but the DPI indicator its firmware owns, the DPI on 0x2202 and the rate on 0x8061
#: (sensor.py), which their firmware takes in HOST mode alone: only there does Gearbench set
#: either. G HUB's device database gives the three the same capabilities and
#: DPI indicator colors, and the SUPERLIGHT 2 SE the SUPERLIGHT 2's ids. Their firmware is
#: encrypted, so no image was read: the buttons are the PRO X SUPERLIGHT's, back and forward at
#: 3 and 4 of five entries, assumed (five buttons, RTINGS' review and retailers'); the cable's
#: HID++ interface, 02, is the PRO X SUPERLIGHT's, assumed; no cookie is trusted until a unit
#: is read; that HOST mode is volatile on them is assumed, as on the PRO X SUPERLIGHT. Their
#: feature tables are Solaar's listings of the SUPERLIGHT 2 (40A9, firmware MPM 32.00.B0012)
#: and the DEX (issue 2904, MPM 37.01.B0005); the 2c's is assumed the SUPERLIGHT 2's.
HERO_2_COLORS = (0xFFFFFF, 0xFF7203, 0x85FFB4, 0xFFE913, 0xFF00D7)
PRO_X_SUPERLIGHT_2 = MouseModel("PRO X SUPERLIGHT 2", "proxsuperlight2", HERO_2_COLORS,
                                button_table=PRO_X_SUPERLIGHT_BUTTONS, host_mode=True)
PRO_X_SUPERLIGHT_2_DEX = replace(PRO_X_SUPERLIGHT_2, name="PRO X SUPERLIGHT 2 DEX",
                                 backup_name="proxsuperlight2dex")
PRO_X_SUPERLIGHT_2C = replace(PRO_X_SUPERLIGHT_2, name="PRO X SUPERLIGHT 2c",
                              backup_name="proxsuperlight2c")
#: The SUPERLIGHT 2's body with HITS buttons, on the same c54d (Solaar issue 3202's listing):
#: Solaar's listing of 40BD (firmware MPM 42.00.B0011) gives it the SUPERLIGHT 2's features
#: and 0x1B0C, its buttons' actuation, rapid trigger and haptics, and 0x80E0. Those analog
#: settings wait for a device report (the owner's decision of 2026-10-21, "Rest now, analog
#: later"): only the device report asks 0x1B0C, and nothing asks 0x80E0. G HUB gives it the
#: SUPERLIGHT 2's DPI indicator colors. Solaar 1.1.19 found no onboard profiles it reads on
#: one, so its profile format may be past 5, which the first report checks; the rest is the
#: SUPERLIGHT 2's, assumed as there.
PRO_X2_SUPERSTRIKE = replace(PRO_X_SUPERLIGHT_2, name="PRO X2 SUPERSTRIKE",
                             backup_name="prox2superstrike")
#: Cabled alone, on 046d:c092, and on 046d:c09d: G HUB's device database files both ids under
#: the G203 LIGHTSYNC, each sold as the G102 LIGHTSYNC too (its derivatives by extended
#: model), and libratbag's data file matches both. Solaar issue 2984's listing of a c092
#: (firmware U1 52.00.B0010) gives 0x2201 v1, 0x8060, 0x8071 v0 with a logo zone, 0x8081 v2
#: with three LEDs, 0x8100 and 0x8110, and no battery or 0x1D4B; G HUB its DPI, 200 to 8000
#: in steps of 50, per-key lighting with a logo zone, and no DPI indicator colors. Its HID++
#: collection is on interface 01 beside the mouse's keyboard collection (Solaar's descriptors:
#: `interface=1` for the c092; the c09d's assumed the same), as on the G502 HERO. Its lighting
#: through 0x8071 and 0x8081 as the G502 X PLUS's, assumed, claimed only once there is a color
#: to write (the fewest writes to an untested mouse). HOST mode as
#: untested, its image never read.
G203_LIGHTSYNC = MouseModel("G203 LIGHTSYNC", "g203lightsync", (),
                            button_table=G203_LIGHTSYNC_BUTTONS, host_mode=True, interface=1,
                            waits_to_claim=True)

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
                                   0x4087: G903_HERO, 0xC091: G903_HERO,
                                   0x40A9: PRO_X_SUPERLIGHT_2, 0xC09B: PRO_X_SUPERLIGHT_2,
                                   0x40B8: PRO_X_SUPERLIGHT_2_DEX, 0xC0A0: PRO_X_SUPERLIGHT_2_DEX,
                                   0x40B7: PRO_X_SUPERLIGHT_2C, 0xC09F: PRO_X_SUPERLIGHT_2C,
                                   0x40BD: PRO_X2_SUPERSTRIKE, 0xC0A8: PRO_X2_SUPERSTRIKE,
                                   0xC092: G203_LIGHTSYNC, 0xC09D: G203_LIGHTSYNC}
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
                        0xC091,
                        0xC09B,
                        0xC0A0,
                        0xC09F,
                        0xC0A8,
                        0xC092, 0xC09D})

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
#: four, then the PRO X SUPERLIGHT's other four, then the HERO 2 mice's. A color added above
#: with no name here gets a dot that names none.
STAGE_COLOR_NAMES = {
    0xFFFFFF: "white", 0xFF6400: "orange", 0x28FFDC: "cyan", 0xFFFF14: "yellow",
    0xDC14FF: "purple",
    0xF8E71C: "yellow", 0xFD7600: "orange", 0xE9168C: "pink", 0x00A9E0: "blue",
    0xFFFF00: "yellow", 0xFF9900: "orange", 0xFF0293: "pink", 0x8D40FF: "purple",
    0xFF7203: "orange", 0x85FFB4: "green", 0xFFE913: "yellow", 0xFF00D7: "pink",
}

#: The widest DPI range of a supported sensor, (lowest, highest): the HERO 2's, 100 to 44000,
#: as a PRO X SUPERLIGHT 2 DEX lists it (Solaar issue 2904's log); the G502 X PLUS's ends at
#: 25600, and the G305's starts at 200. A DPI under the lowest is a stage's number, which the
#: daemon refuses too.
WIDEST_DPI_RANGE = (100, 44000)
#: How long a front end waits for a command that can write onboard profile 1 to the mouse's
#: flash, in seconds, past send_command's 3 s.
PROFILE_WRITE_WAIT = 30.0
#: How long a front end waits for `mouse_dpi`, in seconds: Mouse.set_dpi gives a mouse that
#: does not answer three tries of 1 s, which outlast send_command's 3 s.
DPI_WRITE_WAIT = 10.0


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


def other_profile(running: object) -> str | None:
    """A `mouse` reply's onboard.running_profile in words where it names a profile other than
    1, or None: profile 1, not read yet, or no sector number."""
    return (profile_words(running) if isinstance(running, int) and runs_another_profile(running)
            else None)


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


def partly_enabled(enabled: list[str]) -> str:
    """"partly enabled (G6 only)": a profile 1 where the buttons `enabled` (ButtonTable.enabled)
    send the daemon's mouse buttons and the rest do not."""
    return f"partly enabled ({word_list([name.upper() for name in enabled], 'and')} only)"


def try_again_words(many: bool = False) -> str:
    """The step that has a mouse, or `many` of them, try again: a power cycle."""
    return f"switch {'each mouse' if many else 'the mouse'} off and on to try again"


def not_taken_words(named: str, many: bool = False) -> str:
    """`problems`' mouse_not_host and mouse_host_mode's note: host mode did not take on
    `named`, one mouse or `many`, each with its reason, and the step that tries again."""
    return f"host mode did not take on {named}; {try_again_words(many)}"


def stuck_words(many: bool = False) -> str:
    """What a mouse stuck in HOST mode, or `many` of them, cannot do, and the step to take."""
    return f"{'their' if many else 'its'} programmable buttons do nothing; {try_again_words(many)}"


def stuck_in_host(names: list[str], why: str | None = None) -> str:
    """`problems`' mouse_left_in_host and mouse_host_mode's note: the mice `names` counted in
    HOST mode with host mode off or refused, and `why` when it is one mouse's."""
    many = len(names) > 1
    return (f"{word_list(names, 'and')} {'are' if many else 'is'} stuck in host mode"
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
    .flash_refusal): the rate went live alone, and is kept for its model (Dpi.live_rate)."""
    return (f"Set live on the {name}, and again each time Gearbench sets it up in host mode. "
            f"Its onboard profile, which Gearbench does not write yet, keeps its own rate for "
            f"when Gearbench stops.")


def dpi_in_host_only(name: str, dpi: int, refused: HidppError) -> str:
    """Why a mouse whose firmware takes a DPI in host mode alone (sensor.ExtendedDpi) refused
    `dpi` in onboard mode, and where to look."""
    return (f"the {name} refused DPI {dpi} ({refused}): outside host mode it takes no DPI "
            f"from the computer; `gearbench-ctl mouse` says whether host mode is on, and why "
            f"the mouse is not in it")


def written_note(name: str, host: bool = False, running: int | None = None, own: str = "") -> str:
    """The note after a write to onboard profile 1. It names the mouse, since with two
    attached "the mouse" is a guess. `running` is the profile the mouse was last read
    running (Mouse.running_profile): outside HOST mode, on another, the write is not in
    effect (the owner's decision of 2026-10-11). `own`, in HOST mode, names the loaded game
    profile whose own stages or rate stay in force over profile 1's."""
    if host:
        over = f", except {own}'s own while it is loaded; the mouse runs profile 1" if own else ""
        return (f"Written to onboard profile 1 on the {name}. In host mode, Gearbench uses its "
                f"stages and rate at once{over or '; the mouse runs it'} when Gearbench stops.")
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
WRITE_A_BACKUP = ("write the newest good backup over it before switching the mouse off: "
                  "Restore profile backup… (or `gearbench-ctl mouse-restore`); see RECOVERY in "
                  "gearbench-ctl(1)")
#: The refusal of a profile call on a mouse without onboard profiles, with its name; the
#: Mouse tab says it on the Programmable buttons row.
NO_ONBOARD = "the {} has no onboard profiles"
#: The refusal to write over a profile 1 whose CRC fails.
BAD_CRC = f"onboard profile 1 fails its CRC, so nothing is written over it; {WRITE_A_BACKUP}"
#: Why a mouse on another profile is not switched to a profile 1 whose CRC fails, and what to do
#: (Mouse.switch_refusal).
NOT_SWITCHED = (f"onboard profile 1 fails its CRC, so the mouse is not switched to it; "
                f"{WRITE_A_BACKUP}")


def untested_words(name: str) -> str:
    """Why every write to the onboard profile of a model whose writes are not tested on this
    hardware (MouseModel.writes_tested) is refused: the window says it before a click, and
    not_tested after one."""
    return (f"Gearbench does not write the {name}'s onboard profile until that is tested on "
            f"this model")


#: The writes to the onboard profile that not_tested refuses, each with what holds in its
#: place, said after the reason. The rate's is refused outside host mode alone: in host mode
#: the daemon sets the rate live on such a model (AttachedMouse.sets_rate_live), and every
#: model whose writes are not tested has host mode.
UntestedWrite = Literal["rate", "stages", "buttons", "restore"]
#: Where a live DPI is set, which the stages' refusal sends the person to.
LIVE_DPI = "set live with `gearbench-ctl mouse-dpi` or the Mouse tab's DPI row"
UNTESTED_KEEPS: dict[UntestedWrite, str] = {
    "rate": "its report rate stays as that profile holds it; in host mode it is set live",
    "stages": f"its DPI stages stay as that profile holds them; its DPI is {LIVE_DPI}",
    "buttons": "outside host mode, and without Gearbench, its buttons do what that profile holds",
    "restore": "that profile stays as the mouse holds it",
}
#: The stages' in place of UNTESTED_KEEPS' on a mouse whose firmware takes a DPI in host mode
#: alone (Mouse.dpi_host_only): outside it, set_dpi is refused (dpi_in_host_only).
HOST_ONLY_STAGES = (f"its DPI stages stay as that profile holds them; in host mode its DPI is "
                    f"{LIVE_DPI}")


def not_tested(name: str, write: UntestedWrite, dpi_host_only: bool = False) -> str:
    """The refusal of `write` to the onboard profile of a model whose writes are not tested on
    this hardware (MouseModel.writes_tested); `dpi_host_only` for a mouse whose firmware takes
    a DPI in host mode alone, whose stages' refusal sends the person to host mode."""
    keeps = HOST_ONLY_STAGES if dpi_host_only and write == "stages" else UNTESTED_KEEPS[write]
    return f"nothing was written: {untested_words(name)}, so {keeps}"


def lighting_under_test(name: str) -> str:
    """`status`'s words for an attached mouse whose lighting is driven before its writes
    passed their test on this hardware (MouseModel.lighting_tested)."""
    return (f"the {name}'s lighting is not yet tested on this model: a color sent to it "
            "may not stay after a replug")


#: Whose lighting a mouse that waits to claim its lighting shows (AttachedMouse.lighting_shown,
#: `mouse`'s `lighting_shown` and `status`'s `mouse_lighting_shown`): its own, or Gearbench's.
OWN_LIGHTING, GEARBENCH_LIGHTING = "own", "gearbench"


def lighting_shown_words(name: str, shown: str) -> str:
    """`status`'s words for whose lighting an attached mouse that waits to claim its lighting
    shows: `shown` is OWN_LIGHTING or GEARBENCH_LIGHTING."""
    if shown == OWN_LIGHTING:
        return (f"the {name} shows its own lighting until Gearbench sends it a color or an "
                f"effect, set for it or followed from the keyboard")
    return f"the {name} shows the lighting Gearbench sends it"


def not_switched_untested(crc_ok: bool) -> str:
    """Why a model whose writes are not tested is not switched to profile 1: whether the
    switch persists on it is not known, so it is treated as a write that may (the owner's rule
    of 2026-10-17). `crc_ok` False adds what profile 1's bad CRC means, with no restore offered,
    the words for a blank one, as on a mouse G HUB never wrote."""
    said = "Gearbench does not switch it until that is tested on this model"
    return said if crc_ok else (f"{said}; its profile 1 fails its CRC, as a blank one does, "
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
SET_IT_ASIDE = ("; set it aside, since each restore takes the newest backup, and see RECOVERY "
                "in gearbench-ctl(1)")


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
             else f"{error}; Gearbench tries again once the mouse answers" if silent and running
             else f"{error}; {try_again_words()}")
    return f"{said}, so what profile 1 holds is not in effect; {again}"


def find_attached_mice() -> list[tuple[Path, int, int]]:
    """Every known mouse attached now, in PRODUCT_IDS order. Two mice on separate receivers
    each have a hidraw node with its own product id (measured 2026-09-22)."""
    return find_each_attached({product: model.interface for product, model in MODEL_OF.items()})


def backup_name(product: int) -> str:
    """The prefix of this model's backup files (MouseModel.backup_name)."""
    return BACKUP_NAMES[product]
