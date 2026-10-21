"""The lighting and the brightness in force, by keyboard model: which entry of the layer in force
shows on the keyboard model in force, the entry a lighting change edits, and that model's
brightness. config.json and each game profile keep both by model (keyboard.model_key).

The model in force is the keyboard attached last since the daemon started, attached now or not:
a change made while it is away is saved for it. Before any keyboard has been attached, a
lighting or brightness change is refused (NO_KEYBOARD_YET). A model with no lighting or
brightness of its own starts from the leader's (`leader`, chosen or attached first) or, where
the leader has none, another model's (keyboard.source_entry), cut to what it can show
(keyboard.shown_lighting, shown_brightness), and attaching it saves no copy: its first change
saves that copy, with the change, for it alone. The daemon drives one keyboard, the leader's
where two are attached as it connects (`find_keyboard`), as a new leader is chosen
(`switch_to_leader`), or as the one chosen is plugged in or switched on beside another
(`tend`), each once its keyboard answers and is not left on its own profile. In place of one
that does not answer, at a connect (`stand_in`) or while it is driven (`hand_over`), or one left
on its own profile at a connect (`stand_in`), it drives another plugged in that answers. The
mice follow the one it drives or, with none driven, the leader (`followed`, through
`Keyboard.shown_color` and `Keyboard.at_level`). The
keyboards plugged in and not driven are asked whether they answer for the window, which shows
one that does not as switched off (`keyboards`)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from ..config import ConfigError, Lighting, layer_color
from ..keyboard import (
    MODEL_KEYS,
    attached_keyboards,
    find_attached_keyboard,
    keep_entry,
    key_name,
    model_ids,
    model_key,
    own_entry,
    shown_brightness,
    shown_lighting,
)
from ..lighting import ANIMATIONS, offered_effects
from ..profiles import PER_KEY, layer_in_force
from ..wording import word_list
from .shared import RECONNECT_DELAY, Answered, name_text, open_keyboard, scaled, with_note

if TYPE_CHECKING:
    from pathlib import Path

    from ..hidpp import Hidpp
    from . import Daemon

log = logging.getLogger("gearbench")

#: The refusal of a lighting or brightness change made before any keyboard was attached.
NO_KEYBOARD_YET = ("No keyboard has been connected since the Gearbench daemon started, and each "
                   "keyboard model keeps its own lighting and brightness: connect a keyboard "
                   "first.")

#: What a pick of a model plugged in that does not answer says, as one switched off behind its
#: receiver, with its name: the owner's decision of 2026-10-20, "Add a note": "Otherwise the
#: pick looks like it did nothing." A pick of the keyboard attached first says nothing, since
#: nothing switches to it while the one driven answers (`tend`): the owner's decision of
#: 2026-10-21.
LEADER_OFF = "The {} is driven once it is switched on."


class PerKeyboard:
    """The lighting and brightness of the keyboard model in force, and the leader."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: The models of the keyboards attached since the start, in the order Keyboard.connect
        #: found them (`found`): the first leads until a model is chosen (config.sync_keyboard).
        self.attached: dict[str, None] = {}
        #: When `tend` next looks (`looking`): for the keyboard chosen to lead, or for another
        #: to drive in place of the one driven, marked not answering.
        self._look_at = 0.0
        #: Each keyboard node's answer when last asked (`ask`), by the node as
        #: keyboard.attached_keyboards gives it: when, and whether a keyboard answered. One
        #: older than RECONNECT_DELAY says nothing, and `keyboards` asks that node again. The
        #: kernel gives a new node the lowest free hidraw number (hidraw.c), so they stay few.
        self._asked: dict[tuple[Path, int, int], tuple[float, bool]] = {}
        #: The keyboard node `find_stand_in` last said another keyboard was connected in place
        #: of, so that is said once until the node answers an ask; None once it does (`ask`).
        self._passed_over: Path | None = None

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"sync_keyboard": self._cmd_sync_keyboard}

    def found(self, product: int) -> int:
        """Keep the model of keyboard `product`, which Keyboard.connect found, among those
        attached since the start; answers `product`."""
        self.attached.setdefault(model_key(product))
        return product

    def model(self) -> str | None:
        """The model of the keyboard attached last since the start, or None before any."""
        product = self.d.keyboard.keyboard_product
        return None if product is None else model_key(product)

    def chosen(self) -> str | None:
        """The keyboard model chosen to lead (config.sync_keyboard), by its key (model_ids): one
        written by hand under another of the model's ids, the G915 TKL's c545, is the model's,
        as a lighting entry is. None for none chosen."""
        chosen = self.d.config.sync_keyboard
        return None if chosen is None else model_ids(chosen)[0]

    def leader(self) -> str | None:
        """The keyboard model chosen to lead (`chosen`), else the first attached since the start.
        Keyboard.connect looks for it first (find_keyboard), the mice follow it while no other
        model is driven (followed), and a model with no lighting or brightness of its own
        starts from it."""
        return self.chosen() or next(iter(self.attached), None)

    def followed(self) -> str | None:
        """The keyboard model the mice follow: the model in force while the daemon drives a
        keyboard, else the leader."""
        return self.model() if self.d.keyboard.dev is not None else self.leader()

    def moved(self) -> bool:
        """Whether the mice follow another model than the one in force (followed): with no
        keyboard driven, a leader other than the keyboard attached last."""
        return self.followed() != self.model()

    def find_keyboard(self) -> tuple[Path, int, int] | None:
        """The node of the keyboard Keyboard.connect opens (keyboard.find_attached_keyboard),
        the leader's model first: with two attached the daemon drives the one the mice follow.
        From sysfs; nothing is opened."""
        return find_attached_keyboard(leader=self.leader())

    def drives_another(self) -> bool:
        """Whether the daemon drives a keyboard of another model than the leader's."""
        return self.d.keyboard.dev is not None and self.model() != self.leader()

    def switch_to_leader(self, why: str = "chosen to lead") -> bool | None:
        """Hand the keyboard driven back, as a disconnect and the stop do
        (Keyboard.teardown_device), and connect the leader's, for a new leader chosen while
        both are plugged in: the owner's decision of 2026-10-20, "Switch at once". Nothing
        while no keyboard is driven, which the loop's next connect sees to, while the one
        driven is of the leader's model, while the leader's is not plugged in (find_keyboard,
        from sysfs) or is held on its own profile or would be at its connect
        (OnboardHold.untyped_for: the owner's decision of 2026-10-20, "Keep the change"), or
        while nothing answers on its node, as on a receiver's with its keyboard switched off:
        it is asked first, as connect asks it (ask), and the one driven stays until it answers,
        the owner's decision of 2026-10-20, "Count it as unplugged". A node this user may not
        open is handed to connect as before, which says why. `why` is the log line's. Whether
        it handed one back, or None where nothing answered the leader's, which its caller says
        for a model chosen (LEADER_OFF)."""
        if not self.drives_another():
            return False
        found = self.find_keyboard()
        keyboard = self.d.keyboard
        if (found is None or model_key(found[1]) != self.leader()
                or keyboard.hold.untyped_for(found[1]) is not None):
            return False
        try:
            opened = self.ask(found)
        except PermissionError:
            # connect's own open is refused the same way, and says so.
            opened = None
        else:
            if opened is None:
                return None
        said = keyboard.teardown_device()
        log.info("%s %s: %s", key_name(model_key(found[1])), why, said)
        keyboard.connect(None if opened is None else Answered(found, opened))
        return True

    def switch_at_reload(self, was: str | None) -> bool:
        """The switch to the leader (switch_to_leader) for a reload that changed it from `was`
        in config.json: the owner's decision of 2026-10-20, "Yes, same as the window". One
        chosen, plugged in, that does not answer is said once at info, as a pick's note says
        it (LEADER_OFF); the look every RECONNECT_DELAY (tend) says nothing more. Whether it
        handed one back."""
        if self.leader() == was:
            return False
        switched = self.switch_to_leader("chosen to lead in config.json")
        if switched is None and self.chosen() is not None:
            # The leader's keyboard, a model's, was asked. With none chosen, `tend` does not
            # look for it again, so nothing is said of it.
            log.info("%s chosen to lead in config.json does not answer; it is driven once it "
                     "is switched on", key_name(cast("str", self.leader())))
        return switched is True

    def ask(self, found: tuple[Path, int, int]) -> tuple[Hidpp, int] | None:
        """The keyboard at `found` open, asked as Keyboard.connect asks it (open_keyboard), or
        None where nothing answers, as on a receiver's node with its keyboard switched off; the
        answer is kept for `keyboards` (_asked). Raises PermissionError for a node the udev
        rule did not grant."""
        opened = open_keyboard(found)
        self._asked[found] = (time.monotonic(), opened is not None)
        if opened is not None and found[0] == self._passed_over:
            self._passed_over = None
        return opened

    def find_stand_in(self, model: str, path: Path, held: bool = False) -> Answered | None:
        """Another keyboard plugged in that answers in place of the keyboard of `model` on node
        `path`, which does not, as on a receiver's node with its keyboard switched off, or, with
        `held`, which Keyboard.connect leaves on its own profile (OnboardHold.keep_onboard,
        held_onboard): the first of another model, in MODEL_KEYS' order, that answers its ask
        (`ask`), picked to lead or not. One held on its own profile is not asked, and one this
        user may not open is passed by. Said at info once until `path` answers an ask
        (_passed_over)."""
        for found in attached_keyboards():
            if (model_key(found[1]) == model
                    or self.d.keyboard.hold.held_onboard(found) is not None):
                continue
            try:
                opened = self.ask(found)
            except PermissionError:
                # Nothing is learned of it: passed by, as one that does not answer is.
                continue
            if opened is not None:
                log.log(logging.DEBUG if path == self._passed_over else logging.INFO,
                        "%s %s on %s: connecting the %s on %s in its place", key_name(model),
                        "held on its own profile" if held else "not answering", path,
                        key_name(model_key(found[1])), found[0])
                self._passed_over = path
                return Answered(found, opened)
        return None

    def stand_in(self, passed: tuple[Path, int, int], held: bool = False) -> bool:
        """Connect another keyboard plugged in in place of `passed`, the one Keyboard.connect
        found first, which nothing answered on or, with `held`, which it leaves on its own
        profile (find_stand_in). The owner's decisions of 2026-10-20, "Drive the other": "An
        off keyboard counts as unplugged everywhere. The G515 is driven until the G915
        answers.", and of 2026-10-21 for one held, "Drive the other". A picked one that answers
        later, or can be set up, takes over (`tend`); with none picked the one connected stays.
        Whether one was connected."""
        answered = self.find_stand_in(model_key(passed[1]), passed[0], held)
        return answered is not None and self.d.keyboard.connect(answered)

    def marked(self) -> bool:
        """Whether the keyboard driven is marked not answering (Keyboard.keyboard_answering),
        as one switched off behind its receiver is, and one asleep can be."""
        keyboard = self.d.keyboard
        return keyboard.dev is not None and not keyboard.keyboard_answering()

    def hand_over(self) -> bool:
        """Hand back the keyboard driven, marked not answering (`marked`), and connect another
        plugged in that answers in its place (find_stand_in): the owner's decision of
        2026-10-21, a keyboard switched off while it is driven counted as unplugged too. Its
        teardown writes nothing to it while it is marked (Keyboard.teardown_device). Whether it
        handed it back; the connect is the loop's try."""
        keyboard = self.d.keyboard
        answered = self.find_stand_in(cast("str", self.model()), cast("Hidpp", keyboard.dev).path)
        if answered is None:
            return False
        keyboard.teardown_device()
        keyboard.connect(answered)
        return True

    def switched_off(self, found: tuple[Path, int, int], asking: bool) -> bool:
        """Whether nothing answered on the node of keyboard `found`, one not driven, when it was
        asked within RECONNECT_DELAY (_asked), and with `asking` asked now where it was not
        (ask), its device closed after: Claude Code's call of 2026-10-20, pending the owner,
        asked only while a window asks (`keyboards`) and at most once every RECONNECT_DELAY.
        Not asked: one held on its own profile (OnboardHold.held_onboard), whose node is not
        opened, and one this user may not open, of which nothing is learned."""
        asked = self._asked.get(found)
        if asked is not None and asked[0] > time.monotonic() - RECONNECT_DELAY:
            return not asked[1]
        if not asking or self.d.keyboard.hold.held_onboard(found) is not None:
            return False
        # Not checked on the hardware: whether this ask keeps an idle G915 from going to sleep.
        try:
            opened = self.ask(found)
        except PermissionError:
            # Its pick says why: connect's own open is refused (switch_to_leader).
            return False
        if opened is not None:
            opened[0].close()
        return opened is None

    def keyboards(self, asking: bool) -> list[dict]:
        """status's `keyboards_attached`: each keyboard model plugged in now
        (keyboard.attached_keyboards), by key, and whether it is switched off (switched_off),
        which the Keyboard tab's switcher shows and offers no pick of: the owner's decision of
        2026-10-20, "If it is switched off, we should be able to identity it being switched
        off and just unselectable." `asking` is the window's status read's ask_keyboards, with
        which switched_off asks. On a receiver's node the receiver answers for a keyboard
        switched off, so that keyboard is sent nothing. The keyboard driven is never asked
        here: its state is status's `answering`."""
        driven = self.model() if self.d.keyboard.dev is not None else None
        return [{"model": model_key(found[1]),
                 "switched_off": (model_key(found[1]) != driven
                                  and self.switched_off(found, asking))}
                for found in attached_keyboards()]

    def looking(self) -> bool:
        """Whether `tend` looks: while the keyboard driven is marked not answering (`marked`),
        or a keyboard is chosen and another one driven."""
        return self.marked() or (self.chosen() is not None and self.drives_another())

    def tend(self) -> bool:
        """Drive the keyboard chosen to lead (`chosen`) once it is plugged in beside another one
        driven and answers, looked for and asked every RECONNECT_DELAY (switch_to_leader): the
        owner's decision of 2026-10-20, "Only attached ones": "The last pick is remembered and
        used when both are plugged in again." With none chosen, the one driven stays while it
        answers, the owner's decision of 2026-10-20, "Yes, keep the driven one": "Until you
        pick one, plugging in a second keyboard changes nothing." Else, while the one driven is
        marked not answering, another that answers takes its place (hand_over), looked for
        every RECONNECT_DELAY too. Whether it handed one back; its connect is the loop's try."""
        now = time.monotonic()
        if not self.looking() or now < self._look_at:
            return False
        self._look_at = now + RECONNECT_DELAY
        # It says nothing of a leader that does not answer: it asks again at the next look.
        if (self.chosen() is not None
                and self.switch_to_leader("plugged in, chosen to lead") is True):
            return True
        return self.marked() and self.hand_over()

    def deadlines(self) -> list[float]:
        """When `tend` next looks, while it looks (`looking`)."""
        return [self._look_at] if self.looking() else []

    def check_model(self) -> str:
        """The model in force, or the refusal of a change made before any keyboard was
        attached."""
        model = self.model()
        if model is None:
            raise ConfigError(NO_KEYBOARD_YET)
        return model

    def entries(self) -> dict[str, Lighting]:
        """The lighting of the layer in force, by keyboard model: the loaded game profile's,
        else the active M profile's."""
        return layer_in_force(self.d.games.overlay, self.d.config.profile).lighting

    def shown(self, entries: dict[str, Lighting] | None = None) -> Lighting:
        """What `entries`, the layer in force's by default, show on the keyboard model in force
        (shown_on)."""
        return self.shown_on(self.model(), entries)

    def shown_on(self, model: str | None, entries: dict[str, Lighting] | None = None) -> Lighting:
        """What `entries`, the layer in force's by default, show on keyboard model `model`: its
        own, or copy first's, cut to the effects and per-key lighting the attached keyboard
        reports, and to its board. Not for a change: a copy is in no profile."""
        lighting = self.d.keyboard.lighting
        effects = None if lighting is None else offered_effects(lighting.clusters)
        per_key = None if lighting is None else lighting.per_key
        return shown_lighting(self.entries() if entries is None else entries, model,
                              self.leader(), effects, per_key)[0]

    def edited(self) -> tuple[Lighting, Callable[[], None]]:
        """The lighting a lighting change edits: the layer in force's own for the keyboard model
        in force, made from what it shows (shown) where it has none; and what drops one made
        here, for a change whose save is refused, so that no later save writes it. Refused
        before any keyboard was attached (check_model)."""
        entries, model = self.entries(), self.check_model()
        own = own_entry(entries, model)
        if own is not None:
            # Under the model's key: one under its other id, written by hand, moves there.
            keep_entry(entries, model, own)
            return own, lambda: None
        # check_model gives a model key, so shown cuts a copy (keyboard.cut_lighting) that
        # shares nothing with the source's entry.
        made = self.shown(entries)
        keep_entry(entries, model, made)

        def drop() -> None:
            entries.pop(model, None)

        return made, drop

    def active_lighting(self) -> tuple[str, int, dict[str, int], dict[str, int]]:
        """Effect, color, per-key colors and effect params of the layer in force, the overlay
        first, as the keyboard model the mice follow shows it (followed, shown_on): with a
        keyboard attached, the model in force. The color is the background in
        per-key mode without an animation, the Static color otherwise. An animation the
        attached keyboard does not report shows static (Paint.shows_static), as copy first's
        cut makes one (keyboard.cut_lighting)."""
        layer = self.shown_on(self.followed())
        effect = "static" if self.d.keyboard.paint.shows_static(layer.effect) else layer.effect
        shows_keys = layer.mode == PER_KEY and effect not in ANIMATIONS
        per_key = dict(layer.keys) if shows_keys else {}
        return (layer.effect, layer_color(effect, layer.mode, layer.color, layer.background),
                per_key, layer.settings())

    def brightness_in_force(self, followed: bool = False) -> tuple[int, bool]:
        """The brightness of the keyboard model in force, or with `followed` of the one the mice
        follow (followed), and whether it is its own: else the one copy first gives it
        (keyboard.shown_brightness)."""
        model = self.followed() if followed else self.model()
        return shown_brightness(self.d.config.brightness, model, self.leader())

    def brightness(self, followed: bool = False) -> int:
        """The brightness of the keyboard model in force, or with `followed` of the one the mice
        follow (brightness_in_force)."""
        return self.brightness_in_force(followed)[0]

    def followed_view(self) -> tuple[Lighting, int]:
        """The lighting and the brightness the keyboard model the mice follow shows (followed):
        with a keyboard attached, those of the model in force."""
        return self.shown_on(self.followed()), self.brightness(followed=True)

    def at_brightness(self, color: int) -> int:
        """`color` at the brightness of the keyboard model the mice follow (shared.scaled): the
        keyboard's color as a mouse takes it, which has no brightness of its own."""
        return scaled(color, self.brightness(followed=True))

    def with_brightness(self, level: int) -> dict[str, int]:
        """config.json's brightness with `level` for the keyboard model in force; refused before
        any keyboard was attached (check_model)."""
        levels = dict(self.d.config.brightness)
        keep_entry(levels, self.check_model(), level)
        return levels

    def keep_brightness(self, level: int) -> None:
        """Keep `level`, which the keyboard of the model in force shows already, as that
        model's in config.json (with_brightness), saved through persist: a file that cannot
        be saved keeps it live with a warning."""
        self.d.config.brightness = self.with_brightness(level)
        self.d.persist()

    def known(self) -> list[str]:
        """The keyboard models Gearbench knows of, in MODEL_OF's order: those config.json keeps
        lighting or brightness for, those the loaded game profile keeps lighting for, those
        attached since the start, and the one chosen to lead."""
        overlay = self.d.games.overlay
        kept = {*self.d.config.brightness, *(model for profile in self.d.config.profiles.values()
                                             for model in profile.lighting),
                *(overlay.lighting if overlay is not None else ()), *self.attached, self.chosen()}
        return [key for key in MODEL_KEYS if kept & set(model_ids(key))]

    def status(self, asking: bool) -> dict:
        """What `status` gives of the models, by key: the one in force, the one chosen to lead
        (`chosen`) or None, the leader in force, those known (`known`), and those plugged in
        now, each said switched off or not (`keyboards`, which asks them with `asking`), which
        the Keyboard tab's switcher offers."""
        return {"lighting_model": self.model(), "sync_keyboard": self.chosen(),
                "sync_leader": self.leader(), "keyboard_models": self.known(),
                "keyboards_attached": self.keyboards(asking)}

    def _cmd_sync_keyboard(self, request: dict) -> dict:
        """Choose the keyboard model the daemon drives with two plugged in, which the mice follow
        and a keyboard with no lighting or brightness of its own starts from, by any of its ids
        or its name, or with null the keyboard attached first since the start; saved in
        config.json under the model's key. With the new leader's keyboard plugged in beside
        the one driven and answering, that one is handed back and the leader's connected
        (switch_to_leader), with the reply's note saying a leader not set up; where nothing
        answers on the node of a model chosen, the note is LEADER_OFF. A leader its connect
        would leave on its own profile (OnboardHold.untyped_for) is not asked and the one
        driven stays. Otherwise, where the choice changes the lighting or the brightness of the
        model the mice follow (followed_view), the keyboard is woken and the profile applied, as
        at a profile switch, with the apply's note in the reply, in place of LEADER_OFF where it
        has one."""
        value = request["value"]
        chosen = None if value is None else model_named(name_text(value, "sync_keyboard"))
        was = self.followed_view()
        self.d.save_setting(sync_keyboard=chosen)
        reply = {"ok": True, "sync_keyboard": chosen, "sync_leader": self.leader()}
        keyboard = self.d.keyboard
        switched = self.switch_to_leader()
        if switched:
            # The connect applied the profile, whose failure is the note, or left none driven.
            return with_note(reply, keyboard.change_unreached() or keyboard.last_apply_error,
                             keyboard.last_apply_detail)
        # None: the leader's keyboard, a model's, was asked and does not answer. With none
        # chosen nothing switches to it while the one driven answers (tend), so nothing is
        # promised.
        off = (None if switched is False or chosen is None
               else LEADER_OFF.format(key_name(cast("str", self.leader()))))
        if self.followed_view() == was:
            return with_note(reply, off)
        keyboard.wake_for_change()
        return with_note(reply, keyboard.apply_profile() or off, keyboard.last_apply_detail)


def model_named(text: str) -> str:
    """The key of the keyboard model `text` names, by any of its ids (model_ids) or its name in
    any case; for anything else a ConfigError that names the models there are."""
    wanted = text.strip().lower()
    for key in MODEL_KEYS:
        if wanted in (*model_ids(key), key_name(key).lower()):
            return key
    known = word_list([f"{key_name(key)} ({key})" for key in MODEL_KEYS], "and")
    raise ConfigError(f"sync_keyboard: {text!r} names no keyboard model Gearbench drives; "
                      f"they are {known}")
