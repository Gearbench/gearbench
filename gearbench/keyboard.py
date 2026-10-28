"""Which keyboard is attached, what it can do, and what it shows of the lighting a profile keeps
for another model: copy first, for a keyboard with none of its own (`shown_lighting`).

Capabilities are probed, as the mouse's are, since what answers matters more than the model.
The G915 is the only keyboard measured in full; where each other model's ids and features come
from is said beside its KeyboardModel and in MODEL_OF's comment, and `tested` marks those
Gearbench has run on.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypedDict

from .battery import G915_CELL, CellCurves, has_battery, read as read_any_battery
from .config import (
    DEFAULT_BRIGHTNESS,
    F_ROW_KEYS,
    OWN_KEYS,
    STATIC,
    Lighting,
    ModelKeys,
    entry_under,
    keep_under,
)
from .hidpp import RECEIVER_SLOTS, Hidpp, find_each_attached
from .keycodes import ZONES
from .keycontrol import F_FILES, F_KEY_CONTROL
from .keys import F_GKEY, F_MKEYS, F_MR
from .layout import (
    G213_BOARD,
    G316X_BOARD,
    G316X_FN_KEYS,
    G512X75_BOARD,
    G512X75_FN_KEYS,
    G512X98_BOARD,
    G512X98_FN_KEYS,
    G515_BOARD,
    G515_FN_KEYS,
    G815_BOARD,
    G915_BOARD,
    G915_TKL_BOARD,
    G915X_BOARD,
    G915X_FN_KEYS,
    G915X_LIGHTSPEED_BOARD,
    G915X_TKL_BOARD,
    PRO_X_TKL_BOARD,
    Board,
)
from .lighting import F_BRIGHTNESS, F_PERKEY, F_RGB, SIGNATURES, WAVE_DIRECTIONS
from .mouse_lighting import F_COLOR_LED
from .onboard import F_ONBOARD
from .settings_files import ConfigError
from .wording import word_list

#: The vendor HID++ collection's interface on the G915, on the cable and the c541 receiver
#: alike; 00 and 01 carry keystrokes and a pointer and are never opened. The c541 gives the
#: keyboard no node of its own (udev/70-g915.rules).
VENDOR_INTERFACE = 2

#: A keyboard has GKEY and a receiver does not, which is what `hidpp.open_device` uses to tell
#: the G915 from its receiver on the receiver's own node.
WITNESS = F_GKEY


@dataclass(frozen=True)
class KeyboardModel:
    """What this project knows of one keyboard model, on every route it answers on."""

    #: Its name, as Gearbench shows and logs it.
    name: str
    #: The feature `hidpp.open_device` asks each device index for, which the keyboard has and
    #: a receiver lacks.
    probe: int
    #: The keys the window draws.
    board: Board
    #: The wave directions it takes, by lighting.WAVE_DIRECTIONS' names.
    wave_directions: tuple[str, ...]
    #: The USB interface of its vendor HID++ collection, which `find_each_attached` matches and its
    #: udev rule grants.
    interface: int = VENDOR_INTERFACE
    #: Whether a per-key paint is cut to the board's zones before it is sent. The G915 is sent
    #: every zone a request names, and lights those it has.
    cut_to_board: bool = False
    #: The key each of its G keys types while nothing is bound to it, from G1 on, in a
    #: binding's spelling, where its G keys are keys of its own board that only its onboard
    #: profile makes type: in HOST mode Gearbench types them (keystroke), and its stop hands
    #: it ONBOARD mode back, a shutdown's included, or they type nothing until it is switched
    #: off and on. Empty where an unbound G key types nothing.
    g_key_defaults: tuple[str, ...] = ()
    #: Whether its G keys are keys of its own board that its custom assignments set to report
    #: to Gearbench while bound and leave doing their own job while unbound
    #: (keycontrol.TriggeredKeys): the G515 generation's way. False for a keyboard whose G
    #: keys 0x8010 diverts, and for one with no G keys.
    custom_assignments: bool = False
    #: The key id of each of those G keys, from G1 on, which the file that sets them names,
    #: where they were measured on one, and the check of the read below. Empty for a model
    #: whose key ids were not: its own 0x1B10 control list gives them at its setup, by
    #: control_ids (KeyControl.set_up), and with no list read they are not known.
    key_ids: tuple[int, ...] = ()
    #: The control id of each of those G keys, from G1 on (keycontrol.key_ids_at), Logitech's
    #: number for the key, which models share where their key ids differ.
    control_ids: tuple[int, ...] = ()
    #: The number 0x1B05's event 0 gives each of those G keys by while it is held, from G1 on
    #: (keycontrol.TriggeredKeys), empty while not known.
    trigger_numbers: tuple[int, ...] = ()
    #: Whether the custom assignments' write has passed its test on this hardware, which needs
    #: its G keys' key ids and trigger_numbers. Until it has, no layer file is written and its
    #: G keys take no binding (assignments_untested).
    assignments_tested: bool = False
    #: What one of the G keys its custom assignments bind is called in a line, and what they
    #: do while no file of Gearbench's sets them to report: what an unbound one does, but on a
    #: model with reports_unbound what each does without Gearbench or with its custom
    #: assignments off.
    own_key: str = "F key"
    unbound: str = "type their own keys"
    #: Whether its custom assignments set each of those G keys to report to Gearbench, bound or
    #: not, so an unbound one does nothing while Gearbench runs (KeyControl.wanted): dedicated G
    #: keys whose own keys the desktop takes for its own. False where they are its F row, whose
    #: unbound keys type their F key.
    reports_unbound: bool = False
    #: Whether its HID++ collection declares the long report alone, so that every request goes
    #: out long (Hidpp.long_only), the probe's included: the G815's interface 01.
    long_only: bool = False
    #: Whether Gearbench has run on one on the hardware. Until it has, the front ends and the
    #: journal ask for a device report from it (report.untested).
    tested: bool = False
    #: The cell its 0x1001 millivolts are read against for a percentage, or None: then it
    #: reports volts and no percentage, since another cell's curve would give a wrong number.
    cell: CellCurves | None = None
    #: The zones its board lights white while FN is held, where it reports FN over 0x8051
    #: (modifiers.py): the keys FN gives a second function, and FN. Empty for a model with no
    #: FN key, or one whose FN is not reported.
    fn_keys: frozenset[int] = frozenset()
    #: Whether its lighting is 0x8070's zones (keyboard_zones.KeyboardZones), the G213
    #: Prodigy's, in place of 0x8071's clusters.
    zone_lighting: bool = False
    #: Whether it is known to keep its brightness when switched off, which makes each brightness
    #: write one it stores: it is sent a level only when the level changes
    #: (daemon.keyboard.paint.Paint.write_brightness).
    keeps_brightness: bool = False
    #: Whether its brightness is read off it at each setup and kept where it shows its own
    #: (Paint.read_brightness) though it is not known to keep it, so it is still written at
    #: each apply. A model that keeps its brightness is read whether or not this is set.
    reads_brightness: bool = False
    #: The 0x8070 zone that lights each of its board's areas (layout.Board.areas), in their
    #: order, on a model with zone_lighting whose areas take a color each. Empty otherwise.
    area_zones: tuple[int, ...] = ()
    #: The whole-board effects its 0x8071 cluster 0xff, its whole board's,
    #: is read for where its device info counts effects on it (lighting.Lighting.whole_board),
    #: each run there where the cluster lists its id and a counted cluster lacks it: on the G515
    #: generation's wave, ripple and Pulsar Point (G515_WHOLE_BOARD), and on the G512 X pair's
    #: three more (G512X_WHOLE_BOARD). With none, nothing is read there.
    whole_board: tuple[str, ...] = ()

    @property
    def g_key_names(self) -> tuple[str, ...]:
        """The names its G keys bind under, from G1 on, as its board draws them bindable
        (layout.Board.bindable_keys): config's G_KEYS or one of its OWN_KEYS, the names a layer
        of bindings keeps (bound_as). Empty for a keyboard with no G keys."""
        return tuple(key.binds_as for key in self.board.bindable_keys)


#: The G915's wave directions: the four the owner watched on it on 2026-09-24, and
#: reverse-vertical and center-in, 7 and 8, which the owner watched run as named on it on
#: 2026-10-18 (hardware round 2) and its firmware image's direction check takes; its own
#: lighting key runs 7 (read 2026-10-17). The check refuses G HUB's 4 and 5, which
#: lighting.WAVE_DIRECTIONS leaves out, after the write is answered as a success.
G915_WAVE = ("horizontal", "vertical", "center-out", "reverse-horizontal", "reverse-vertical",
             "center-in")
G915 = KeyboardModel("G915", WITNESS, G915_BOARD, wave_directions=G915_WAVE, tested=True,
                     cell=G915_CELL)
#: The control ids of F1 to F12. F1 to F8's, 0x00F6 and 0x00EF to 0x00F5, are Logitech's ids
#: for those keys as Solaar lists them. F9 to F12's are taken to be 0x014D to 0x0150, which no
#: listing names (assumed): in the G515 TKL's control list and the G915 X LIGHTSPEED's alike
#: they sit where the F row's key ids run on, F1 to F8's interleaved with them, with Esc's
#: before them and Print Screen, Scroll Lock and Pause's, whose control ids Solaar lists, after.
F_ROW_CONTROLS = (0x00F6, 0x00EF, 0x00F0, 0x00F1, 0x00F2, 0x00F3, 0x00F4, 0x00F5,
                  0x014D, 0x014E, 0x014F, 0x0150)
#: The G515 TKL's key ids for F1 to F12, the key ids that hold F_ROW_CONTROLS in its 0x1B10
#: control list, which G HUB read off the owner's wired G515 TKL on 2026-10-21. 0x10, F1's,
#: is the one G HUB's assignment of F1 named, and F1 to F4's each took an entry on the owner's
#: wired G515 TKL on 2026-10-22, F2's a remap to Q.
G515_KEY_IDS = (0x10, 0x01, 0x11, 0x02, 0x12, 0x03, 0x13, 0x04, 0x14, 0x05, 0x15, 0x06)
#: The number the G515 TKL's 0x1B05 event 0 gives each of F1 to F12 by while it is held, set to
#: report: F1 to F4's 02 to 05 as the owner's wired G515 TKL sent them on 2026-10-22, each tagged
#: with its F number (keycontrol.REPORT_TAIL), and F5 to F12's taken to run on (assumed).
G515_TRIGGER_NUMBERS = (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D)
#: The whole-board effects a G515 generation keyboard is read for (KeyboardModel.whole_board):
#: wave and ripple, run there where a counted cluster lacks them, as the G915 X LIGHTSPEED's
#: logo cluster does, so that its logo joins them, and left to the clusters where each runs
#: them, as the G515s' one does (the owner's decision of 2026-10-24, "lets try it"); and Pulsar
#: Point, from the G515s' comment below.
G515_WHOLE_BOARD = ("wave", "ripple", "pulsar-point")
#: The G512 X pair's: the G515 generation's, and Remind Me Of, Neon and Outer Space, which with
#: Pulsar Point are the four G HUB's G512 X 75 and 98 records list on the whole board
#: (ZONE_ALL), the three by their signature index on Pulsar Point's effect
#: (lighting.SIGNATURES; the owner's decision of 2026-10-24, "Now, before install"). No other
#: model's record lists the three.
G512X_WHOLE_BOARD = (*G515_WHOLE_BOARD, *SIGNATURES)
# No 0x8010 in the wired G515's feature table (read 2026-10-17), so the probe asks for
# 0x8071: cabled, the G515 answers on 0xFF, where no receiver is. Its HID++ collection is on
# interface 02, as the G915's. A zone it lacks is cut, since how its firmware takes one is not
# known. Its wave, COLORWAVE_S, takes the G915's directions: the owner watched each of the six
# run as named on a wired G515 TKL on 2026-10-18. Its FN keys light while FN is held (the
# owner's decisions of 2026-10-21), as they did on the owner's on 2026-10-22. Its F1 to F12
# bind through its custom assignments, under names of their own, each typing its own key
# while unbound (the owner's decisions of 2026-10-18, "Bindable, F keys by default" and "Their
# own", and of 2026-10-21, "Build it, test the write first"); that write is refused until it
# passes its hardware test. It keeps its brightness when switched off: 0x8040 fn 1 read 12
# after a replug with no daemon running on 2026-10-22, as G HUB's capture A5c read 13.
# The G515 LIGHTSPEED TKL is taken to be the same, and its brightness is written untested on the
# wired one's evidence (the owner's decision of 2026-10-22, "Lets test with the keyboard we have
# and allow."); through its receiver it answers on a node of its own (MODEL_OF). Its key ids
# are its own control list's (the owner's decision of 2026-10-23, "All four lessons"). Its
# cluster 0xff is read for Pulsar Point, as on the whole G515 generation (the owner's decision
# of 2026-10-23, "Before the install", under "every feature its hardware can do"): the wired
# one's lists it (0x0018), and G HUB wrote it there at its start (Windows capture A2,
# 2026-10-21); G HUB's records give it to both G515s, the G915 X trio and the G512 X pair.
G515_TKL = KeyboardModel("G515 TKL", F_RGB, G515_BOARD, wave_directions=G915_WAVE,
                         cut_to_board=True, custom_assignments=True, key_ids=G515_KEY_IDS,
                         control_ids=F_ROW_CONTROLS, trigger_numbers=G515_TRIGGER_NUMBERS,
                         tested=True, fn_keys=G515_FN_KEYS, keeps_brightness=True,
                         whole_board=G515_WHOLE_BOARD)
G515_LIGHTSPEED = KeyboardModel("G515 LIGHTSPEED TKL", F_RGB, G515_BOARD,
                                wave_directions=G915_WAVE, cut_to_board=True,
                                custom_assignments=True, control_ids=F_ROW_CONTROLS,
                                trigger_numbers=G515_TRIGGER_NUMBERS, fn_keys=G515_FN_KEYS,
                                keeps_brightness=True, whole_board=G515_WHOLE_BOARD)
# The G915's feature tables and lighting code, from its firmware image (MPK14.02_B0022, read
# 2026-10-17), with a board of its own. A paint naming a zone it has no LED for is refused
# whole there (error 2), so its paints are cut. Its wave takes the G915's directions: the
# same firmware's handler, its own direction check not read. Its G1 to G12 are its F row,
# F1 to F12 in its onboard profile; in HOST mode they type nothing unless Gearbench types
# them (the owner's decision of 2026-10-18, "Gearbench types F keys"). They bind as the
# G915's G keys do, each typing its own key while unbound, under names of their own (the
# owner's decisions of 2026-10-18, "Bindable, F keys by default" and "Their own"): the names
# on their keycaps, Claude Code's call of 2026-10-18, pending the owner. The G913 TKL is on
# the same ids. It takes the G915's cell: the power model in G HUB's
# g915_tkl_us package has the G915's discharge and charging curves point for point, and
# differs only in its charging time and power figures (compared 2026-10-18).
G915_TKL = KeyboardModel("G915 TKL", WITNESS, G915_TKL_BOARD, wave_directions=G915_WAVE,
                         cut_to_board=True,
                         g_key_defaults=tuple(name.lower() for name in F_ROW_KEYS),
                         cell=G915_CELL)
# The G915's lighting, keys and onboard features at the G915's versions, and its replies byte
# for byte (Solaar's listing of U1 31.02.B0018, five more units' in Solaar's issues, and the
# raw HID++ of Solaar issue 2968 and libratbag issues 1127 and 1752); cabled alone. Its HID++
# shares interface 01 with what is typed on it, and that collection declares the long report
# alone, no short one. Its paints are cut, since how this firmware takes a zone it lacks is
# not known; its zones are the G915's. Its wave takes the G915's directions: the same effect
# records, its own direction check not read. The G813 is on the same id.
G815 = KeyboardModel("G815", WITNESS, G815_BOARD, wave_directions=G915_WAVE, interface=1,
                     cut_to_board=True, long_only=True)
# Solaar issue 3155's listing of a cabled unit (MPK 23.00.B0008): the G915 TKL's G keys
# (0x8010) and onboard profiles (0x8100) without its M and MR keys (no 0x8020 or 0x8030), the
# G515's lighting features (0x8071 v3, 0x8081 v2) and 0x8040, and 0x1004's battery; no 0x8051,
# so no FN reports. G HUB's record gives it F1 to F12 as G keys, one M state, and the G515's
# effects, which its clusters report. Its F row follows the G915 TKL's decisions of
# 2026-10-18, "Gearbench types F keys", "Bindable, F keys by default" and "Their own": its G1
# to G12 bind under names of their own, each typing its F key while unbound, in HOST mode, and
# its stop hands it ONBOARD mode back. Its HID++ is taken to be on interface 02, as on the
# G515s and the G915 TKL (assumed). Its paints are cut to its board, and its wave takes the
# G915's six directions (assumed: its setup guide's onboard rainbows run right, left, center
# out and up). Through its c547 receiver it answers on a node of its own, 40B0, as the G515
# LIGHTSPEED TKL does (G HUB's device database; Solaar's transport ids).
PRO_X_TKL = KeyboardModel("PRO X TKL LIGHTSPEED", WITNESS, PRO_X_TKL_BOARD,
                          wave_directions=G915_WAVE, cut_to_board=True,
                          g_key_defaults=G915_TKL.g_key_defaults)
# Solaar's listing of U1 09.00.B0006: 0x8070 v5, 0x8060, 0x4522, 0x1BC0 and 0x4540, cabled
# alone, with no 0x8071, 0x8081, 0x8040, G keys, onboard profiles or battery. Its lighting is
# 0x8070's zones, the whole board and its five areas (G HUB's record and package; OpenRGB's
# G213 controller), the first keyboard Gearbench drives on 0x8070. Its HID++ is on interface
# 01, Logitech's long collection beside its own reports (OpenRGB's detector; Solaar issue
# 2665's node list), so its requests go out long. G HUB's F1 to F12, programmable through
# 0x1BC0, are not driven. Its wave takes the four directions OpenRGB's G213 controller sends.
# Its areas are taken to be zones 1 to 5 in G HUB's order, zone 0 the whole board (assumed:
# OpenRGB's G213 controller writes a color to zones 1 to 5 and an effect to zone 0).
G213 = KeyboardModel("G213 Prodigy", F_COLOR_LED, G213_BOARD,
                     wave_directions=("horizontal", "center-out", "reverse-horizontal",
                                      "center-in"),
                     interface=1, long_only=True, zone_lighting=True,
                     area_zones=(1, 2, 3, 4, 5))


def g515_generation(name: str, board: Board, fn_keys: frozenset[int],
                    whole_board: tuple[str, ...] = G515_WHOLE_BOARD) -> KeyboardModel:
    """One of the G515 generation's other keyboards, untested and driven as the G515
    LIGHTSPEED TKL is, its G keys' key ids read off its own control list by `control_ids`
    and its cluster 0xff read for `whole_board` where its device info counts effects on it,
    but its brightness, read at each setup, is written at each apply. `whole_board` is the
    G515s' unless its record lists more."""
    return KeyboardModel(name, F_RGB, board, wave_directions=G915_WAVE, cut_to_board=True,
                         custom_assignments=True, control_ids=F_ROW_CONTROLS, fn_keys=fn_keys,
                         reads_brightness=True, whole_board=whole_board)


