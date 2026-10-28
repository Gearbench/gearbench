"""The Centurion headsets: the Astro A50 X (046d:0b0b) and the Astro A50 Gen 5 (046d:0b1c),
whose base station speaks Centurion (gearbench.centurion) on its own HID interface, and the
PRO X 2 LIGHTSPEED, the G522 LIGHTSPEED and the G325 LIGHTSPEED, reached through their
receiver's bridge, the G522 on its cable too. Each model is a record (Model): its id and
interface, how a frame starts on it, and the features it has, each built by its feature's
module (gearbench.centurion_features) in the layout of the version the record names; and
`A50X` is a model's channel and the requests it sends (Model.sent). Nothing here imports
the daemon.

The A50 X's frames, features, reads and events are measured: Gearbench's reads of one on
2026-10-21 (the first connect's steps 1 to 6, firmware before 1.3.21), and G HUB 39.1.2's
traffic with the same unit on firmware 1.3.21 that day, each of its settings changed once
(the captures, kept outside the repository, by section: B3 the table, B4 each setting). The
base station answers every lookup and every read with the headset switched off, docked or
not: it holds the feature table and the settings. The A50 Gen 5's frames are HeadsetControl
PR 531's, checked on one; Logitech's release of 2024-09-17 says the Gen 5 "is based on the
same architecture" as the A50 X, and the Gen 5 takes the A50 X's measured layouts where PR
531's frames agree with them, and its own where they do not. Each feature id is G HUB's (its
agent's names, which Solaar's hidpp20_constants.py gives the same ids), looked up through the
root feature, never taken from the index one firmware gave.

Every write is refused with nothing sent (not_tested) until it is let through by its model's
writes_tested: the owner's rule of 2026-10-17 for a model not tested on this hardware. The
A50 X keeps each setting it was sent through a power cycle (the captures, B6), so its writes
waited for the write test with the owner, which lets each through (the owner's decision of
2026-10-21); the A50 Gen 5's stay refused. That test passed on 2026-10-22, so the A50 X is
`tested` too (the owner's decision of 2026-10-22). A write to a feature the lookup found at
another version than its record's is refused with nothing sent too (A50X.layout_refused, the
owner's decision of 2026-10-25), since that version's layout may differ.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import centurion_features
from .battery import BASE_CHARGE, CABLE_CHARGE
from .centurion import BRIDGE, Bridged, Centurion, Framing, Link, Message
from .centurion_features import (
    auto_sleep,
    battery,
    eco_mode,
    equalizer,
    led_brightness,
    mix,
    mixer,
    noise_gate,
    noise_reduction,
    onboard_eq,
    power_mode,
    sidetone,
    tones,
    volume,
)
from .centurion_features.equalizer import Band
from .centurion_features.mixer import Mix
from .centurion_features.setting import Setting
from .hidpp import (
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    Feature,
    HidppFailure,
    find_each_attached,
)
from .wording import word_list, written

#: The A50s' prefix, report 0x02 and the byte 0x0C: the A50 X's report descriptor declares
#: report 0x02 alone, on usage page 0xFF32, and every reply starts `02 0c` (2026-10-21, sysfs
#: and the captures); HeadsetControl PR 531 gives the Gen 5's ("byte[1] = 0x0c constant
#: marker"; the 0x50 variant's address byte stands there in Solaar's). PR 531's report 0x51
#: for the A50 X is not the A50 X's.
FRAMING = Framing(0x02, 0x0C)


#: The vendor-defined usage pages, 0xFF00 to 0xFFFF (HID Usage Tables 1.5, section 3): the
#: A50s' control collection is on 0xFF32 (the A50 X's report descriptor; HeadsetControl PR
#: 531 for the Gen 5), the receivers' on 0xFFA0 (RECEIVER_INTERFACE).
VENDOR_PAGES = 0xFF00
#: The control collection's USB interface: 8 on the A50 X (lsusb, 2026-10-21, of its nine:
#: Voice, Mic, Game and Stream Out, each a control and a streaming interface, then the HID
#: one), and on the Gen 5 (PR 531, and a second owner in its thread).
INTERFACE = 8

#: The slot each A50 instance plays, the one fn 1 reads and G HUB writes; PR 531 wrote the
#: Gen 5's headphones there too. Slots 0 to 2 hold G HUB's three presets of the instance, and
#: choosing one copies its bands into this slot (B4-06).
PLAYING_SLOT = 3
#: The A50s' instances, G HUB's `unifiedEqualizer` names for 0 and 1: the A50 X's 0 has the
#: microphone's bands, 80 Hz to 19 kHz, and 1 the headphones', 20 Hz to 20 kHz.
INSTANCES = {"microphone": 0, "headphones": 1}
INSTANCE_NAMES = {"microphone": "Microphone", "headphones": "Headphones"}
#: The equalizer 0x0636 holds on a model with `onboard_eq`: the headphones'.
ONBOARD = "headphones"
#: The presets in slots 0, 1 and 2, by G HUB's names for them: the bands G HUB wrote for
#: Gaming and Broadcast are the A50 X's slot 1 and slot 2 (B4-06), and its slot 0 is flat.
PRESETS = {"microphone": ("default", "competition", "broadcast"),
           "headphones": ("default", "gaming", "media")}
#: The writes that are no setting: an instance's ten bands, and the whole stream mix.
EQUALIZER_WRITE = "equalizer"
MIXER_WRITE = "mixer"


@dataclass(frozen=True)
class Model:
    """One model: its key, the name the settings know it by, which names its library's file
    (a50x_library) and Gearbench Spatial's pick of it (spatial.py's keys, which the tests
    hold alike); its name, its id, the USB interface its control collection is on, the
    framings it may use, the first its report descriptor declares taken (framing_for), its
    settings, each instance's presets in slots 0 to 2 by name, the writes its hardware test
    let through, by their names in `writes`, and whether Gearbench has run on one. Every
    other write is refused with nothing sent (not_tested); until it has run, the front ends
    and the journal ask for a device report from it. `instances` are its 0x020D equalizers'
    bytes by name, none for a model without 0x020D, and `slot` the one they play; `onboard_eq`,
    whether its headphones' equalizer is 0x0636's, read alone; `mixer`, whether it has the
    A50 X's stream mix and microphone boom; `versions` gives, by feature id, the version
    whose layout its record takes. `bridged` is a model reached through its receiver's
    bridge, and `dongle` the words for what plugs into the computer, with `charge` the
    sentence that ends its low battery notice."""

    key: str
    name: str
    product: int
    interface: int
    framings: tuple[Framing, ...]
    settings: tuple[Setting, ...]
    presets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    writes_tested: frozenset[str] = frozenset()
    tested: bool = False
    instances: Mapping[str, int] = field(default_factory=lambda: INSTANCES)
    slot: int = PLAYING_SLOT
    onboard_eq: bool = False
    mixer: bool = False
    versions: Mapping[int, int] = field(default_factory=dict)
    bridged: bool = False
    dongle: str = "base station"
    charge: str = BASE_CHARGE

    def framing_for(self, reports: Mapping[int, int]) -> Framing | None:
        """The first of `framings` whose report id the node's report descriptor declares on a
        vendor page, `reports` being report.Descriptor's: each id with its collection's
        usage page. None for none."""
        return next((framing for framing in self.framings
                     if reports.get(framing.report_id, 0) >= VENDOR_PAGES), None)

    def setting(self, key: str) -> Setting:
        """The model's setting with the socket word `key`, one of its settings' keys."""
        return next(setting for setting in self.settings if setting.key == key)

    def setting_of(self, feature_id: int) -> Setting | None:
        """The model's setting on the feature `feature_id`, None for a feature that holds
        none."""
        return next((setting for setting in self.settings if setting.feature == feature_id),
                    None)

    @property
    def features(self) -> tuple[int, ...]:
        """The features its record lists, in the order find_features looks them up."""
        return (battery.ID, *(setting.feature for setting in self.settings),
                *((equalizer.ID,) if self.instances else ()),
                *((onboard_eq.ID,) if self.onboard_eq else ()),
                *((mixer.ID,) if self.mixer else ()))

    @property
    def equalizers(self) -> tuple[str, ...]:
        """The equalizers it has, by instance: those of 0x020D, then the headphones' of
        0x0636."""
        return (*self.instances, *((ONBOARD,) if self.onboard_eq else ()))

    def onboard(self, instance: str) -> bool:
        """Whether `instance`'s equalizer is 0x0636's, read alone, not one of 0x020D's."""
        return self.onboard_eq and instance == ONBOARD

    @property
    def writes(self) -> frozenset[str]:
        """Every write the daemon may send it: each setting's, the equalizer's and the stream
        mix's."""
        return frozenset({*(setting.key for setting in self.settings),
                          *((EQUALIZER_WRITE,) if self.instances else ()),
                          *((MIXER_WRITE,) if self.mixer else ())})

    @property
    def sent(self) -> frozenset[tuple[int, int]]:
        """Every (feature id, function) its channel, `A50X`, sends after the root lookups of
        `find_features`: each read, and each write `writes_tested` lets through; its
        `request` refuses any other before anything is written."""
        sent = {(battery.ID, battery.READ),
                *((setting.feature, setting.read) for setting in self.settings),
                *((setting.feature, setting.write) for setting in self.settings
                  if setting.key in self.writes_tested)}
        if self.instances:
            sent.add((equalizer.ID, equalizer.FN_GET))
            if EQUALIZER_WRITE in self.writes_tested:
                sent.add((equalizer.ID, equalizer.FN_SET))
        if self.presets:
            sent.add((equalizer.ID, equalizer.FN_SLOT))
        if self.onboard_eq:
            sent.add((onboard_eq.ID, onboard_eq.FN_GET))
        if self.mixer:
            sent |= {(mixer.ID, mixer.FN_BOOM), (mixer.ID, mixer.FN_READ)}
            if MIXER_WRITE in self.writes_tested:
                sent.add((mixer.ID, mixer.FN_WRITE))
        return frozenset(sent)


