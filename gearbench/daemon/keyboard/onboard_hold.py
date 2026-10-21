"""The keyboard left on its own profile while Gearbench cannot type its own keys (untyped): a
model whose G keys type keys of its own while unbound (KeyboardModel.g_key_defaults), found so
at its connect (keep_onboard) or set up when the session goes inactive (hold_for_session), and
the node `connect` opens no more meanwhile (held_onboard)."""

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
from ..shared import UINPUT_NOT_OPENED

if TYPE_CHECKING:
    from pathlib import Path

    from ...hidpp import Hidpp
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: The problem of a keyboard whose G keys type its own keys while unbound
#: (KeyboardModel.g_key_defaults) left on its own profile while Gearbench cannot type them
#: (OnboardHold.keep_onboard, hold_for_session), and its journal line: its name, those
#: keys, what is off and until when before the colon, where the tray cuts a long line, and
#: why after it (Untyped).
KEPT_ONBOARD = ("the {model} keeps its own profile, so its {keys} work, and Gearbench's "
                "lighting and key features are off until {until}: {why}")


@dataclass(frozen=True)
class Untyped:
    """Why Gearbench cannot type the own keys of a keyboard whose G keys type them while
    unbound (OnboardHold.untyped), in KEPT_ONBOARD's words: what it waits for and why,
    and the level the journal says so at."""

    until: str
    why: str
    level: int


#: The virtual keyboard cannot open: a fault, warned of.
NO_VIRTUAL_KEYBOARD = Untyped("the virtual keyboard opens",
                              f"{UINPUT_NOT_OPENED} (see gearbench-ctl(1), FILES)",
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
    """Whether the keyboard is left on its own profile, and the node held there."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: The node (keyboard.find_attached_keyboard) of a keyboard left on its own profile
        #: while Gearbench cannot type its own keys (keep_onboard, hold_for_session), or None.
        self.kept_onboard: tuple[Path, int, int] | None = None

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
        """Why the keyboard node `found` names is held on its own profile (keep_onboard,
        hold_for_session), or None when it is not: it was left there, and Gearbench still
        cannot type its keys (untyped). connect does not open its node while it is held, and
        connects another keyboard plugged in that answers in its place (PerKeyboard.stand_in)."""
        return self.untyped() if found == self.kept_onboard else None

    def untyped_for(self, product: int) -> Untyped | None:
        """Why the keyboard of id `product` is left on its own profile at its connect
        (keep_onboard), or None where it is set up: a model whose G keys type its own keys while
        unbound (KeyboardModel.g_key_defaults), while Gearbench cannot type them (untyped)."""
        return self.untyped() if KEYBOARDS[product].g_key_defaults else None

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
        untyped = self.untyped_for(product)
        if untyped is None:
            return False
        dev = cast("Hidpp", self.keyboard.dev)
        onboard = dev.feature(F_ONBOARD)
        in_host = onboard is not None and read_mode(dev, onboard) == MODE_HOST
        said = self.keyboard.teardown_device(hand_back=in_host)
        if in_host:
            log.info("%s was in host mode, where nothing types its %s until %s: whatever "
                     "drove it last did not hand it back; %s", keyboard_name(product),
                     typed_words(product), untyped.until, said)
        else:
            self.kept_onboard = found
            log.log(untyped.level, "%s", kept_onboard_words(product, untyped))
        return True

    def hold_for_session(self) -> None:
        """While the session is not active, hand a keyboard whose G keys type its own keys
        (`typed_keys`) its own profile back, as a stop does, and hold it there (held_onboard):
        in HOST mode those keys type only through the virtual keyboard, which types nothing
        then, so whoever has the seat would have none (the owner's decision of 2026-10-19).
        Its node is the seat's meanwhile, and the daemon cannot open another keyboard's (the
        seat's ACL): stand_in passes it by, so none takes the held one's place. Once the session
        is active the loop's next connect sets it up again."""
        if self.keyboard.dev is None or "typed_keys" not in self.keyboard.keyboard_able:
            return
        product = cast("int", self.keyboard.keyboard_product)
        said = self.keyboard.teardown_device()
        # Its own model's node, from sysfs, nothing opened: with two keyboards plugged in, the
        # one connect looks for first (PerKeyboard.find_keyboard) can be the other's.
        self.kept_onboard = find_attached_keyboard(same_model_as=product)
        log.info("%s; %s", kept_onboard_words(product, SESSION_INACTIVE), said)