# The G515 generation's other keyboards (the owner's decisions of 2026-10-21: "Built untested
# based on as much data as we can gather", the framework built now "so when we confirm, we can
# fill in the blanks"; the seven are Claude Code's calls of 2026-10-21, pending the owner).
# None had been read on this machine before the G915 X LIGHTSPEED, whose own tests of
# 2026-10-23 its record follows below. Each has 0x1B05, 0x8101, 0x8051, 0x4523 and 0x8040 beside
# 0x8071 and 0x8081 in its feature table, so FN's reports, Game Mode's state, the brightness
# event and its custom assignments come by feature, as on the G515s; their write is refused
# until that model's own test. Their brightness, but the G915 X LIGHTSPEED's (below), is written
# untested, at each apply, as no source tells whether they keep it (the owner's decision of
# 2026-10-22, "Allow it too"), and read at each setup, as two of the family keep theirs (the
# owner's decision of 2026-10-23, "All four lessons"). Each is on interface 02 of its cable, the
# G915 X LIGHTSPEED's one HID++ interface on 2026-10-23 (its first connect's device report), and
# assumed on the others: on the G316 X's and G512 X's images interface 02 is a vendor collection
# of the G915 TKL's HID++ length. The G915 X LIGHTSPEED pair is on a node of its own behind its
# receiver too (MODEL_OF). Its board is G HUB's layout package for it (layout.py). The G515's
# effects and wave directions are taken to be theirs: the G915 X LIGHTSPEED's keys listed the
# G515's six effects and its logo disabled, static, breathe and cycle on 2026-10-23 (its first
# connect's step 7), as Solaar's listing of it (issue 3328) has them, no wave direction has been
# watched on one of the seven, and the G316 X's 0x8071 v5 and the G512 X's v6 are versions no
# unit has been read at. The G915 X's table is the wired G515 TKL's index for index to 0x16, and
# the G915 X LIGHTSPEED's through its receiver the G515 LIGHTSPEED TKL's to 0x1A, where on its
# cable 0x1D4B and 0x1004 put 0x8071 at 0x08 (Solaar issues 3113, 3137 and 3328; the G915 X
# LIGHTSPEED's two read so on 2026-10-23, its device reports and Windows captures G1 and G5d);
# the G915 X LIGHTSPEED TKL's is taken to be the G915 X LIGHTSPEED's, as G HUB
# gives it its capabilities less G-key lighting; the G316 X pair's and the G512 X pair's are
# their firmware images' (G HUB's 175.0.16, 186.01.7, 181.5.20 and 177.5.20). The G512 X's
# analog keys, 0x1B08, wait for a device report (the owner's decision of 2026-10-21).
# The G915 X's G1 to G9 bind under names of their own and do what the keyboard does with them
# while unbound (Claude Code's call of 2026-10-21, as the owner's "Bindable" and "Their own" of
# 2026-10-18 have the G915's), but on the G915 X LIGHTSPEED, below; FN with its F1 to F4 is the
# keyboard's, custom assignments' shortcut and its three onboard profiles. Each key id its
# control list gives is the keyboard's own (the owner's decision of 2026-10-23, "All four
# lessons"), its G keys' by the G915 X LIGHTSPEED's control ids, G915X_G_CONTROLS, and the
# TKL's F row by the G515's.
G915_X_OWN_KEY = "G key"
#: The control ids of G1 to G9 on the G915 X LIGHTSPEED, 0x018F to 0x0197 in G order: the
#: entries at the key ids its G keys were found at by remapping each to a letter, in its own
#: control list as the device report read it off the owner's on 2026-10-23 (the G-key plan's
#: steps 4 and 6). The G915 X is taken to have the same (assumed).
G915X_G_CONTROLS = tuple(range(0x018F, 0x0198))
# The G915 X's G keys take the G915 X LIGHTSPEED's rule, an unbound one doing nothing, once its
# custom assignments are tested (the owner's decision of 2026-10-23, "Yes, same rule"); until
# then its writes are refused and they do what they do without Gearbench.
G915_X = replace(g515_generation("G915 X", G915X_BOARD, G915X_FN_KEYS), own_key=G915_X_OWN_KEY,
                 unbound="do what they do without Gearbench", control_ids=G915X_G_CONTROLS,
                 reports_unbound=True)