#: The versions the A50 X lists, by feature id (its table, the Linux reads' step 4 and the
#: captures' B3, the same before firmware 1.3.21 and on it).
A50_X_VERSIONS = {battery.ID: 4, sidetone.ID: 2, volume.ID: 4, mix.ID: 2, noise_gate.ID: 1,
                  equalizer.ID: 1, auto_sleep.ID: 3, power_mode.ID: 1, led_brightness.ID: 1,
                  tones.ID: 1, mixer.ID: 1}
#: The sidetone, the volume and the balance, the same on both A50s (the A50 X's, measured;
#: PR 531's on the Gen 5).
SHARED_SETTINGS = (sidetone.setting(7, A50_X_VERSIONS[sidetone.ID]), volume.setting(),
                   mix.setting())
#: The settings the A50 X's captures measured after the noise gate, in G HUB's order on its
#: Settings page: the auto sleep, then the deep sleep mode, then these. G HUB gives the A50
#: Gen 5 all but the deep sleep mode too (its record's autoSleep, ledBrightness and
#: hasHeadsetTones; it has no hasEuPowerModeControl), whose published frames give none of
#: them: there they are the A50 X's, assumed, as is its stream mix (its record's
#: hasAudioMixingMatrix).
LATER_SETTINGS = (led_brightness.setting("base station's brightness"), tones.setting())
#: The A50s' auto sleep, in minutes: G HUB's four choices for the A50 X, never, 15, 30 and
#: 60 (B4-08).
A50_SLEEP_MINUTES = (0, 15, 30, 60)

