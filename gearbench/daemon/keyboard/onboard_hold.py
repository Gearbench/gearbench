"""The keyboard left on its own profile while Gearbench cannot type its own keys (untyped): a
model whose G keys type keys of its own while unbound (KeyboardModel.g_key_defaults), found so
at its connect (keep_onboard) or set up when the session goes inactive (hold_for_session), and
the node `connect` opens no more meanwhile (held_onboard), held too where that hand-back fell
short of its own profile (left_in_host)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ...keyboard import (
    MODEL_OF as KEYBOARDS,
    find_attached_keyboard,
    name as keyboard_name,
    typed_words,
)
from ...onboard import F_ONBOARD, MODE_HOST, read_mode

if TYPE_CHECKING:
    from pathlib import Path

    from ...hidpp import Hidpp
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: The problem of a keyboard whose G keys type its own keys while unbound
#: (KeyboardModel.g_key_defaults) left on its own profile while Gearbench cannot type them
#: (OnboardHold.keep_onboard, hold_for_session), and its journal line: its name, those
#: keys and until when before the colon, where the tray cuts a long line, and why after it
#: (Untyped). It is the Keyboard tab's Status row too, so it is short and leaves unsaid that
#: Gearbench's lighting and key features are off, which its onboard profile implies (the
#: owner's decision of 2026-10-25; "onboard", the profile's word in probe, the man page and
#: the Mouse tab, the user's decision of 2026-10-27).
KEPT_ONBOARD = "the {model} keeps its onboard profile, its {keys} typing, until {until}: {why}"
#: KEPT_ONBOARD's place for a keyboard held while the session is not active whose hand-back
#: did not reach its own profile (OnboardHold.left_in_host), as short: in host mode its keys
#: type only through the virtual keyboard, and it powers up on its own profile.
LEFT_IN_HOST = ("the {model} is left in host mode, its {keys} dead until it is switched off "
                "and on: its hand-back did not finish")


@dataclass(frozen=True)
class Untyped:
    """Why Gearbench cannot type the own keys of a keyboard whose G keys type them while
    unbound (OnboardHold.untyped), in KEPT_ONBOARD's words: what it waits for and why, or
    where to read why, and the level the journal says so at."""

    until: str
    why: str
    level: int


#: The virtual keyboard cannot open: a fault, warned of. Its cause is said beside it, by the
#: no_virtual_keyboard problem, the window's banner and the warning before it
#: (Daemon.make_virtual_devices), so this points to the man page alone (the owner's decision
#: of 2026-10-25).
NO_VIRTUAL_KEYBOARD = Untyped("the virtual keyboard opens", "see gearbench-ctl(1), FILES",
                              logging.WARNING)
#: The session is not active (Session.why_inactive): whoever has the seat types on the
#: keyboard's own profile, as it would with no Gearbench (the owner's decision of 2026-10-19).
SESSION_INACTIVE = Untyped("the session is active again",
                           "another account or a text console has the seat", logging.INFO)


def kept_onboard_words(product: int, untyped: Untyped) -> str:
    """KEPT_ONBOARD for the keyboard of id `product`, held for `untyped`."""
    return KEPT_ONBOARD.format(model=keyboard_name(product), keys=typed_words(product),
                               until=untyped.until, why=untyped.why)


class OnboardHold:
    """Whether the keyboard is left on its own profile, or in HOST mode by a hand-back cut
    short, and the node held there."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: The node (keyboard.find_attached_keyboard) of a keyboard held while Gearbench cannot
        #: type its own keys (keep_onboard, hold_for_session), on its own profile or in HOST
        #: mode (left_in_host), or None. It stays once Gearbench can type again, until the
        #: keyboard's next set_up clears it (keep_onboard): the node counts as held only while
        #: Gearbench still cannot (held_onboard).
        self.kept_onboard: tuple[Path, int, int] | None = None
        #: Whether the keyboard held (kept_onboard) was left in host mode by a hand-back cut
        #: short (hold_for_session), so its own keys type nothing (LEFT_IN_HOST).
        self.left_in_host = False

    def held_words(self, product: int, untyped: Untyped) -> str:
        """The problem of the keyboard of id `product` held for `untyped`: KEPT_ONBOARD, or
        LEFT_IN_HOST where its hand-back did not reach its own profile (left_in_host)."""
        if self.left_in_host:
            return LEFT_IN_HOST.format(model=keyboard_name(product), keys=typed_words(product))
        return kept_onboard_words(product, untyped)

    def untyped(self) -> Untyped | None:
        """Why Gearbench cannot type a keyboard's own keys now, or None when it can: the
        virtual keyboard missing, which outlasts the session's return, else the session not
        active (Session.why_inactive), where the virtual keyboard would type into the session
        that is."""
        if self.d.kbd is None:
            return NO_VIRTUAL_KEYBOARD
        if self.d.session.why_inactive() is not None:
            return SESSION_INACTIVE
        return None

    def held_onboard(self, found: tuple[Path, int, int]) -> Untyped | None:
        """Why the keyboard node `found` names is held (keep_onboard, hold_for_session), or
        None when it is not: it was left there, and Gearbench still cannot type its keys
        (untyped)."""
        return self.untyped() if found == self.kept_onboard else None

    def held_problem(self) -> str | None:
        """The problem of the keyboard held (held_onboard, held_words) while its node is still
        plugged in, from sysfs, or None."""
        kept = self.kept_onboard
        if kept is None:
            return None
        untyped = self.held_onboard(kept)
        if untyped is None or find_attached_keyboard(same_model_as=kept[1]) != kept:
            return None
        return self.held_words(kept[1], untyped)

    def keep_onboard(self, found: tuple[Path, int, int]) -> bool:
        """At its connect, with the keyboard `found` names open: True when it is left on its
        own profile, not set up, as a model whose G keys type its own keys while unbound
        (KeyboardModel.g_key_defaults) is while Gearbench cannot type them (untyped), so those
        keys work (the owner's decisions of 2026-10-18, "Stay onboard", and of 2026-10-19).
        Found in ONBOARD mode, it is written nothing and held there (held_onboard). Found in
        HOST mode, where a stop that did not run leaves it, it is handed back as a stop hands
        it back, and its next connect reads its mode again, so a hand-back cut short is tried
        again. Its handle is closed either way. A read nothing answers raises into connect's
        guard."""
        product = found[1]
        untyped = self.untyped() if KEYBOARDS[product].g_key_defaults else None
        if untyped is None:
            # Set up, so no longer held: a later connect while Gearbench cannot type reads its
            # mode again rather than take an old hold's node for it.
            self.kept_onboard, self.left_in_host = None, False
            return False
        dev = cast("Hidpp", self.keyboard.dev)
        onboard = dev.feature(F_ONBOARD)
        in_host = onboard is not None and read_mode(dev, onboard) == MODE_HOST
        said = self.keyboard.teardown_device(hand_back=in_host, connected=False)
        if in_host:
            log.info("%s was in host mode, where nothing types its %s until %s: whatever "
                     "drove it last did not hand it back; %s", keyboard_name(product),
                     typed_words(product), untyped.until, said)
        else:
            self.kept_onboard, self.left_in_host = found, False
            log.log(untyped.level, "%s", kept_onboard_words(product, untyped))
        return True

    def hold_for_session(self) -> None:
        """While the session is not active, hand a keyboard whose G keys type its own keys
        (`typed_keys`) its own profile back, as a stop does, and hold it there (held_onboard):
        in HOST mode those keys type only through the virtual keyboard, which types nothing
        then, so whoever has the seat would have none (the owner's decision of 2026-10-19).
        Its node is the seat's meanwhile, as is each keyboard's node the daemon does not hold
        open (the seat's ACL). A hand-back that did not reach ONBOARD mode leaves it in HOST
        mode, held all the same, and says so at warning (left_in_host). Once the session is
        active the loop's next connect sets it up again."""
        if self.keyboard.dev is None or "typed_keys" not in self.keyboard.keyboard_able:
            return
        product = cast("int", self.keyboard.keyboard_product)
        said = self.keyboard.teardown_device()
        # Its own model's node, from sysfs, nothing opened: with two keyboards plugged in, the
        # one Keyboards.connect_due looks for first (PerKeyboard.find_keyboard) can be the
        # other's.
        self.kept_onboard = find_attached_keyboard(same_model_as=product)
        # One without 0x8100 has no HOST mode to be left in.
        self.left_in_host = ("onboard" in self.keyboard.keyboard_able
                             and "onboard mode" not in self.keyboard.handed_back)
        log.log(logging.WARNING if self.left_in_host else logging.INFO, "%s; %s",
                self.held_words(product, SESSION_INACTIVE), said)