#: The G915 X LIGHTSPEED's G1 to G9 key ids: each printed its letter when remapped there, on the
#: owner's on 2026-10-23 (the G-key plan's step 6).
G915_X_LIGHTSPEED_KEY_IDS = (0x6E, 0x77, 0x6F, 0x78, 0x70, 0x79, 0x71, 0x7A, 0x72)
#: The numbers 0x1B05's event 0 gave G1 to G9 by while held, set to report, 02 to 0a, one past
#: each tag, on the same unit (steps 8 and 9).
G915_X_LIGHTSPEED_TRIGGER_NUMBERS = tuple(range(0x02, 0x0B))
# Its LIGHTSPEED and Bluetooth buttons stand where the G915's do. It is the family's tested
# model, on its cable (046d:c356, MPK 26.00.B0012) and through its receiver (40B5): the owner
# ran its first connect and its G-key plan on one on 2026-10-23, and the record takes what they
# measured. Its G keys set to report gave their event while bound and the file went at a power
# loss (steps 8 to 10), so they take their bindings on both ids (the owner's decision of
# 2026-10-23, "Turn them on"). Each is set to report, bound or not, so an unbound one does
# nothing while Gearbench runs (the owner's decision of 2026-10-23, "Nothing"): with no file,
# G1, G2, G8 and G9 sent F13, F14, F20 and F21 from the keyboard in host and on-board mode
# alike, and G3 to G7 F15 to F19 in host mode (Windows captures G5b, G5c and X1, 2026-10-23),
# and GNOME takes F13 for Settings, F20 for mic mute and F21 for the touchpad. It
# keeps its brightness when it loses power: the 13 its own Brightness button set read 13 again
# after the cable was out and the keyboard switched off 5 s (the first connect's step 17), so it
# is read at each connect and written only on change, as the G515s are (the owner's decision
# of 2026-10-23, "Yes").
G915_X_LIGHTSPEED = replace(
    g515_generation("G915 X LIGHTSPEED", G915X_LIGHTSPEED_BOARD, G915X_FN_KEYS),
    own_key=G915_X_OWN_KEY, unbound="send F13 to F21", control_ids=G915X_G_CONTROLS,
    key_ids=G915_X_LIGHTSPEED_KEY_IDS, trigger_numbers=G915_X_LIGHTSPEED_TRIGGER_NUMBERS,
    assignments_tested=True, reports_unbound=True, tested=True, keeps_brightness=True)