#: The A50 X's noise gate: off 00, home 01, night 02, tournament 04 (G HUB's writes and the
#: base's events, B4-07, and its fn 3, which lists 00 01 02 04).
A50_X_SETTINGS = (*SHARED_SETTINGS, noise_gate.setting((0x00, 0x02, 0x01, 0x04)),
                  auto_sleep.setting(A50_SLEEP_MINUTES, A50_X_VERSIONS[auto_sleep.ID]),
                  power_mode.setting(), *LATER_SETTINGS)
#: Its writes, every setting, the equalizer and the stream mix, let through by its write test
#: with the owner, which passed on 2026-10-22.
A50_X = Model("a50x", "Astro A50 X", 0x0B0B, INTERFACE, (FRAMING,), A50_X_SETTINGS, PRESETS,
              mixer=True, versions=A50_X_VERSIONS, tested=True,
              writes_tested=frozenset({*(setting.key for setting in A50_X_SETTINGS),
                                       EQUALIZER_WRITE, MIXER_WRITE}))
#: The Gen 5's: off 00, night 01 and tournament 04 as PR 531 sent them, home 02 as
#: HeadsetControl-A50-GUI does, which differ from the A50 X's. Its presets are not read: no
#: published frame of the Gen 5 reads a slot with fn 5. Its versions are not published: the
#: A50 X's are assumed.
A50_GEN5 = Model("a50gen5", "Astro A50 Gen 5", 0x0B1C, INTERFACE, (FRAMING,),
                 (*SHARED_SETTINGS, noise_gate.setting((0x00, 0x01, 0x02, 0x04)),
                  auto_sleep.setting(A50_SLEEP_MINUTES, A50_X_VERSIONS[auto_sleep.ID]),
                  *LATER_SETTINGS), mixer=True, versions=A50_X_VERSIONS)
