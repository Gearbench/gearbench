"""The mice found and not attached, by node: one this user may not open (`denied_mice`), and one
whose setup was refused at its attach (`setup_refused`), each said once in the journal. They
give `problems` its first entries and the error of a command that needs a mouse while none is
attached (`none_attached`), and tell `attached.asked` why the mouse it was asked for is
missing (`found`)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...hidpp import NO_RULE, DeviceGone
from ...mouse import CABLED_IDS as MOUSE_CABLED, MODELS, find_attached_mice
from ...wording import reason
from ..shared import DEVICE_NOT_SET_UP, failure_reason, node_denied, said_once, unanswered
from .attached import denied_problem, no_mouse

if TYPE_CHECKING:
    from pathlib import Path

    from . import Mice


class Unattached:
    """The mice found and not attached, and what the journal said of each."""

    def __init__(self, mice: Mice) -> None:
        self.mice = mice
        #: By path, the refusal of a mouse node's open said, so a retry says it at debug.
        self._denied_said: dict[str, str] = {}
        #: By path, the product id and problem of the mouse whose setup was refused there: not
        #: one that timed out or whose node went, which the retry or a replug settles.
        self._not_set_up: dict[str, tuple[int, str]] = {}
        #: By path, the line said for the mouse whose setup was refused there (said_once).
        self._not_set_up_said: dict[str, str] = {}

    def open_refused(self, path: Path, exc: PermissionError) -> None:
        """Say that the mouse node at `path` cannot be opened: at warning once per node, a repeat
        at debug, since a missing udev rule or a seat held elsewhere is in no other journal
        line. wording.reason, since the error's own text repeats the path."""
        said_once(self._denied_said, str(path), f"cannot open the mouse's {path}: "
                  f"{reason(exc)}; {NO_RULE}", logging.WARNING)

    def opened(self, path: Path) -> None:
        """Forget the refusal said of the node at `path`, opened now."""
        self._denied_said.pop(str(path), None)

    def setup_refused(self, path: Path, product: int, exc: BaseException) -> str:
        """Say that the mouse of id `product` found on `path` could not be set up, at info once
        per node and cause: dropped without a word, it would read as no mouse. Its problem is
        kept for `problems`, but for a timeout or a node gone. Answers why, in words."""
        why = failure_reason(exc)
        said_once(self._not_set_up_said, str(path), f"found the {MODELS[product]} "
                  f"({product:04x}) on {path} but could not set it up: {why}", logging.INFO)
        if isinstance(exc, DeviceGone) or unanswered(exc):
            self._not_set_up.pop(str(path), None)
        else:
            self._not_set_up[str(path)] = product, DEVICE_NOT_SET_UP.format(
                MODELS[product], why, "its cable" if product in MOUSE_CABLED else "its receiver")
        return why

    def set_up(self, path: Path) -> None:
        """Forget what was said of the node at `path`, whose mouse is set up now."""
        self._not_set_up.pop(str(path), None)
        self._not_set_up_said.pop(str(path), None)

    def denied_mice(self) -> list[tuple[Path, int]]:
        """Mouse nodes attached to nothing because this user may not open them."""
        attached = {m.product for m in self.mice.attached}
        return [(path, product) for path, product, _index in find_attached_mice()
                if product not in attached and node_denied(path)]

    def not_set_up(self) -> list[tuple[int, str]]:
        """Each mouse node still there, and still that mouse's, whose setup was refused at its
        last attach, by its product id, with its problem: not one whose model is attached, on its
        other route too, nor a node number the kernel has given another mouse since."""
        if not self._not_set_up:
            # No walk of the nodes: `problems` runs at each pass, for the tray.
            return []
        covered = self.mice.attached_models()
        listed = {(str(path), product) for path, product, _index in find_attached_mice()
                  if product not in covered}
        return [(product, why) for path, (product, why) in self._not_set_up.items()
                if (path, product) in listed]

    def found(self) -> list[tuple[int, str]]:
        """Each mouse found and not attached, by its product id, with why: a node this user may
        not open (denied_problem), then one whose setup was refused."""
        return [*((product, denied_problem(path, product)) for path, product in self.denied_mice()),
                *self.not_set_up()]

    def problems(self) -> list[tuple[str, str]]:
        """`problems`' entries for the mice found and not attached, as (code, message)."""
        return [*(("mouse_no_permission", denied_problem(path, product))
                  for path, product in self.denied_mice()),
                *(("mouse_not_set_up", why) for _product, why in self.not_set_up())]

    def none_attached(self) -> str:
        """The error of a command that needs a mouse while none is attached (no_mouse)."""
        return no_mouse([why for _product, why in self.found()])