G915_X_TKL = g515_generation("G915 X LIGHTSPEED TKL", G915X_TKL_BOARD, G915X_FN_KEYS)
# G HUB draws the G316 X (c361) on the G316 X 98's board, in every picture of it, with the 98's
# light bar; the G316 X 75 was announced on 2026-09-28, after G HUB's data was read, so c361 is
# taken to be a 98% board and the 75 to be on an id that data lacks (assumed).
G316_X = g515_generation("G316 X", G316X_BOARD, G316X_FN_KEYS)
G316_X_98 = g515_generation("G316 X 98", G316X_BOARD, G316X_FN_KEYS)
G512_X_75 = g515_generation("G512 X 75", G512X75_BOARD, G512X75_FN_KEYS,
                            whole_board=G512X_WHOLE_BOARD)
G512_X_98 = g515_generation("G512 X 98", G512X98_BOARD, G512X98_FN_KEYS,
                            whole_board=G512X_WHOLE_BOARD)

#: Each product id's model, in the order they are looked for: the G915 on its cable, then on
#: its receiver (both read off the hardware 2026-09-22), then the wired-only G515 TKL and the
#: G515 LIGHTSPEED TKL on its cable (G HUB's device database), then the G515 LIGHTSPEED TKL on
#: a node of its own that hid-logitech-dj gives it behind its c547 receiver, which the installed
#: 7.0.0 kernel binds, as the G502 X PLUS's 4099 node shows (Solaar 1.1.13's listing of it on
#: 40B4), after its cable, then the G915 TKL on its cable, on its own node on a kernel whose
#: hid-logitech-dj binds its c545 receiver, and
#: through the receiver's own node where hid-generic holds the receiver, as the installed
#: 7.0.0 kernel does (the image's ids and Solaar's records). The node of its own goes first:
#: a kernel whose hid-logitech-dj binds the c545 keeps the receiver's node too, and hands the
#: keyboard's replies to the other (inferred from the c547, where the G502 X PLUS answers on
#: a node of its own). Then the G815, on its cable alone (Solaar's listing; G HUB's record).
#: Then the PRO X TKL LIGHTSPEED on its cable and on a node of its own behind its c547, as the
#: G515 LIGHTSPEED TKL (G HUB's records), then the G515 generation's others, each on its
#: cable, the G915 X LIGHTSPEED and the G915 X LIGHTSPEED TKL each then on a node of its own
#: behind its receiver, as the G515 LIGHTSPEED TKL, and last the G213 Prodigy on its cable
#: alone (G HUB's records). G HUB names the pair's 40B5 and 40B6 and gives their receiver's
#: kind, LS2, as it gives the G515 LIGHTSPEED TKL's and the PRO X TKL's, and not its id: the
#: G915 X LIGHTSPEED's was a c547, which the installed kernel's hid-logitech-dj binds, on
#: 2026-10-23 (its first connect and Windows capture G5e), and the TKL's is assumed to be one.
MODEL_OF: dict[int, KeyboardModel] = {
    0xC33E: G915,
    0xC541: G915,
    0xC358: G515_TKL,
    0xC355: G515_LIGHTSPEED,
    0x40B4: G515_LIGHTSPEED,
    0xC343: G915_TKL,
    0x408E: G915_TKL,
    0xC545: G915_TKL,
    0xC33F: G815,
    0xC352: PRO_X_TKL,
    0x40B0: PRO_X_TKL,
    0xC359: G915_X,
    0xC356: G915_X_LIGHTSPEED,
    0x40B5: G915_X_LIGHTSPEED,
    0xC357: G915_X_TKL,
    0x40B6: G915_X_TKL,
    0xC361: G316_X,
    0xC36B: G316_X_98,
    0xC35F: G512_X_75,
    0xC360: G512_X_98,
    0xC336: G213,
}
PRODUCT_IDS = tuple(MODEL_OF)
MODELS = {product: model.name for product, model in MODEL_OF.items()}
CELLS = {product: model.cell for product, model in MODEL_OF.items() if model.cell is not None}


