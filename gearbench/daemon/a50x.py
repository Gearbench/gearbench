"""The Centurion headsets, the Astro A50 X's or A50 Gen 5's base station and the headsets on a
receiver or a cable: finding one, the framing its report descriptor declares, its features
and what they hold, the events it sends, and the `a50x`, `a50x_set`, `a50x_eq` and
`a50x_mix` commands.

The base speaks Centurion on its own node (gearbench.centurion, gearbench.a50x), as a
receiver does for the headset behind its bridge. The daemon sends it the root lookups of its
model's `features` when it attaches, then only what the model's `sent` names: first a read
of each feature it lists. After that it reads a feature again when asked (`a50x` with `read`),
after a resume or a headset's return to its receiver, after a write to it, and where an
event leaves it unknown. G HUB reads the base when it starts and takes its events after
(the A50 X's captures, B4-13): an event that carries a value is taken as its read, and any
other event of a listed feature, or one it cannot read, has that feature read at the next
pass. A read the base refuses or answers with what Gearbench cannot read forgets the value
it had. The A50 X's base answers for its headset switched off (the captures, B3); a pass
still stops at the first read with no answer, since a base that does not answer leaves each
read to wait out its tries.

A write to a model whose writes are not tested is refused before anything is sent
(a50x.not_tested). The value each setting's first read found is kept by model, across a
replug and a move between the G522's cable and its receiver too, and `reset` writes those
back where their writes are let through.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import selectors
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..a50x import (
    A50X,
    EQUALIZER_WRITE,
    MIXER_WRITE,
    MODELS,
    ONBOARD,
    PRODUCTS,
    Model,
    cable_of,
    equalizer_refused,
    find_each_a50x,
    not_tested,
    open_a50x,
    preset_refused,
    writes_refused,
)
from ..centurion import Framing, HeadsetAway, Link
from ..centurion_features import battery, equalizer, mixer, onboard_eq
from ..centurion_features.equalizer import (
    BAND_COUNT,
    PRESET_Q,
    Band,
    bands_from,
    gain_of,
    preset_of,
    raw_gain,
)
from ..centurion_features.mixer import Mix
from ..centurion_features.setting import Setting
from ..config import read_switch
from ..hidpp import (
    NO_RULE,
    DeviceGone,
    HidppFailure,
    HidppTimeout,
    UnreadableAnswer,
    descriptor_of,
)
from ..report import describe, untested_note
from ..tray import Battery
from ..wording import counted, given_count, reason, route, whole, word_list, written
from .shared import (
    CABLE_LOOK,
    DEVICE_NOT_SET_UP,
    DEVICE_OFF,
    NODE_DENIED,
    AttachNotes,
    BatteryNotices,
    Lookout,
    attached_entry,
    failure_reason,
    hidpp_refusal,
    look_due,
    name_text,
    node_denied,
    said_once,
)

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: No headset found, each model named once in the records' order, with what to plug in.
NO_A50X = (f"no {word_list(list(dict.fromkeys(PRODUCTS.values())))} found; plug in its base "
           "station, receiver or cable")
#: A receiver whose headset did not answer through its bridge at the attach, or that said its
#: headset went away later, in the words every other device takes.
HEADSET_AWAY = DEVICE_OFF.format("{}'s headset") + "; switch it on, or bring it near"
#: The journal's warning of a node this user may not open: the node, the model, its dongle,
#: why.
CANNOT_OPEN = "a50x: cannot open {}, the {}'s {}: {}; " + NO_RULE
#: What `a50x` gives of a headset away with no reply kept (_away_reply).
NOTHING_KEPT: dict[str, object] = {
    "device": None, "framing": None, "bridge": None, "listed": [], "battery": None,
    "battery_at": None, "settings": {}, "equalizer": {}, "presets": {}, "mix": None,
    "mic_live": None}
#: Why a base's report descriptor named no framing Gearbench knows: each report it declares
#: with its usage page, since a known id on another page is not the control's; NO_REPORT for
#: a descriptor that declares none.
NO_FRAMING = "its report descriptor declares no report Gearbench knows on a vendor page, only {}"
NO_REPORT = "its report descriptor declares no report"
NOT_READ = ("the {}'s {} equalizer has not been read, so Gearbench has no bands to change: "
            "read it with `gearbench-ctl audio --read`")
NO_PRESETS = ("the presets of the {}'s {} equalizer have not been read, so Gearbench has none "
              "to copy: read them with `gearbench-ctl audio --read`")
NOTHING_TO_RESET = "no setting of the {} was read before any write, so none is put back"
#: An equalizer write that asks more than one way.
ONE_OF = "give the equalizer ten gains, one of its presets or ten bands, one of the three"
#: A model whose equalizer is 0x0636's, which Gearbench reads and does not write.
EQUALIZER_READ_ONLY = ("Gearbench reads the {}'s equalizer and does not change it: a change "
                       "rewrites the sound filters saved in the headset")
NO_MIX = "the {} has no stream mix that Gearbench drives"
MIX_NOT_READ = ("the {}'s stream mix has not been read, so Gearbench has no mix to change: "
                "read it with `gearbench-ctl audio --read`")
MIX_UNLISTED = "the {}'s stream mix lists no {} source, so Gearbench has none to change"
#: The stream mix's sources and the stream port, by the words a request names them.
MIX_SOURCES = (mixer.STREAM, *mixer.SOURCES)


class A50XHeadset(BatteryNotices):
    """The base station while one is attached, and what it last said."""

    BATTERY_PREFIX = "a50x: "
    BATTERY_POWERED = "is charging"

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.dev: A50X | None = None
        self.path: Path | None = None
        self._lookout = Lookout()
        #: A node refused to this user and why a found base could not be set up, for
        #: problems() and the journal (AttachNotes).
        self._notes = AttachNotes()
        #: A bridged model whose headset is away from its receiver (_headset_away), for
        #: problems() and `a50x`, and the last reply of one that went away, its readings kept
        #: while it does not answer, as a mouse's are (_away_reply).
        self._away: Model | None = None
        self._kept: dict | None = None
        #: The value each setting's first read found, by the model's key, kept across a replug
        #: and a move between the G522's cable and its receiver: what `reset` writes back. And
        #: the settings written before any read found them, whose read after it finds the
        #: daemon's own value.
        self.first: dict[str, dict[str, int | str]] = {}
        self._written: dict[str, set[str]] = {}
        #: When the cable of a headset attached through its receiver is next looked for
        #: (_look_for_cable), 0.0 until a look or look_now sets it, and the cable nodes whose
        #: attach did not go through, left alone while each stays.
        self._cable_look_at = 0.0
        self._cable_tried: set[Path] = set()
        #: The model the battery notices were last armed for, by its key, which the G522's
        #: cable and receiver share: another's attach arms them again.
        self._battery_of: str | None = None
        self._clear()

    def _clear(self) -> None:
        """Forget what the attached base said. The battery notices' state stays: a replug of
        the base does not charge the headset."""
        self.battery: dict | None = None
        self.battery_wall: int | None = None
        #: Each setting's value as last read or written; one not known is not here.
        self.values: dict[str, int | str] = {}
        #: The bands of the slot each instance plays as last read, and the model's presets of
        #: each by name, read once an attach; one not known is not here.
        self.equalizers: dict[str, tuple[Band, ...]] = {}
        self.presets: dict[str, dict[str, tuple[Band, ...]]] = {}
        #: The stream mix and whether the microphone is live, as last read or said; None
        #: while not known.
        self.mix: Mix | None = None
        self.mic_live: bool | None = None
        #: The features to read at the next pass, and the last failure each read said.
        self._owed: set[int] = set()
        self._read_said: dict[str, str] = {}

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"a50x": self._cmd_a50x, "a50x_set": self._cmd_a50x_set,
                "a50x_eq": self._cmd_a50x_eq, "a50x_mix": self._cmd_a50x_mix}

    @property
    def name(self) -> str | None:
        """The attached base's model name, None with none attached."""
        return None if self.dev is None else self.dev.model.name

    def attached_name(self) -> str | None:
        """The base's model, or one whose headset is away behind its receiver, which is
        attached still: its page shows it asleep."""
        return self.name or (self._away.name if self._away is not None else None)

    def answers(self) -> bool:
        """Whether the base attached answers: false while its headset is away behind its
        receiver, as `a50x`'s `answering` gives it."""
        return self.dev is not None

    def status_entry(self) -> dict | None:
        """`status`'s entry for the base attached (attached_entry), or for one whose headset is
        away behind its receiver, with the reading kept; None with none."""
        if self.dev is None and self._away is not None:
            kept = self._kept_of(self._away)
            return attached_entry(self._away.name, None, route(self._away.dongle),
                                  kept.get("battery"), kept.get("battery_at"), answering=False)
        if self.dev is None:
            return None
        model = self.dev.model
        return attached_entry(model.name, str(self.path), route(model.dongle), self.battery,
                              self.battery_wall)

    def tray_battery(self) -> Battery | None:
        """The tray's line for the headset: its last reading while a base is attached, and
        while its headset is away the one kept, with when it was taken."""
        if self.dev is None and self._away is not None:
            kept = self._kept_of(self._away)
            battery = kept.get("battery")
            return None if battery is None else Battery.of(
                "a50x", self._away.name, battery, answering=False, read_at=kept.get("battery_at"))
        if self.name is None or self.battery is None:
            return None
        return Battery.of("a50x", self.name, self.battery)

    def deadlines(self) -> list[float]:
        """The look for a base (Lookout.deadlines), or with one attached through a receiver
        whose headset has a cable, the look for that cable (_look_for_cable) once a look or
        look_now has timed it."""
        if self.dev is None:
            return self._lookout.deadlines(self.d.plugs.pace)
        cable = cable_of(self.dev.model) is not None
        return [self._cable_look_at] if cable and self._cable_look_at else []

    def recheck(self) -> None:
        """After a resume: a missing base looked for at once, and an attached one's features
        read at the next pass, since the headset may have charged or been switched off."""
        self._lookout.recheck()
        if self.dev is not None:
            self._owed |= set(self.dev.features)

    def look_now(self) -> None:
        """Have the next pass look for the cable (_look_for_cable): a node came (Plugs)."""
        self._cable_look_at = time.monotonic()

    # -- attaching ---------------------------------------------------------

    def maybe_connect(self) -> None:
        """Look for a base once the retry is due, or at once when one whose lookup got no
        answer has lost its node; the next look is timed from the end. Attached through a
        receiver, look for the headset's cable instead (_look_for_cable)."""
        if self.dev is not None:
            self._look_for_cable()
            return
        if not self._lookout.due():
            return
        try:
            self.connect()
        finally:
            self._lookout.looked()

    def connect(self) -> None:
        """Attach the first node find_each_a50x finds that can be set up: on the framing its
        report descriptor declares, if it answers the root lookups, then read what it lists.
        One that cannot, a receiver whose headset is away among them, leaves the next to be
        tried: a G522 on its cable with its receiver plugged in too is attached on its cable.
        Never raises."""
        self._notes.looking()
        self._lookout.unanswered = self._away = None
        try:
            found = find_each_a50x()
        except OSError as exc:
            log.debug("a50x: could not look for a base station: %s", exc)
            return
        for path, product in found:
            if self._attach(path, MODELS[product]):
                return

    def _look_for_cable(self) -> None:
        """Move a headset attached through its receiver to its cable once the cable's node is
        there, looked for through sysfs at the looks' pace (Plugs.pace), or at the next pass
        after look_now: the cable is preferred, as a keyboard's and a mouse's are, though the
        headset may still answer through its receiver. One this
        user may not open yet is not tried: its refusal is warned
        of and a problem while the node stays refused (_refused), as a mouse's cable's is, the
        receiver stays attached, and udev's grant brings the look forward. The connect
        tries the cable first; one that does not attach leaves the receiver attached again,
        and is left alone while its node stays. Where neither attaches, the next look is timed
        from this one, and a headset away keeps what it last said, as maybe_connect and
        _linked have it."""
        dev = cast("A50X", self.dev)
        cable, now = cable_of(dev.model), time.monotonic()
        if cable is None or not look_due(now, self._cable_look_at):
            return
        self._cable_look_at = now + self.d.plugs.pace(CABLE_LOOK)
        try:
            found = find_each_a50x()
        except OSError as exc:
            log.debug("a50x: could not look for the %s's cable: %s", dev.model.name, exc)
            return
        nodes = {path for path, product in found if product == cable.product}
        self._cable_tried &= nodes
        node = min(nodes - self._cable_tried, default=None)
        if node is None:
            return
        if node_denied(node):
            # Before the teardown: a node udev never grants would have the receiver read again
            # at every look.
            self._refused(node, cable, os.strerror(errno.EACCES))
            return
        receiver, kept = self.path, self.reply()
        self.teardown()
        self.connect()
        if self.path == node:
            log.info("a50x: the %s moved from its receiver (%s) to its cable (%s)",
                     dev.model.name, receiver, node)
            return
        self._cable_tried.add(node)
        if self.dev is None:
            self._kept = kept
            self._lookout.looked()

    def _attach(self, path: Path, model: Model) -> bool:
        """connect's attach of one node; True once it attached, though a node gone at its
        first read lets it go again. A failure is the problem said, until a node tried after
        it in the same look attaches."""
        try:
            reports = describe(descriptor_of(path)).reports
        except OSError as exc:
            log.debug("a50x: the report descriptor of %s could not be read: %s", path, exc)
            return False
        framing = model.framing_for(reports)
        if framing is None:
            declared = ", ".join(f"0x{report:02x} on page 0x{page:04x}"
                                 for report, page in sorted(reports.items()))
            self._set_up_failed(path, model,
                                NO_FRAMING.format(declared) if declared else NO_REPORT)
            return False
        started = time.monotonic()
        dev = self._open(path, model, framing)
        if dev is None:
            return False
        try:
            dev.find_features()
        except HidppFailure as exc:
            dev.close()
            quiet = isinstance(exc, HidppTimeout)
            self._lookout.unanswered = path if quiet else None
            if isinstance(exc, HeadsetAway):
                self._headset_away(path, model,
                                   "headset did not answer its receiver at the attach")
            # A node gone is no base found.
            elif not isinstance(exc, DeviceGone):
                self._set_up_failed(path, model, "no answer" if quiet else failure_reason(exc))
            self.d.note_slow(started, f"{model.name} connect on {path}", repeats="a50x")
            return False
        self._notes.attached()
        self._lookout.unanswered = self._away = None
        self.dev, self.path, self._kept = dev, path, None
        self.BATTERY_BODY = model.charge
        if model.key != self._battery_of:
            self._battery_of = model.key
            self.battery_rearmed(f"the headset read before the {model.name} is no longer attached")
        self.d.selector.register(dev.fileno, selectors.EVENT_READ, "a50x")
        listed = [f"0x{feature_id:04x}" for feature_id, found in dev.features.items() if found]
        route = "" if dev.bridge is None else f", through its receiver's bridge at {dev.bridge}"
        log.info("a50x: %s connected on %s, frames %s%s, features %s%s", model.name, path,
                 framing.words(), route, ", ".join(listed) or "none",
                 "" if model.tested else f"; {untested_note(model.name)}")
        for feature_id, version, taken in dev.other_versions():
            log.info("a50x: feature 0x%04x of the %s is at version %d; Gearbench reads it in "
                     "the layout of version %d and writes nothing to it", feature_id,
                     model.name, version, taken)
        if (refused := self._call(partial(self._read, set(dev.features)))) is not None:
            log.info("a50x: reading the %s stopped: %s", model.name, refused["detail"])
        self.d.forget_slow("a50x")
        self.d.note_slow(started, f"{model.name} connect on {path}")
        return True

    def _open(self, path: Path, model: Model, framing: Framing) -> A50X | None:
        """The base's channel, or None: a node this user may not open is refused (_refused),
        any other failure is said at debug."""
        try:
            dev = open_a50x(path, model, framing)
        except PermissionError as exc:
            self._refused(path, model, reason(exc))
            return None
        except OSError as exc:
            log.debug("a50x: %s not reachable on %s: %s", model.dongle, path, exc)
            return None
        self._notes.opened(path)
        return dev

    def _refused(self, path: Path, model: Model, why: str) -> None:
        """`model`'s node `path`, which this user may not open, `why` in words: a problem while
        it stays refused, warned of once (AttachNotes.refused)."""
        self._notes.refused(path, NODE_DENIED.format(path, f"{model.name}'s {model.dongle}"),
                            CANNOT_OPEN.format(path, model.name, model.dongle, why))

    def _set_up_failed(self, path: Path, model: Model, why: str) -> None:
        """A found base that could not be set up: a problem, said at info once."""
        self._notes.not_set_up = DEVICE_NOT_SET_UP.format(model.name, why, f"its {model.dongle}")
        self._notes.failed(path, f"a50x: the {model.name} on {path} could not be set up: {why}")

    def _headset_away(self, path: Path, model: Model, why: str) -> None:
        """A bridged model's headset away from its receiver on `path`: a problem, said at
        info once, `why` after the model's name."""
        self._away = model
        self._notes.failed(path, f"a50x: the {model.name}'s {why}")

    def teardown(self) -> str | None:
        """Let go of the base, sending it nothing; what was let go, for shutdown's line, or
        None with none attached. Never raises."""
        dev, name, self.dev = self.dev, self.name, None
        if dev is None:
            return None
        with contextlib.suppress(KeyError, ValueError):
            self.d.selector.unregister(dev.fileno)
        with contextlib.suppress(OSError):
            dev.close()
        self.path = None
        self._clear()
        return f"{name} let go"

    # -- the loop's passes ---------------------------------------------------

    def tend(self) -> None:
        """Once a pass: read what is owed. Never raises."""
        if self.dev is None or not self._owed:
            return
        owed, self._owed = self._owed, set()
        if (refused := self._call(partial(self._read, owed))) is not None:
            log.debug("a50x: a read owed stopped: %s", refused["detail"])

    def pending(self) -> bool:
        """Whether reports a request read are queued, which do not wake select."""
        return self.dev is not None and self.dev.pending

    def pump(self) -> None:
        """The node is readable: each event taken, and late replies dropped. Never raises.
        Dispatch calls it for the node registered while a base is attached, and one_pass for
        the events a request queued."""
        dev = cast("A50X", self.dev)
        try:
            dev.pump()
        except DeviceGone as exc:
            log.info("a50x: the %s went away: %s", dev.model.dongle, failure_reason(exc))
            self.teardown()
            return
        except HidppFailure as exc:
            # Or select would wake for the node forever.
            log.info("a50x: reading the %s's node failed (%s); letting it go",
                     dev.model.dongle, failure_reason(exc))
            self.teardown()
            return
        heard = False
        while (message := dev.next_event()) is not None:
            if isinstance(message, Link):
                if not self._linked(dev, message):
                    return
                continue
            heard = True
            try:
                said = dev.event(message)
            except UnreadableAnswer as exc:
                log.info("a50x: an event could not be read: %s", exc)
                # It says the feature changed: its read at the next pass tells to what. Raised
                # only for an index the lookups gave (A50X.event).
                self._owed.add(cast("int", dev.feature_at(message.index)))
                continue
            if said is not None:
                self._take(*said)
        # A headset that sends events is on: what it left unknown is read at the next pass.
        # A late reply alone is no event, or a read that answers late would be sent again.
        if heard:
            self._owed |= self._unknown()

    def _linked(self, dev: A50X, link: Link) -> bool:
        """The bridge's connection event: a headset back is read again at the next pass, and
        one gone lets the receiver go, to be looked for again; False for that."""
        if link.connected:
            self._owed |= set(dev.features)
            return True
        model, path, kept = dev.model, cast("Path", self.path), self.reply()
        self.teardown()
        self._kept = kept
        self._headset_away(path, model, "receiver said its headset went away")
        return False

    def _take(self, feature_id: int, value: int | str | dict | Mix | bool | None) -> None:
        """What an event said of a feature: its value, or None for a read owed."""
        if value is None:
            self._owed.add(feature_id)
        elif feature_id == battery.ID:
            self._take_battery(cast("dict", value))
        elif isinstance(value, Mix):
            self.mix = value
        elif isinstance(value, bool):
            self.mic_live = value
            log.debug("a50x: the microphone is %s", "live" if value else "muted")
        else:
            # A feature with no setting has an event value of None, owed above.
            setting = cast("Setting", cast("A50X", self.dev).model.setting_of(feature_id))
            self.values[setting.key] = cast("int | str", value)
            log.debug("a50x: the %s is %s", setting.words, value)

    def _unknown(self) -> set[int]:
        """The features whose value is not known."""
        dev = cast("A50X", self.dev)
        unknown = {battery.ID} if self.battery is None else set()
        unknown |= {s.feature for s in dev.model.settings if s.key not in self.values}
        if not set(dev.model.instances) <= set(self.equalizers):
            unknown.add(equalizer.ID)
        if dev.model.onboard_eq and ONBOARD not in self.equalizers:
            unknown.add(onboard_eq.ID)
        if dev.model.mixer and (self.mix is None or self.mic_live is None):
            unknown.add(mixer.ID)
        return unknown

    # -- what problems() and the refusals read -------------------------------

    def problems(self) -> list[tuple[str, str]]:
        """The base station's entries for the daemon's `problems`, as (code, message). Nothing
        is sent to it."""
        found: list[tuple[str, str]] = []
        if (denied := self._notes.denied_problem()) is not None:
            found.append(("a50x_no_permission", denied))
        if self._away is not None:
            found.append(("a50x_headset_away", HEADSET_AWAY.format(self._away.name)))
        if self._notes.not_set_up is not None:
            found.append(("a50x_not_set_up", self._notes.not_set_up))
        return found

    def missing(self) -> str:
        """Why no command can go to a base, with none attached."""
        return next((message for _code, message in self.problems()), NO_A50X)

    # -- reading -------------------------------------------------------------

    def _read(self, features: set[int]) -> None:
        """Read each feature in `features` the base lists, and the model's presets of an
        instance not read yet. An answer it cannot read, or an error reply, is said and its
        value forgotten (_try); a base gone or not answering raises HidppFailure, which stops
        the rest."""
        dev = cast("A50X", self.dev)
        wanted = {feature_id for feature_id in features if dev.listed(feature_id)}
        if battery.ID in wanted:
            self._try(battery.WORDS, lambda: self._take_battery(dev.battery()), self._forget_battery)
        for setting in dev.model.settings:
            if setting.feature in wanted:
                self._try(setting.words, partial(self._read_setting, dev, setting.key),
                          partial(self.values.pop, setting.key, None))
        if equalizer.ID in wanted:
            for instance in dev.model.instances:
                self._try(f"{instance} equalizer", partial(self._read_equalizer, dev, instance),
                          partial(self.equalizers.pop, instance, None))
                if instance in dev.model.presets and instance not in self.presets:
                    self._try(f"presets of the {instance}", partial(self._read_presets, dev, instance))
        if onboard_eq.ID in wanted:
            self._try(onboard_eq.WORDS, partial(self._read_onboard_equalizer, dev),
                      partial(self.equalizers.pop, ONBOARD, None))
        if mixer.ID in wanted:
            self._try(mixer.WORDS, partial(self._read_mix, dev), self._forget_mix)
            self._try("microphone's boom", partial(self._read_boom, dev), self._forget_boom)

    def _try(self, what: str, read: Callable[[], None],
             forget: Callable[[], object] = lambda: None) -> None:
        """One read, a node gone or no answer raised; any other failure said, at info unless
        it is the one `what`'s last read said, since an event has it read again, and the value
        forgotten (`forget`, nothing for a read of what is not held yet): what the headset
        holds now is not known."""
        try:
            read()
        except (DeviceGone, HidppTimeout):
            raise
        except HidppFailure as exc:
            forget()
            said_once(self._read_said, what,
                      f"a50x: its {what} could not be read: {failure_reason(exc)}", logging.INFO)
        else:
            self._read_said.pop(what, None)

    def _read_setting(self, dev: A50X, key: str) -> None:
        """A setting's read; the first since the daemon started, before any write, is kept."""
        value = self.values[key] = dev.read(key)
        first = self.first.setdefault(dev.model.key, {})
        if key not in first and key not in self._written.get(dev.model.key, set()):
            first[key] = value
            setting = dev.model.setting(key)
            log.info("a50x: %s first read as %s", setting.words, setting.level(value))

    def _read_equalizer(self, dev: A50X, instance: str) -> None:
        self.equalizers[instance] = dev.equalizer(instance)

    def _read_presets(self, dev: A50X, instance: str) -> None:
        self.presets[instance] = dev.presets(instance)

    def _read_onboard_equalizer(self, dev: A50X) -> None:
        self.equalizers[ONBOARD] = dev.onboard_equalizer()

    def _read_mix(self, dev: A50X) -> None:
        self.mix = dev.mix()

    def _read_boom(self, dev: A50X) -> None:
        self.mic_live = dev.boom()

    def _take_battery(self, reading: dict) -> None:
        self.battery = reading
        self.battery_wall = round(time.time())
        self.warn_battery(reading)

    def _forget_battery(self) -> None:
        self.battery = self.battery_wall = None

    def _forget_mix(self) -> None:
        self.mix = None

    def _forget_boom(self) -> None:
        self.mic_live = None

    def battery_names(self) -> tuple[str, str]:
        return cast("str", self.name), "the headset"

    def _call(self, work: Callable[[], object]) -> dict | None:
        """Run `work` on the base: None when it went through, else the refusal. A node gone
        tears the base down."""
        name = cast("str", self.name)
        try:
            work()
        except DeviceGone as exc:
            dongle = cast("A50X", self.dev).model.dongle
            log.info("a50x: the %s went away: %s", dongle, failure_reason(exc))
            self.teardown()
            return hidpp_refusal(f"{name}'s {dongle}", exc)
        except HidppFailure as exc:
            if not isinstance(exc, HidppTimeout | UnreadableAnswer):
                log.info("a50x: a request failed: %s", failure_reason(exc))
            return hidpp_refusal(name, exc)
        return None

    # -- commands --------------------------------------------------------------

    def reply(self) -> dict:
        """`a50x`'s answer from what the base last said."""
        dev = cast("A50X", self.dev)
        model = dev.model
        return {
            "ok": True,
            "answering": True,
            "name": model.name,
            "device": str(self.path),
            "product": f"{model.product:04x}",
            # How each frame starts, as "02 0c": the first connect checks it.
            "framing": dev.framing.words(),
            # The receiver's bridge's index, null for a model on its own node.
            "bridge": dev.bridge,
            "listed": [f"0x{feature_id:04x}" for feature_id, found in dev.features.items()
                       if found is not None],
            "battery": self.battery,
            "battery_at": self.battery_wall,
            "settings": {s.key: self.values[s.key] for s in model.settings
                         if s.key in self.values},
            "first": dict(self.first.get(model.key, {})),
            "equalizer": {instance: equalizer_reply(self.equalizers[instance],
                                                    self.presets.get(instance, {}))
                          for instance in model.equalizers if instance in self.equalizers},
            # The presets of each instance that `a50x_eq` can copy, by name.
            "presets": {instance: list(self.presets[instance]) for instance in model.instances
                        if instance in self.presets},
            "mix": None if self.mix is None else mixer.reply(self.mix),
            "mic_live": self.mic_live,
            # Why every write is refused, for the front ends to say before a click; None where
            # none is.
            "writes_refused": writes_refused(model),
            # Not yet run on this hardware: the front ends ask for a device report.
            "untested": not model.tested,
        }

    def _kept_of(self, model: Model) -> dict:
        """The kept reply of `model`'s headset, or none for another model's."""
        product = f"{model.product:04x}"
        return self._kept if self._kept is not None and self._kept["product"] == product else {}

    def _away_reply(self, model: Model) -> dict:
        """`a50x`'s answer while `model`'s headset does not answer its receiver, with answering
        false, as a mouse's reply keeps a silent mouse's: the reply kept when its receiver was
        let go for the headset's leaving or for its cable (_kept), until a base attaches, or
        nothing, as for a receiver unplugged while its headset answered. Its writes are
        refused (missing)."""
        return {**NOTHING_KEPT, **self._kept_of(model), "ok": True, "answering": False,
                "name": model.name, "product": f"{model.product:04x}",
                "first": dict(self.first.get(model.key, {})),
                "writes_refused": writes_refused(model), "untested": not model.tested}

    def _cmd_a50x(self, request: dict) -> dict:
        """The base's state; with `read`, every feature it lists read again first; while a
        bridged model's headset is away, its last readings (_away_reply)."""
        if self.dev is None:
            if self._away is not None:
                return self._away_reply(self._away)
            return {"ok": False, "error": self.missing()}
        if request.get("read") is True:
            self._owed.clear()
            if (refused := self._call(partial(self._read, set(self.dev.features)))) is not None:
                return refused
        return self.reply()

    def _cmd_a50x_set(self, request: dict) -> dict:
        """Write a setting's `value`, or with `reset` the value each setting's first read
        found, then read it back. Refused, with nothing sent, where A50X.write_refused refuses
        the write."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev, model = self.dev, self.dev.model
        if request.get("reset") is True:
            return self._reset(dev, model)
        key = name_text(request.get("setting"), "a50x_set.setting").strip().lower()
        setting = next((s for s in model.settings if s.key == key.replace("-", "_")), None)
        if setting is None:
            return {"ok": False, "error": (
                f"there is no {model.name} setting {written(key)}; the settings are "
                f"{word_list([s.key for s in model.settings], 'and')}")}
        try:
            setting.checked(request.get("value"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if (refused := dev.write_refused(setting.feature, setting.key,
                                         setting.words)) is not None:
            return {"ok": False, "error": refused}
        return self._write(dev, {setting.key: request["value"]})

    def _reset(self, dev: A50X, model: Model) -> dict:
        """Each setting's first-read value written back; refused, with nothing sent, unless
        every one of their writes is tested and may go (A50X.write_refused)."""
        first = self.first.get(model.key, {})
        if not first:
            return {"ok": False, "error": NOTHING_TO_RESET.format(model.name)}
        if not set(first) <= model.writes_tested:
            return {"ok": False, "error": not_tested(model.name, "settings")}
        # Kept across a replug, whose lookup can miss a feature the first read's found.
        for setting in map(model.setting, first):
            if (refused := dev.write_refused(setting.feature, setting.key,
                                             setting.words)) is not None:
                return {"ok": False, "error": refused}
        return self._write(dev, first)

    def _write(self, dev: A50X, values: Mapping[str, int | str]) -> dict:
        """Write each setting's value, then read it back; one whose write or read back failed
        is not known until it is read again (_unknown)."""
        written_now = self._written.setdefault(dev.model.key, set())

        def write() -> None:
            for key, value in values.items():
                # Before the write: a write that fails part way may still have reached it.
                written_now.add(key)
                self.values.pop(key, None)
                dev.write(key, value)
                setting = dev.model.setting(key)
                log.info("a50x: %s set to %s", setting.words, setting.level(value))
                self._read_setting(dev, key)

        return self._call(write) or self.reply()

    def _cmd_a50x_mix(self, request: dict) -> dict:
        """One source's level or mute, or the stream port's level, written in the whole stream
        mix as it was last read, then read back. Refused, with nothing sent, where
        A50X.write_refused refuses the write and for a source the mix as read does not list."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev, model = self.dev, self.dev.model
        if not model.mixer:
            return {"ok": False, "error": NO_MIX.format(model.name)}
        try:
            change = mix_change(request)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if (refused := dev.write_refused(mixer.ID, MIXER_WRITE, mixer.WORDS)) is not None:
            return {"ok": False, "error": refused}
        if self.mix is None:
            return {"ok": False, "error": MIX_NOT_READ.format(model.name)}
        if not self.mix.lists(request["source"]):
            return {"ok": False, "error": MIX_UNLISTED.format(
                model.name, mixer.SHOWN.get(request["source"], request["source"]))}
        mix = change(self.mix)

        def write() -> None:
            dev.write_mix(mix)
            log.info("a50x: the stream mix set to %s", mixer.words(mixer.reply(mix)))
            self._read_mix(dev)

        return self._call(write) or self.reply()

    def _cmd_a50x_eq(self, request: dict) -> dict:
        """An instance's ten gains in dB, or with `preset` one of the model's presets of it,
        or with `bands` ten bands, a saved preset's (a50x_library), written into the slot the
        instance plays, then read back: the gains with each band's frequency and Q as read, a
        preset with its frequencies and gains and G HUB's Q for a preset (PRESET_Q), and the
        bands as given. Refused, with nothing sent, for more than one of the three and where
        A50X.write_refused refuses the write."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev, model = self.dev, self.dev.model
        if not model.instances:
            return {"ok": False, "error": EQUALIZER_READ_ONLY.format(model.name)}
        named = request.get("instance")
        if (refused := equalizer_refused(model, named, model.instances)) is not None:
            return {"ok": False, "error": refused}
        # equalizer_refused passes only a name of model.instances.
        instance = cast("str", named)
        try:
            asked = equalizer_change(model, instance, request)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if (refused := dev.write_refused(equalizer.ID, EQUALIZER_WRITE,
                                         f"{instance} equalizer")) is not None:
            return {"ok": False, "error": refused}
        if isinstance(asked, tuple):
            bands = asked
            said = ", ".join(f"{band.freq} Hz {band.gain:+g}" for band in bands)
        elif isinstance(asked, list):
            read = self.equalizers.get(instance)
            if read is None:
                return {"ok": False, "error": NOT_READ.format(model.name, instance)}
            bands = tuple(replace(band, gain=gain) for band, gain in zip(read, asked,
                                                                         strict=True))
            said = " ".join(f"{gain:+g}" for gain in asked)
        else:
            held = self.presets.get(instance)
            if held is None:
                return {"ok": False, "error": NO_PRESETS.format(model.name, instance)}
            bands = tuple(replace(band, q=PRESET_Q) for band in held[asked])
            said = f"its {asked} preset"

        def write() -> None:
            dev.write_equalizer(instance, bands)
            log.info("a50x: the %s equalizer set to %s", instance, said)
            self._read_equalizer(dev, instance)

        return self._call(write) or self.reply()


def equalizer_reply(bands: tuple[Band, ...], presets: Mapping[str, tuple[Band, ...]]) -> dict:
    """The bands of the slot an instance plays as the `a50x` reply gives them: the preset
    whose bands they are, None for none, and a list of them."""
    return {"preset": preset_of(bands, presets), "bands": [asdict(band) for band in bands]}


def mix_change(request: dict) -> Callable[[Mix], Mix]:
    """The change `request` asks of the stream mix: a `source` of MIX_SOURCES and its
    `level`, 0 to mixer.LEVELS, or a source's `muted`, true or false; ValueError with the
    sentence for any other."""
    source, level, muted = request.get("source"), request.get("level"), request.get("muted")
    if source not in MIX_SOURCES:
        raise ValueError(f"the stream mix's sources are {word_list(MIX_SOURCES)}, "
                         f"not {written(source)}")
    if (level is None) == (muted is None):
        raise ValueError("give the stream mix a source's level or whether it is muted, "
                         "one of the two")
    if muted is not None:
        read_switch(muted, "muted")
        if source == mixer.STREAM:
            raise ValueError("the stream port has a level and no mute")
        return lambda mix: mix.with_muted(source, muted)
    if not whole(level) or not 0 <= level <= mixer.LEVELS:
        raise ValueError(f"a level of the stream mix is a whole number from 0 to "
                         f"{mixer.LEVELS}, not {written(level)}")
    return lambda mix: mix.with_level(source, level)


def equalizer_change(model: Model, instance: str, request: dict
                     ) -> list[float] | str | tuple[Band, ...]:
    """What an `a50x_eq` request asks of `instance`'s equalizer: ten gains, checked_gains'
    list; the name of one of the model's presets of it; or ten bands (bands_from), a tuple.
    ValueError with the sentence for a value it does not take, or more than one of the
    three (ONE_OF)."""
    gains, preset, bands = request.get("gains"), request.get("preset"), request.get("bands")
    if sum(value is not None for value in (gains, preset, bands)) > 1:
        raise ValueError(ONE_OF)
    if bands is not None:
        return bands_from(bands)
    if preset is None:
        return checked_gains(model, gains)
    if (refused := preset_refused(model, instance, preset)) is not None:
        raise ValueError(refused)
    return cast("str", preset)


def checked_gains(model: Model, gains: object) -> list[float]:
    """Ten gains in dB, each in equalizer.GAIN_RANGE, on the wire's 0.05 dB steps, as the
    headset will read them back and the journal says them; ValueError with the sentence for
    any other."""
    if not isinstance(gains, list) or len(gains) != BAND_COUNT:
        raise ValueError(f"the {model.name}'s equalizer takes {counted(BAND_COUNT, 'gain')}, "
                         f"not {given_count(gains, 'gain')}")
    return [gain_of(raw_gain(gain)) for gain in gains]
