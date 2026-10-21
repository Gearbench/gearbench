"""The profiles and macros pack: the game lighting profiles and macros that ship with
Gearbench, as a checklist with every one ticked, then a review page that writes nothing
before Import. The checklist's filter, buttons and count, what its check boxes do, and
the review page are the G HUB import's (`import_dialog.Importer`). Opened from the Game
profiles dialog's Import pack… button. Load for starts ticked, since the pack chose each
profile's programs for its game (the owner's decision of 2026-09-28). A name already
taken defaults to Keep mine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from gi.repository import Adw, Gtk

from .. import import_plan, pack
from ..macros import Macro
from ..profiles import GameProfile
from ..wording import counted
from .import_dialog import (
    NO_MATCH as NO_PROFILE_MATCH,
    Importer,
    Picker,
    read_then_show,
    search_text,
)
from .style import left_out_row, plain_row, weakly

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

TITLE = "Profiles and macros pack"
GROUPS = {pack.PROFILE: "Game profiles", pack.MACRO: "Macros"}
NONE = {pack.PROFILE: "No game profiles in the pack", pack.MACRO: "No macros in the pack"}
NO_MATCH = {pack.PROFILE: NO_PROFILE_MATCH,
            pack.MACRO: "No macro matches the filter"}
BROKEN = "Broken, never imported"
#: Both halves of what unticking does, from `pack.make_plan`.
LOAD_FOR_HINT = "Untick to load it by hand, or to keep your programs if it replaces your profile"


def load() -> tuple[pack.Pack, import_plan.Existing]:
    """The pack and what Gearbench has. On the worker: both read files."""
    return pack.read_pack(), import_plan.existing_from_disk()


def open_pack(window: Window) -> None:
    """Read the pack on the worker, then show it (import_dialog.read_then_show)."""
    def show(result: tuple[pack.Pack, import_plan.Existing]) -> None:
        found, existing = result
        PackDialog(window, found, existing).present(window)
    read_then_show(window, load, show)


class PackDialog(Importer):
    """The two pages: the checklist, then review and import."""

    CLASHES: ClassVar[dict[str, str]] = {import_plan.SKIP: "Keep mine",
                                         import_plan.REPLACE: "Use the pack's"}

    def __init__(self, window: Window, found: pack.Pack, existing: import_plan.Existing) -> None:
        super().__init__(window, existing, TITLE)
        self.pack = found
        self.pickers[pack.PROFILE] = Picker(found.profiles, search_text)
        self.pickers[pack.MACRO] = Picker(found.macros, search_text)
        for picker in self.pickers.values():
            picker.select_all()
        self.pickers[pack.PROFILE].with_programs = {p.name for p in found.profiles
                                                   if p.processes}
        self.nav.add(self._pick_page())
        self._update_count()

    # -- the first page: the checklist -------------------------------------

    def _pick_page(self) -> Adw.NavigationPage:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                       margin_bottom=12, margin_start=12, margin_end=12)
        self._controls(body)
        for kind, items in ((pack.PROFILE, self.pack.profiles), (pack.MACRO, self.pack.macros)):
            listbox = self._list(kind, NO_MATCH[kind] if items else NONE[kind])
            for item in items:
                listbox.append(self._row(kind, item))
            group = Adw.PreferencesGroup(title=GROUPS[kind])
            if kind == pack.MACRO and items:
                group.set_description(pack.MACRO_RISK)
            group.add(listbox)
            body.append(group)
        if self.pack.broken:
            group = Adw.PreferencesGroup(title=BROKEN)
            for file, why in self.pack.broken:
                group.add(left_out_row(file, why))
            body.append(group)
        return self._page("pick", "Choose what to import", self._counted(
            len(self.pack.profiles), len(self.pack.macros)), body, self.next)

    def _row(self, kind: str, item: GameProfile | Macro) -> Adw.PreferencesRow:
        """A game profile, with its Load for row under it when it names programs,
        or a macro with its steps; each with its Import check box, ticked."""
        if isinstance(item, GameProfile):
            subtitle = pack.lighting_text(item)
            detail = self._programs_row(item) if item.processes else None
        else:
            subtitle = import_plan.macro_text(item.repeat, item.steps)
            detail = plain_row(
                Adw.ActionRow, "Steps",
                "\n".join(line.strip() for line in import_plan.step_lines(item.steps)),
                subtitle_selectable=True)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=True)
        check.update_property([Gtk.AccessibleProperty.LABEL], [f"Import {item.name}"])
        check.connect("toggled", weakly(self._on_check), kind, item.name)
        self.checks[kind, item.name] = check
        if note := self.pack.note(kind, item.name):
            subtitle = f"{subtitle}\n{note}"
        # Not markup: a name from the pack is text like any other.
        row: Adw.ExpanderRow | Adw.ActionRow
        if detail is not None:
            row = plain_row(Adw.ExpanderRow, item.name, subtitle, name=item.name)
            row.add_row(detail)
        else:
            row = plain_row(Adw.ActionRow, item.name, subtitle, name=item.name,
                            activatable_widget=check)
        row.add_prefix(check)
        return row

    def _programs_row(self, profile: GameProfile) -> Adw.ActionRow:
        # Holds the picker, not the dialog, so the closed dialog is still freed.
        picker = self.pickers[pack.PROFILE]
        return self._load_for_row(pack.PROFILE, profile, LOAD_FOR_HINT,
                                  lambda c: picker.set_programs(profile.name, c.get_active()))

    # -- what the review page shows ----------------------------------------

    def make_plan(self) -> import_plan.Plan:
        profiles = self.pickers[pack.PROFILE]
        return pack.make_plan(self.pack, profiles.chosen(), self.pickers[pack.MACRO].chosen(),
                              self.existing, self.choices, default=self.default_choice,
                              with_programs=profiles.with_programs)

    def plan_title(self) -> str:
        return self._counted(len(self.pickers[pack.PROFILE].ticked),
                             len(self.pickers[pack.MACRO].ticked))

    @staticmethod
    def _counted(profiles: int, macros: int) -> str:
        """"3 game profiles and 1 macro", leaving out a kind there is none of."""
        parts = [counted(n, word)
                 for n, word in ((profiles, "game profile"), (macros, "macro")) if n]
        return " and ".join(parts) or "Empty"