#: The names a model's lighting and brightness are kept under in config.json and a game
#: profile's file: its ids, by the first, its key, "c33e" for the G915 on its cable and on its
#: receiver, so a c541 written by hand is the G915's. In MODEL_OF's order, which copy first
#: falls back on.
KEYING = ModelKeys(MODEL_OF)
MODEL_IDS = KEYING.ids
MODEL_KEYS = tuple(MODEL_IDS)
model_key, model_ids, model_named = KEYING.model_key, KEYING.model_ids, KEYING.model_named

#: The G915's: G HUB's lighting, which the G HUB import reads off the G915's settings alone, and
#: the pack's, drawn for the G915, are kept under it.
G915_KEY = model_key(0xC33E)


def followed(key: str | None) -> str:
    """The keyboard a follower takes its lighting from, the leader, by its key (MODEL_IDS), as
    a sentence names it after "the": its model, or "keyboard" before one is known."""
    return "keyboard" if key is None else key_name(key)


def key_name(key: str) -> str:
    """The name of the model whose key (MODEL_IDS) is `key`, for a line or a label: "keyboard c999"
    for any other id, a model's other ids included."""
    model = model_named(key)
    return f"keyboard {key}" if model is None else model.name


def key_named(text: str, field: str) -> str:
    """The key (MODEL_IDS) of the keyboard model `text` names, by any of its ids (model_ids)
    or its name in any case; else a ConfigError that names `field` and lists the models there
    are."""
    wanted = text.strip().lower()
    for key in MODEL_KEYS:
        if wanted in (*model_ids(key), key_name(key).lower()):
            return key
    known = word_list([f"{key_name(key)} ({key})" for key in MODEL_KEYS], "and")
    raise ConfigError(f"{field}: {text!r} names no keyboard model Gearbench drives; "
                      f"they are {known}")