#: The receivers' framings: report 0x51 with no byte after it on the PRO X 2 LIGHTSPEED's
#: (Solaar's; HeadsetControl's), report 0x50 and the address 0x23 on the G522's and the
#: G325's (HeadsetControl's G522 driver; the G325's owner, HeadsetControl issue 581; Solaar
#: learns the address from the first frame it reads, 0x23 on both). Each is the report the
#: receiver's report descriptor declares, as Solaar finds it: 63 bytes each way.
PLAIN = Framing(0x51)
ADDRESSED = Framing(0x50, 0x23)
#: Their control collection's USB interface, 3, on usage page 0xFFA0 (HeadsetControl's PRO
#: X 2 LIGHTSPEED and G522 drivers; HeadsetControl issue 581's G325 lists it there beside a
#: consumer control collection and another vendor page, 0xFF13).
RECEIVER_INTERFACE = 3

#: The PRO X 2 LIGHTSPEED (046d:0af7) through its receiver's bridge, at index 3 (Solaar's
#: listing of one, on Solaar 1.1.19, and its PR 3150, checked on one; HeadsetControl PR 512).
#: The headset lists 0x0104, 0x0108, 0x0604, 0x0602 and 0x0636 among its ten features, and
#: no 0x020D on that unit, whose equalizer is 0x0636's five bands; HeadsetControl tries
#: 0x020D in its version 0 layout, of bands of three bytes, first, which no source has read
#: off one, and is left out. The listing gives every feature version 0, before Solaar read
#: their versions: the record's are not published. Its sidetone is 0 to 100 in version 1's
#: layout (HeadsetControl's 100 steps, Solaar's 101; the listing read 55), and its auto
#: sleep one byte of minutes, 0 to 255 (HeadsetControl's range; the listing read 10).
PRO_X_2 = Model("pro_x_2", "PRO X 2 LIGHTSPEED", 0x0AF7, RECEIVER_INTERFACE, (PLAIN,),
                (sidetone.setting(101, version=1),
                 auto_sleep.setting(tuple(range(256)), version=1), noise_reduction.setting()),
                instances={}, onboard_eq=True, bridged=True, dongle="receiver",
                charge=CABLE_CHARGE)
#: The headphones' equalizer, instance 0, slot 0, of the G522 and the G325 (G HUB's
#: `unifiedEqualizer` names instance 0 HEADPHONES_LABEL on both; the G325's getCustomEQ of
#: slot 0 answered and of slot 1 error 0x0b, and its function 3 said slot 0; Solaar: the
#: G522 "advertises 16 slots but only honors slot 0").
HEADPHONES_ONLY = {"headphones": 0}