def wave_directions(key: str | None) -> tuple[str, ...]:
    """The wave directions the model whose key is `key` takes (KeyboardModel.wave_directions):
    every one for any other id and for None, before any keyboard, as nothing is cut then."""
    model = model_named(key)
    return tuple(WAVE_DIRECTIONS) if model is None else model.wave_directions


# -- copy first: a keyboard with no lighting of its own in a profile ----------

def own_entry[T](entries: Mapping[str, T], key: str) -> T | None:
    """What `entries`, kept by keyboard model, holds for the model named `key`, under any of its
    ids (model_ids); None for nothing."""
    return entry_under(entries, model_ids(key))


def keep_entry[T](entries: dict[str, T], key: str, entry: T) -> None:
    """Keep `entry` in `entries` as the model named `key`'s, under its key, and drop one under
    its other ids, which beside the key would be left unread (config.keep_under): a G915 entry
    under c541, written by hand."""
    keep_under(entries, model_ids(key), entry)


def source_entry[T](entries: Mapping[str, T], key: str | None, leader: str | None) -> T | None:
    """The entry a keyboard of model `key` starts from where `entries` holds none of its own:
    `leader`'s, the keyboard chosen to lead or attached first, then a model's with the same
    board, then each model's in MODEL_OF's order, the G915's first. With `key` None, before any
    keyboard was attached, the leader's and then MODEL_OF's order. None when none of them has
    an entry."""
    model = model_named(key)
    same = [other for other in MODEL_KEYS
            if model is not None and MODEL_OF[int(other, 16)].board == model.board]
    first = [leader] if leader else []
    return next((entry for other in [*first, *same, *MODEL_KEYS]
                 if (entry := own_entry(entries, other)) is not None), None)