#: The versions the G522 lists, by feature id, from the sources its record names.
G522_VERSIONS = {battery.ID: 3, sidetone.ID: 2, equalizer.ID: 2, auto_sleep.ID: 4, eco_mode.ID: 1}
#: The G522 LIGHTSPEED, through its receiver (046d:0b18) and on its cable (046d:0b19, with no
#: bridge), one table on both (Solaar's listings of one, firmware 7.126 and 8.137, and its
#: issue 3181; HeadsetControl's G522 driver, its pull request 592): 05 the battery version 3,
#: 0d the sidetone version 2, 0 to 9 (HeadsetControl's ten steps), 0f the equalizer version
#: 2, 14 the auto sleep version 4, its minutes and the two lights' times, and 19 the eco
#: mode version 1. Its auto sleep takes any byte: HeadsetControl offers 0 to 90 minutes,
#: and Solaar's listing read 148 on firmware 8.137, for the first connect to settle. Left
#: out: its tones (0x060B version 2, whose only published function writes voice prompts),
#: the microphone's mute (0x0601, which the two sources read differently), its lights
#: (0x0620 to 0x0622), its microphone's gain (0x0611, its range read from the headset), its
#: voice filters (0x0900 to 0x0908) and its microphone test (0x020E). The cable's interface
#: is taken to be the receiver's, 3.
G522 = Model("g522", "G522 LIGHTSPEED", 0x0B18, RECEIVER_INTERFACE, (ADDRESSED,),
             (sidetone.setting(10, G522_VERSIONS[sidetone.ID]),
              auto_sleep.setting(tuple(range(256)), G522_VERSIONS[auto_sleep.ID]),
              eco_mode.setting()),
             instances=HEADPHONES_ONLY, slot=0, versions=G522_VERSIONS,
             bridged=True, dongle="receiver", charge=CABLE_CHARGE)
G522_CABLE = replace(G522, product=0x0B19, bridged=False, dongle="cable")
#: The versions the G325 lists, by feature id, from the sources its record names.
G325_VERSIONS = {battery.ID: 3, sidetone.ID: 2, auto_sleep.ID: 2, noise_reduction.ID: 1,
                 equalizer.ID: 2, eco_mode.ID: 1}
#: The G325 LIGHTSPEED (046d:0b21) through its receiver (its owner's battery frames,
#: HeadsetControl issue 581; Solaar's logs of one, firmware 8.129, attached to Solaar issue
#: 3204): 07 the battery version 3, 08 the sidetone version 2, whose function 2 gives ten
#: levels, 09 the auto sleep version 2, one byte, 0d the microphone's noise suppression, 0f
#: the equalizer version 2 and 12 the eco mode. Left out: its tones (0x060B version 2, as
#: the G522's), its microphone's mute (0x0601) and its noise exposure (0x060D), which no
#: source reads.
G325 = Model("g325", "G325 LIGHTSPEED", 0x0B21, RECEIVER_INTERFACE, (ADDRESSED,),
             (sidetone.setting(10, G325_VERSIONS[sidetone.ID]),
              auto_sleep.setting(tuple(range(256)), G325_VERSIONS[auto_sleep.ID]),
              noise_reduction.setting(), eco_mode.setting()),
             instances=HEADPHONES_ONLY, slot=0, versions=G325_VERSIONS,
             bridged=True, dongle="receiver", charge=CABLE_CHARGE)
#: 0b09 is the A50 X's firmware-update mode (G HUB's firmware package lists it beside 0b0b
#: with "force": true, and it never showed in use, 2026-10-21), left out, as the Gen 5's
#: second id, 0b1a, is.
MODELS = {model.product: model
          for model in (A50_X, A50_GEN5, PRO_X_2, G522, G522_CABLE, G325)}
PRODUCTS = {product: model.name for product, model in MODELS.items()}
#: The ids in the order the daemon tries the nodes it finds (find_each_a50x): the records'
#: order, but a model's cable before its receiver, so a G522 on its cable is driven there with
#: its receiver plugged in too, as a keyboard and a mouse are.
SEARCH_ORDER = tuple(sorted(MODELS, key=lambda product: (
    [model.key for model in MODELS.values()].index(MODELS[product].key), MODELS[product].bridged)))
#: Every model's settings by their socket words, the first model's of each word: a word
#: means one setting on each model, which only its ranges and bytes tell apart.
SETTING_OF = {setting.key: setting for model in reversed(MODELS.values())
              for setting in model.settings}


def not_tested(name: str, what: str) -> str:
    """The refusal of a write to a model whose writes are not tested on this hardware."""
    return (f"nothing was sent: Gearbench does not change the {name}'s {what} until that is "
            "tested on this model")


def not_listed(model: Model, feature_id: int) -> str:
    """The refusal of a request to a feature of the model's that its lookup did not find."""
    return (f"the {model.name} did not list its {centurion_features.WORDS[feature_id]} "
            f"(feature 0x{feature_id:04x}), so Gearbench cannot read or set it")


def other_layout(model: Model, feature_id: int, found: int) -> str:
    """The refusal of a write to a feature the lookup found at version `found`, another than
    the one whose layout the model's record takes (Model.versions)."""
    return (f"nothing was sent: the {model.name} lists its "
            f"{centurion_features.WORDS[feature_id]} at version {found}, and Gearbench writes "
            f"it only in the layout of version {model.versions[feature_id]}")


#: Each instance an equalizer may have, as a refusal names it, in the order it names them.
OWNERS = {"headphones": "the headphones'", "microphone": "the microphone's"}


def equalizer_refused(model: Model, instance: object, names: Collection[str]) -> str | None:
    """Why `instance` is none of `names`, the model's equalizers that are shown
    (Model.equalizers) or written (Model.instances), or None for one of them."""
    if isinstance(instance, str) and instance in names:
        return None
    owners = [OWNERS[name] for name in OWNERS if name in names]
    return f"the {model.name}'s equalizer is {word_list(owners)}, not {written(instance)}"


def preset_refused(model: Model, instance: str, name: object) -> str | None:
    """Why `name` is not one of the model's presets of `instance`, or None for one it is."""
    names = model.presets.get(instance, ())
    if isinstance(name, str) and name in names:
        return None
    if not names:
        return f"Gearbench reads no presets of the {model.name}'s {instance} equalizer"
    return f"the presets of the {instance} are {word_list(names)}, not {written(name)}"


def writes_refused(model: Model) -> str | None:
    """What the front ends say before a click of a model whose writes are refused, or None
    for one whose writes are all tested."""
    if model.writes <= model.writes_tested:
        return None
    return f"Gearbench changes nothing on the {model.name} until that is tested on this model"


def find_each_a50x() -> list[tuple[Path, int]]:
    """Each model's control node found and its product id, read from sysfs by the vid, pid
    and interface the models' udev rules match, in SEARCH_ORDER; opens nothing."""
    found = find_each_attached({product: MODELS[product].interface for product in SEARCH_ORDER})
    return [(path, product) for path, product, _index in found]


def cable_of(model: Model) -> Model | None:
    """The record of `model`'s headset on its cable, for a model reached through its
    receiver that has one, the G522's G522_CABLE; None for any other."""
    return next((each for each in MODELS.values() if model.bridged and not each.bridged
                 and each.key == model.key), None)


#: Why a receiver whose root lookup lists no bridge is not set up.
NO_BRIDGE = f"its receiver lists no bridge, feature 0x{BRIDGE:04x}"


def open_a50x(path: str | os.PathLike[str], model: Model, framing: Framing) -> A50X:
    """Open the node and wrap it; raises PermissionError without the rule."""
    return A50X(Centurion(path, framing), model)