def cut_lighting(entry: Lighting, model: KeyboardModel, effects: Collection[str] | None,
                 per_key: bool | None) -> Lighting:
    """`entry`, another keyboard model's lighting, cut to what a keyboard of `model` can show:
    what it starts from with no lighting of its own in a profile. `effects` are the effects it
    reports and `per_key` whether it shows painted keys, key by key or by area, each None
    while that is not known, which cuts nothing for it.

    An effect it does not report becomes static and the mode stays, as the board shows one
    (Paint.write_lit). A painted key stays only where its board has the key. A board that
    shows no painted key is static. A wave direction outside its wave_directions is dropped, so
    wave runs in its default one. A hand-set `params`, set for the other model's firmware,
    is dropped. The colors, the background and the speeds stay. The copy shares nothing with
    `entry`."""
    effect = entry.effect if effects is None or entry.effect in effects else "static"
    direction = entry.direction if entry.direction in model.wave_directions else None
    return Lighting(effect=effect, color=entry.color, second_color=entry.second_color,
                    speeds=dict(entry.speeds), direction=direction,
                    keys={name: color for name, color in entry.keys.items()
                          if ZONES.get(name) in model.board.zones},
                    mode=STATIC if per_key is False else entry.mode,
                    background=entry.background)


def shown_brightness(levels: Mapping[str, int], key: str | None, leader: str | None,
                     follows: bool = False) -> tuple[int, bool]:
    """The brightness `levels`, kept by model, give a keyboard of model `key`, and whether it is
    the model's own: its level, else source_entry's, else DEFAULT_BRIGHTNESS. With `follows`,
    a keyboard that follows `leader`, the level the leader shows, never its own. The
    keyboard floors it to a level it keeps as it is written (lighting.snap_brightness)."""
    own = None if key is None or follows else own_entry(levels, key)
    if own is not None:
        return own, True
    source = source_entry(levels, leader if follows else key, leader)
    return (DEFAULT_BRIGHTNESS if source is None else source), False


def shown_lighting(entries: Mapping[str, Lighting], key: str | None, leader: str | None,
                   effects: Collection[str] | None = None, per_key: bool | None = None,
                   follows: bool = False) -> tuple[Lighting, bool]:
    """The lighting a profile's `entries` show on a keyboard of model `key`, and whether it is
    the model's own: its entry, else source_entry's, cut for it (cut_lighting, with
    `effects` and `per_key`), else the defaults. With `follows`, a keyboard that follows
    `leader`, what the leader shows whole, its own entry or the one it starts from, cut for
    it, never its own. With `key` None, before any keyboard, the source's whole. Its own entry
    and the source's whole are the profile's own objects, not copies."""
    own = None if key is None or follows else own_entry(entries, key)
    if own is not None:
        return own, True
    source = source_entry(entries, leader if follows else key, leader)
    entry = Lighting() if source is None else source
    model = model_named(key)
    return (entry if model is None else cut_lighting(entry, model, effects, per_key)), False

#: The ids a keyboard reports on its own cable; the others are its receiver's. They decide
#: the route, never the interface: cabled, the keyboard is on external power and its battery
#: is not asked; on the receiver the cable is looked for by these ids, and on a receiver's
#: own node the link notices are turned on (RECEIVER_NODE_IDS).
CABLED_IDS = frozenset({0xC33E, 0xC358, 0xC355, 0xC343, 0xC33F, 0xC352, 0xC336,
                        0xC359, 0xC356, 0xC357, 0xC361, 0xC36B, 0xC35F, 0xC360})

#: The ids of a receiver's own node, which a keyboard is reached through on its slot, and
#: where the receiver's registers are: its link notices are turned on there
#: (Link.turn_on_link_notices). A keyboard on a node of its own behind its receiver, the
#: G915 TKL's 408E or the G515 LIGHTSPEED TKL's 40B4, is not on it: hid-logitech-dj writes the
#: keyboard's slot into every request sent there (hidpp.attached_index), so a register request
#: would go to the keyboard. That driver turns the receiver's link notices on itself
#: (hidpp.NOTIFICATION_FLAGS) and hands the keyboard's node each one for its slot.
RECEIVER_NODE_IDS = frozenset({0xC541, 0xC545})


def waited_index(found: tuple[Path, int, int]) -> int:
    """The device index the node `found`, (node, product, derived index), is kept open on
    while nothing answers its keyboard's probe (Waiting.wait_for): the derived one, but the
    first receiver slot on a receiver's own node (RECEIVER_NODE_IDS), whose derived 0xFF is
    the receiver itself. A channel there drops each report of the keyboard's slot, its
    power-on report and its link notices among them; hidpp.find_index found the G915 on slot
    1 of its c541."""
    return RECEIVER_SLOTS[0] if found[1] in RECEIVER_NODE_IDS else found[2]


#: Capability to the feature that provides it, in the order front ends list them.
CAPABILITIES = (
    ("g_keys", F_GKEY),
    ("m_keys", F_MKEYS),
    ("mr", F_MR),
    ("brightness", F_BRIGHTNESS),
    ("per_key", F_PERKEY),
    ("onboard", F_ONBOARD),
)
#: The capabilities of a keyboard with G keys Gearbench binds (capabilities): the G213 has
#: neither.
BOUND_KEY_CAPABILITIES = frozenset({"g_keys", "assigned_keys"})


def name(product: int | None) -> str:
    return MODELS[product] if product else "keyboard"


def read_battery(dev: Hidpp, product: int | None, name: str) -> dict | None:
    """battery.read against the cell of `product`'s model (CELLS): a 0x1001 reading with no
    cell gives volts and no percent."""
    return read_any_battery(dev, None if product is None else CELLS.get(product), name)