class A50X:
    """A model's channel and the requests its record sends (Model.sent), each feature at the
    index the root lookup found."""

    def __init__(self, dev: Centurion, model: Model) -> None:
        #: The node's channel, and the channel the requests go on: the node's, or for a
        #: bridged model the headset's through the receiver's bridge, once find_features
        #: found it.
        self.receiver = dev
        self.dev: Centurion | Bridged = dev
        self.model = model
        #: Each feature the model lists as its lookup found it, None for one it lacks; a
        #: feature not looked up yet is not here.
        self.features: dict[int, Feature | None] = {}

    @property
    def fileno(self) -> int:
        return self.dev.fileno

    def close(self) -> None:
        self.dev.close()

    @property
    def framing(self) -> Framing:
        return self.dev.framing

    @property
    def pending(self) -> bool:
        """Whether events a request read while it waited are queued (Centurion.pending)."""
        return self.dev.pending

    def pump(self) -> None:
        """Read what the node has queued, sending nothing: events kept, late replies
        dropped."""
        self.dev.pump()

    def next_event(self) -> Message | Link | None:
        """The next event queued, the bridge's connection event among them on a bridged
        model (Bridged.next_event)."""
        return self.dev.next_event()

    def request(self, feature_id: int, function: int, params: bytes = b"") -> bytes:
        """One request of the model's, to the index the lookup found; ValueError for any
        other, or for a feature the lookup did not find, before anything is written. Raises
        HidppFailure."""
        if (feature_id, function) not in self.model.sent:
            raise ValueError(f"0x{feature_id:04x} function {function} is not a request "
                             f"Gearbench sends the {self.model.name}")
        return self.dev.request(self.index_of(feature_id), function, params)

    def index_of(self, feature_id: int) -> int:
        """The index the lookup found for `feature_id`; ValueError for one it did not."""
        found = self.features.get(feature_id)
        if found is None:
            raise ValueError(not_listed(self.model, feature_id))
        return found.index

    @property
    def bridge(self) -> int | None:
        """The receiver's bridge's index, None for a model on its own node."""
        return self.dev.index if isinstance(self.dev, Bridged) else None

    def find_features(self) -> None:
        """The root lookup of each feature the model lists, the first on the probe's budget,
        since it is the first request after the node opens, or for a bridged model the first
        to its headset, after the receiver's lookup of its bridge, the headset's answer
        waited for as long as the budget's tries take together; kept as `features`. Raises
        HeadsetAway for a headset that does not answer, and HidppFailure, a receiver that
        lists no bridge among them."""
        timeout = PROBE_TIMEOUT
        if self.model.bridged:
            found = self.receiver.lookup(BRIDGE, timeout=PROBE_TIMEOUT, retries=PROBE_RETRIES)
            if found is None:
                raise HidppFailure(NO_BRIDGE)
            self.dev = Bridged(self.receiver, found.index)
            # Bridged spends the tries, each `timeout` long, on the receiver's acknowledgment,
            # then waits one `timeout` for the headset's answer without sending again.
            timeout = PROBE_TIMEOUT * (PROBE_RETRIES + 1)
        first, *rest = self.model.features
        self.features[first] = self.dev.lookup(first, timeout=timeout, retries=PROBE_RETRIES)
        for feature_id in rest:
            self.features[feature_id] = self.dev.lookup(feature_id)

    def listed(self, feature_id: int) -> bool:
        return self.features.get(feature_id) is not None

    def other_versions(self) -> list[tuple[int, int, int]]:
        """Each feature found at another version than the one whose layout the record takes,
        as (feature id, the version found, the record's)."""
        return [(feature_id, found.version, self.model.versions[feature_id])
                for feature_id, found in self.features.items()
                if found is not None and feature_id in self.model.versions
                and found.version != self.model.versions[feature_id]]

    def layout_refused(self, feature_id: int) -> str | None:
        """Why a write to `feature_id` is refused before anything is sent: the lookup found it
        at another version than the record's (other_versions), whose layout may not be the
        record's (other_layout). None otherwise."""
        return next((other_layout(self.model, feature_id, found)
                     for listed, found, _taken in self.other_versions() if listed == feature_id),
                    None)

    def write_refused(self, feature_id: int, write: str, words: str) -> str | None:
        """Why a write to `feature_id`, `write` by its name in Model.writes, is refused before
        anything is sent: the lookup did not find the feature (not_listed), found it at
        another version than the record's (layout_refused), or writes_tested does not name
        it (not_tested, of `words`). None for a write that may go."""
        if not self.listed(feature_id):
            return f"nothing was sent: {not_listed(self.model, feature_id)}"
        if (other := self.layout_refused(feature_id)) is not None:
            return other
        if write not in self.model.writes_tested:
            return not_tested(self.model.name, words)
        return None

    def _refuse(self, feature_id: int, write: str, words: str) -> None:
        if (refused := self.write_refused(feature_id, write, words)) is not None:
            raise ValueError(refused)

    # -- reads -------------------------------------------------------------

    def battery(self) -> dict:
        """The battery in battery.read's shape; UnreadableAnswer for a short reply or a percent
        over 100."""
        return battery.of(self.request(battery.ID, battery.READ))

    def read(self, key: str) -> int | str:
        """The model's setting `key`, one of its settings' keys, as the headset holds it;
        UnreadableAnswer for a value it does not take."""
        setting = self.model.setting(key)
        return setting.decoded(self.request(setting.feature, setting.read))

    def equalizer(self, instance: str) -> tuple[Band, ...]:
        """The bands of the slot an instance plays; UnreadableAnswer for a reply it cannot
        read."""
        return equalizer.bands_of(self.request(
            equalizer.ID, equalizer.FN_GET, bytes([self.model.instances[instance],
                                                   self.model.slot])))

    def onboard_equalizer(self) -> tuple[Band, ...]:
        """The headphones' bands of 0x0636; UnreadableAnswer for a reply it cannot read."""
        return onboard_eq.bands_of(self.request(onboard_eq.ID, onboard_eq.FN_GET,
                                                bytes([onboard_eq.SLOT])))

    def mix(self) -> Mix:
        """The stream port's mix; UnreadableAnswer for a reply it cannot read."""
        return mixer.mix_of(self.request(mixer.ID, mixer.FN_READ, bytes([mixer.OUTPUT])))

    def boom(self) -> bool:
        """Whether the microphone's boom is down, the microphone live; UnreadableAnswer for a
        reply too short."""
        return mixer.live(self.request(mixer.ID, mixer.FN_BOOM))

    def presets(self, instance: str) -> dict[str, tuple[Band, ...]]:
        """The model's presets of an instance, each its slot's bands by its name;
        UnreadableAnswer for a reply it cannot read."""
        return {name: equalizer.bands_of(self.request(
                    equalizer.ID, equalizer.FN_SLOT,
                    bytes([self.model.instances[instance], slot])), start=0)
                for slot, name in enumerate(self.model.presets.get(instance, ()))}

    # -- writes, each refused until its model's writes_tested names it ---------------

    def write(self, key: str, value: object) -> None:
        """Write the model's setting `key`, its `value` checked first (Setting.checked), and
        refused (write_refused) with nothing sent; a setting whose write sends back other
        bytes of its feature's reads them first, as HeadsetControl's G522 driver does for
        its auto sleep."""
        setting = self.model.setting(key)
        setting.checked(value)
        self._refuse(setting.feature, key, setting.words)
        read = self.request(setting.feature, setting.read) if setting.kept else b""
        self.request(setting.feature, setting.write, setting.params(value, read))

    def write_equalizer(self, instance: str, bands: tuple[Band, ...]) -> None:
        """Write an instance's bands into the slot it plays, refused (write_refused) with
        nothing sent, each gain checked first (G HUB's setCustomEQ, B4-04)."""
        self._refuse(equalizer.ID, EQUALIZER_WRITE, f"{instance} equalizer")
        self.request(equalizer.ID, equalizer.FN_SET, equalizer.written_bands(
            self.model.instances[instance], self.model.slot, bands))

    def write_mix(self, mix: Mix) -> None:
        """Write the whole stream mix, as G HUB does (B4-12), refused (write_refused) with
        nothing sent."""
        self._refuse(mixer.ID, MIXER_WRITE, mixer.WORDS)
        self.request(mixer.ID, mixer.FN_WRITE, mixer.written(mix))

    # -- events ------------------------------------------------------------

    def feature_at(self, index: int) -> int | None:
        """The feature the lookups found at `index`, None for an index they did not give."""
        return next((feature_id for feature_id, found in self.features.items()
                     if found is not None and found.index == index), None)

    def event(self, message: Message) -> tuple[int, int | str | dict | Mix | bool | None] | None:
        """What an event says, as (the feature id, its value): the battery's and each
        setting's function 0 event, in its read's layout (the A50 X's, captures B4 and B5,
        the noise gate's too, which is read with function 1; PR 531's battery event); the
        stream mix's function 2 event, its Mix, and its function 0, whether the microphone is
        live. Any other event of a listed feature gives (the feature id, None): something
        changed that a read would tell. None for an event of an index the lookups did not
        give. Raises UnreadableAnswer."""
        feature_id = self.feature_at(message.index)
        if feature_id is None:
            return None
        if feature_id == mixer.ID and message.function == mixer.EVENT_MIX:
            return feature_id, mixer.echoed(message.params)
        if message.function != 0:
            return feature_id, None
        if feature_id == mixer.ID:
            return feature_id, mixer.live(message.params)
        if feature_id == battery.ID:
            return feature_id, battery.of(message.params)
        setting = self.model.setting_of(feature_id)
        return feature_id, None if setting is None else setting.decoded(message.params)