def find_attached_keyboard(cabled_only: bool = False, same_model_as: int | None = None,
                           leader: str | None = None) -> tuple[Path, int, int] | None:
    """The keyboard's vendor node as (node, product, index), on its model's interface: by
    PRODUCT_IDS' order, or with `cabled_only` among CABLED_IDS alone, lowest id first, the ids
    of the model keyed `leader` (model_key) first either way, and with `same_model_as` among
    the ids of that product's model alone. From sysfs, over one walk; nothing is opened."""
    products = sorted(CABLED_IDS) if cabled_only else PRODUCT_IDS
    # sorted is stable: the leader's ids, and the others, keep their order.
    found = find_each_attached({
        product: MODEL_OF[product].interface
        for product in sorted(products, key=lambda product: model_key(product) != leader)
        if same_model_as is None or MODEL_OF[product] is MODEL_OF[same_model_as]})
    return found[0] if found else None


def attached_keyboards() -> list[tuple[Path, int, int]]:
    """Each keyboard model plugged in now, in MODEL_KEYS' order, by the node
    find_attached_keyboard finds for it: the first of its ids with a node on its model's
    interface, by PRODUCT_IDS' order, so a receiver's own node counts with its keyboard off.
    From sysfs, over one walk; nothing is opened."""
    first: dict[str, tuple[Path, int, int]] = {}
    # PRODUCT_IDS holds each model's ids together, so the models come in MODEL_KEYS' order.
    for found in find_each_attached({product: MODEL_OF[product].interface
                                     for product in PRODUCT_IDS}):
        first.setdefault(model_key(found[1]), found)
    return list(first.values())


class Opening(TypedDict):
    """hidpp.open_device's witness and options for a keyboard's node, by keyword."""

    witness: int
    prefer: int
    long_only: bool


def opening(product: int, derived: int) -> Opening:
    """How the node attached_keyboards found for a keyboard of id `product` is opened
    (hidpp.open_device): on its model's witness, the derived device index first, and
    long-only where its model says so."""
    model = MODEL_OF[product]
    return {"witness": model.probe, "prefer": derived, "long_only": model.long_only}


def capabilities(dev: Hidpp, model: KeyboardModel) -> list[str]:
    """What this keyboard of `model` can do, from its own feature table: CAPABILITIES, then
    `battery` for any of the battery features (battery.has_battery), which the wired G515 TKL
    lacks. A model whose G keys type its own keys while unbound
    (KeyboardModel.g_key_defaults) has `typed_keys` after `g_keys`: its stop hands it its
    onboard profile back. A model whose G keys bind through its custom assignments
    (KeyboardModel.custom_assignments) has `assigned_keys` where that write has passed its
    test and the keyboard has both of its features.

    Not lighting: a device can advertise the feature with nothing behind it, so the
    daemon adds `lighting` once clusters enumerate.
    """
    able = [able for able, feature in CAPABILITIES if dev.feature(feature) is not None]
    if model.g_key_defaults:
        # Found by its 0x8010 (WITNESS), such a keyboard has g_keys.
        able.insert(able.index("g_keys") + 1, "typed_keys")
    if (model.custom_assignments and model.assignments_tested
            and all(dev.feature(feature) is not None for feature in (F_KEY_CONTROL, F_FILES))):
        able.append("assigned_keys")
    return able + ["battery"] if has_battery(dev) else able


def m_profiles_in_menu(able: Collection[str]) -> bool:
    """Whether a keyboard that can do `able` (capabilities) switches its M profiles from the
    tray's menu and the window's M profile group: lighting and no M keys (the owner's decision
    of 2026-10-18)."""
    return "lighting" in able and "m_keys" not in able


def bound_as(product: int | None, key: str) -> str:
    """The name G key `key` ("G1"), as 0x8010 or custom assignments report it, binds under on
    the keyboard of id `product` (KeyboardModel.g_key_names): "F1" on the G915 TKL, "G515-F1"
    on the G515s. `key` itself past the model's names, and with no keyboard found yet."""
    names = () if product is None else MODEL_OF[product].g_key_names
    number = int(key[1:])
    return names[number - 1] if number <= len(names) else key


def keystroke(product: int | None, name: str) -> str | None:
    """What the G key bound under `name` types on the keyboard of id `product` while nothing
    is bound to it, in a binding's spelling: "f1" for the G915 TKL's F1
    (KeyboardModel.g_key_defaults). None for a model whose unbound G keys type nothing, for a
    name it has no default for, and with no keyboard found yet."""
    model = None if product is None else MODEL_OF[product]
    return None if model is None else dict(zip(model.g_key_names, model.g_key_defaults)).get(name)


def own_named_keys(keys: Collection[str]) -> list[str]:
    """The keys of each group bound under names of their own (config.OWN_KEYS) whose first
    `keys` holds, with the models whose keys they are, for a line: "G915 TKL's F1 to F12"."""
    return [f"{models.removeprefix('the ')}'s {names[0]} to {names[-1]}"
            for names, models in OWN_KEYS if names[0] in keys]


def assignments_untested(product: int | None) -> bool:
    """Whether the keyboard of id `product` has G keys that bind through its custom
    assignments (KeyboardModel.custom_assignments) whose write has not passed its test on
    this hardware (KeyboardModel.assignments_tested). False with no keyboard found yet."""
    model = None if product is None else MODEL_OF[product]
    return model is not None and model.custom_assignments and not model.assignments_tested


def untested_assignment_words(product: int) -> str:
    """Why the G keys of the keyboard of id `product`, where assignments_untested, take no
    binding yet, for a note and `status`: its F keys on the G515s, the G915 X's G keys."""
    model = MODEL_OF[product]
    return (f"Gearbench does not set the {model.name}'s {model.own_key}s to their bindings "
            f"until that is tested on this model, so they {model.unbound}")


def typed_words(product: int | None) -> str | None:
    """What the G keys of the keyboard of id `product` type while unbound, for a line or a
    label: "F1 to F12" on the G915 TKL. None where keystroke gives none."""
    typed = () if product is None else MODEL_OF[product].g_key_defaults
    return f"{typed[0].upper()} to {typed[-1].upper()}" if typed else None
