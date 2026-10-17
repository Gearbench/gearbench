"""The Mouse tab's DPI stages group and its Report rate row: what onboard profile 1 holds,
or a loaded game profile's own, and the writes that change them.

This card owns those rows, what they were filled for and their handlers. Its page builds
it, places the row and the group, hands it each `mouse` reply and sends what it writes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Adw, GObject, Gtk

from ..onboard import DPI_STAGES
from .style import EntryTexts, Styler, confirm, explain, holds_focus, take_color_class

if TYPE_CHECKING:                       # pragma: no cover
    from .mouse_page import OfferWrite, Write
    from .worker import LoadingGuard

#: The report rate row's usual subtitle. On the row, not a tooltip: an Adw.ComboRow keeps its
#: tooltip out of the accessibility tree. A profile 1 write takes effect at its commit.
RATE_HELP = "Saved to onboard profile 1 at once"
#: The Shift stage row's note: the pick is written to flash and the paddle uses it at once.
STAGE_NOTE = "Saved to the mouse at once"
#: The Default stage row's subtitle. Measured 2026-09-24: a switch to a profile starts on its
#: default stage (G502 X PLUS), and a power cycle on the stage last used (both mice; in host
#: mode, the default). The row leaves the power cycle out for brevity (2026-10-02).
DEFAULT_STAGE_HELP = "Where the mouse starts on profile 1"
#: Picking a default stage writes at once, but the mouse stays on its current stage
#: (measured 2026-09-24 on the G502 X PLUS). On a game profile the rows edit, the daemon
#: keeps it on its stage too (Dpi.retune).
DEFAULT_STAGE_NOTE = "Saving keeps the current DPI"
#: The Default and Shift stage rows while a game profile is loaded in host mode, where they
#: edit the profile's own and leave onboard profile 1 alone. The Default row's note stays
#: DEFAULT_STAGE_NOTE.
GAME_STAGE_NOTE = "Saved to the game profile at once"
GAME_DEFAULT_HELP = "Where the mouse starts on the game profile, unless a DPI was chosen under it"
#: The DPI stages group's description, for profile 1 and for a game profile the rows edit, and
#: the Shift stage row's first sentence. They name no button: the G305 has one DPI button and
#: none of G6 to G8, and a binding moves the G502 X PLUS's DPI shift and steps to any of G4 to G9.
STAGES_HELP = "What the DPI buttons step through. Saved to onboard profile 1, backed up first."
GAME_STAGES_HELP = ("What the DPI buttons step through while the game profile is loaded. "
                    "Saved in the game profile.")
SHIFT_HELP = "Where the DPI shift button drops to while held"
#: What a game profile can hold of its own, in the order the Game profile row names it
#: and Use profile 1's resets it: the reset's request, the `game_profile` key, the words.
GAME_OWN = (({"command": "mouse_dpi_stages", "reset": True}, "dpi_stages", "DPI stages"),
            ({"command": "mouse_rate", "reset": True}, "report_rate", "report rate"))
FACTORY_HELP = "The stages, default and shift in the mouse's ROM"
#: A stage color the dots take, checked because it goes into a style sheet.
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
#: The stage dots' color classes: the prefix, then the color's hex digits.
DOT_PREFIX = "g915-stage-dot-"
#: What a stage dot says, in its tooltip and to a screen reader: the color's name from the
#: reply's `stage_color_names`, never its hex value (the owner's decision of 2026-10-05), and
#: for a color the daemon sends no name for.
DOT_NAMED = "The mouse flashes {} on this stage"
DOT_UNNAMED = "The color the mouse flashes on this stage"
#: An apply's refusal of a saved stage's emptied row, by its number.
STAGE_EMPTIED = "Stage {} is empty. Its trash button removes it."
#: The stage remove dialog's body, by the layer the rows were filled for: a game profile's own
#: stages, by its name, or onboard profile 1.
REMOVE_FROM_GAME = "The rest are saved as {}'s own DPI stages; onboard profile 1 is unchanged."
REMOVE_FROM_PROFILE_1 = ("Onboard profile 1 is rewritten without it, after a backup; Restore "
                         "profile backup… undoes it.")


class StagesCard:
    """The DPI stages group and the Report rate row, and the commands they send.

    `parent` is the window, for where the cursor is and what a dialog is presented over.
    `loading` is the page's guard, held while a reply sets a row. `write` and `offer_write`
    are the page's write and its dialog buttons, `toast` the window's, and `product` gives
    the mouse the last reply was about, by product id.
    """

    def __init__(self, parent: Adw.ApplicationWindow, loading: LoadingGuard, write: Write,
                 offer_write: OfferWrite, toast: Callable[..., object],
                 product: Callable[[], str | None]) -> None:
        self._parent = parent
        self._loading = loading
        self._write = write
        self._offer_write = offer_write
        self._toast = toast
        self._product = product
        #: The text this card last put in each stage row, by a poll or by an apply.
        self._shown = EntryTexts()
        #: The rates and the stages the dropdowns list, so a model is rebuilt only when
        #: they change.
        self._rates: list[int] = []
        self._stages: list[int] = []
        #: The mouse whose stages the stage rows show: they keep their text across polls while
        #: edited, so they can show one mouse's list while the page shows another's.
        self._entry_for: str | None = None
        #: The game profile whose own stages those rows show, or None for profile 1's: a load
        #: or an unload moves the window's layer while the rows keep what is being typed.
        self._entry_overlay: str | None = None
        #: That mouse's default and shift stage when the entry was filled: the two dropdowns
        #: follow every poll, so they can hold another mouse's.
        self._entry_choice: tuple[int | None, int | None] = (None, None)
        #: How many stage rows hold one of the daemon's stages (_show_stages).
        self._stage_count = 0
        #: The slot of each stage the rows were filled with: profile 1's stages flash their
        #: slot's color, unused slots counted; empty for a game profile's own, which count by
        #: position (Mouse._dpi_level).
        self._dot_slots: list[int] = []
        #: The loaded game profile's part of the last reply (game_profile), or None.
        self._game: dict | None = None

    @property
    def flash_rows(self) -> tuple[Gtk.Widget, ...]:
        """The rows whose changes rewrite onboard profile 1, or a loaded game profile's own
        stages and rate."""
        return (self.rate_row, *self.stage_rows, self.add_stage_row, self.default_stage_row,
                self.shift_stage_row, self.factory_row, self.game_row)

    def close(self) -> None:
        """Take the stage dots' sheet off the display, which holds it."""
        self._dot_css.destroy()

    def build_rate_row(self) -> Adw.ComboRow:
        """The Report rate row, which holds profile 1's rate or a loaded game profile's own
        as the stage rows hold their stages."""
        self.rate_row = Adw.ComboRow(title="Report rate", subtitle=RATE_HELP,
                                     model=Gtk.StringList.new(["—"]))
        self.rate_row.connect("notify::selected", self.on_rate)
        return self.rate_row

    def build(self) -> Adw.PreferencesGroup:
        """The DPI stages, whose they are, and the default and shift stage."""
        stages = self.group = Adw.PreferencesGroup(title="DPI stages",
                                                   description=STAGES_HELP)
        # Whose stages and rate the rows hold while a loaded game profile is what they edit or
        # has its own (_show_game), and the way back to profile 1's. Not markup: its name.
        self.game_row = Adw.ActionRow(title="Game profile", use_markup=False, visible=False)
        self.game_reset = Gtk.Button(label="Use profile 1's", valign=Gtk.Align.CENTER,
                                     visible=False)
        self.game_reset.connect("clicked", self.on_game_reset)
        self.game_row.add_suffix(self.game_reset)
        stages.add(self.game_row)
        # One row per stage, all made here and shown per the stage count: a group only appends,
        # so a row made later would land under the rows after it. Applying any row sends them
        # all (on_dpi_stages). The dot is the stage's indicator color (show_stage_colors).
        self._dot_css = Styler()
        self.stage_rows: list[Adw.EntryRow] = []
        self.stage_dots: list[Gtk.Box] = []
        self.stage_removes: list[Gtk.Button] = []
        for index in range(DPI_STAGES):
            entry = Adw.EntryRow(title=f"Stage {index + 1}", visible=False,
                                 show_apply_button=True)
            entry.connect("apply", self.on_dpi_stages)
            # An image to a screen reader, which show_stage_colors names with the tooltip's words.
            dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False,
                          css_classes=["g915-stage-dot"],
                          accessible_role=Gtk.AccessibleRole.IMG)
            dot.set_size_request(14, 14)
            entry.add_prefix(dot)
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Remove this stage", css_classes=["flat"])
            remove.connect("clicked", lambda _b, n=index: self.on_remove_stage(n))
            entry.add_suffix(remove)
            self.stage_rows.append(entry)
            self.stage_dots.append(dot)
            self.stage_removes.append(remove)
            stages.add(entry)
        self.add_stage_row = Adw.ButtonRow(title="Add a stage",
                                           start_icon_name="list-add-symbolic")
        self.add_stage_row.connect("activated", self.on_add_stage)
        stages.add(self.add_stage_row)
        self.default_stage_row = Adw.ComboRow(
            title="Default stage", subtitle=f"{DEFAULT_STAGE_HELP}. {DEFAULT_STAGE_NOTE}",
            model=Gtk.StringList.new(["—"]))
        self.default_stage_row.connect("notify::selected", lambda r, _p: self._stage_choice())
        stages.add(self.default_stage_row)
        self.shift_stage_row = Adw.ComboRow(
            title="Shift stage",
            subtitle=f"{SHIFT_HELP}. {STAGE_NOTE}",
            model=Gtk.StringList.new(["—"]))
        self.shift_stage_row.connect("notify::selected", lambda r, _p: self._stage_choice())
        stages.add(self.shift_stage_row)
        self.factory_row = Adw.ActionRow(title="Factory stages", subtitle=FACTORY_HELP)
        factory = Gtk.Button(label="Use factory stages…", valign=Gtk.Align.CENTER)
        factory.connect("clicked", self.on_factory_stages)
        self.factory_row.add_suffix(factory)
        stages.add(self.factory_row)
        return stages

    def show(self, info: dict, force: bool, switched: bool) -> None:
        """Rate and stages, from the profile image the daemon read or a loaded game
        profile's own, and each stage's dot from the reply's colors.

        `switched` says the reply is about another mouse than the last one,
        which clears what this reply leaves out, as `force` does.
        """
        onboard = info.get("onboard") or {}
        game = info.get("game_profile") if isinstance(info.get("game_profile"), dict) else None
        self._game = game
        # The rows edit the game profile's own only where the daemon says they do: `edited` is
        # that profile, or empty where they do not.
        edited = game if game and game.get("edits") else {}
        self._show_game(game)
        self._show_rate(info, onboard, edited, fresh=force or switched)
        self._show_stage_list(onboard, edited, force)
        # After the stage rows, whose slots the dots' colors go by.
        self.show_stage_colors(info.get("stage_colors"), info.get("stage_color_names"))

    def _show_rate(self, info: dict, onboard: dict, edited: dict, fresh: bool) -> None:
        """The Report rate row: the mouse's rates, on the game profile's own, profile 1's or
        the live one, and a subtitle saying whose."""
        rates = [int(r) for r in info.get("report_rates") or []]
        if rates and rates != self._rates:
            self._rates = rates
            self._loading.put_model(self.rate_row,
                                    Gtk.StringList.new([f"{r} Hz" for r in rates]))
        elif not rates and fresh and self._rates:
            # This mouse named none, and the last one's would be offered for it.
            self._rates = []
            self._loading.put_model(self.rate_row, Gtk.StringList.new(["—"]))
        stored = onboard.get("report_rate")
        live = info.get("report_rate")
        own_rate = edited.get("report_rate")
        # Live where no rate is stored, which is a mouse without an onboard profile or
        # one whose sector is unknown: the rate it runs at is what the row would change.
        chosen = next((rate for rate in (own_rate, stored, live) if rate in self._rates), None)
        if chosen is not None:
            self._loading.put_selected(self.rate_row, self._rates.index(chosen))
        elif fresh:
            self._loading.put_selected(
                self.rate_row, Gtk.INVALID_LIST_POSITION if self._rates else 0)
        if own_rate:
            self.rate_row.set_subtitle(f"{edited['name']}'s own"
                                       + (f"; profile 1 stores {stored} Hz" if stored else ""))
        elif edited:
            self.rate_row.set_subtitle(
                f"Profile 1's; a change is saved as {edited['name']}'s own")
        else:
            self.rate_row.set_subtitle(
                f"Profile 1 stores {stored} Hz; the mouse is running at {live} Hz"
                if stored and live and stored != live else RATE_HELP)

    def _show_stage_list(self, onboard: dict, edited: dict, force: bool) -> None:
        """The stage rows, left as typed while being edited unless `force`, and the Default
        stage and Shift stage dropdowns, from profile 1's stages or the game profile's own,
        with the subtitles that say whose."""
        stages = [int(s) for s in onboard.get("dpi_stages") or []]
        default = min(int(onboard.get("default_dpi_index", 0)), max(len(stages) - 1, 0))
        shift = min(int(onboard.get("shift_dpi_index", 0)), max(len(stages) - 1, 0))
        own = [int(s) for s in edited.get("dpi_stages") or []]
        if own:
            stages = own
            default = own.index(edited["default"]) if edited.get("default") in own else 0
            shift = own.index(edited["shift"]) if edited.get("shift") in own else 0
        if stages != self._stages:
            self._stages = stages
            labels = [str(s) for s in stages] or ["—"]
            self._loading.put_model(self.default_stage_row, Gtk.StringList.new(labels))
            self._loading.put_model(self.shift_stage_row, Gtk.StringList.new(labels))
        if force or not self._editing_stages():
            self._show_stages(stages, default, shift)
            self._dot_slots = [] if own else [int(s) for s in onboard.get("dpi_slots") or []]
            self._entry_for = self._product()
            self._entry_overlay = edited.get("name")
            self._entry_choice = ((stages[default], stages[shift]) if stages
                                  else (None, None))
        if stages:
            self._loading.put_selected(self.default_stage_row, default)
            self._loading.put_selected(self.shift_stage_row, shift)
        self.default_stage_row.set_subtitle(
            f"{GAME_DEFAULT_HELP if edited else DEFAULT_STAGE_HELP}. {DEFAULT_STAGE_NOTE}")
        self.shift_stage_row.set_subtitle(
            f"{SHIFT_HELP}. {GAME_STAGE_NOTE if edited else STAGE_NOTE}")
        self.group.set_description(GAME_STAGES_HELP if edited else STAGES_HELP)

    def _show_stages(self, stages: list[int], default: int, shift: int) -> None:
        """One row per stage, titled with its number and whether it is the default or the
        shift stage; the rest hidden and emptied. A stage can be removed while there are
        two or more, and one added while there is at least one and fewer than DPI_STAGES."""
        self._stage_count = len(stages)
        for index, row in enumerate(self.stage_rows):
            shown = index < len(stages)
            marks = [what for what, at in (("default", default), ("shift", shift))
                     if shown and at == index]
            row.set_title(" · ".join([f"Stage {index + 1}", *marks]))
            self._shown.show(row, str(stages[index]) if shown else "")
            row.set_visible(shown)
            self.stage_removes[index].set_sensitive(len(stages) > 1)
        self.add_stage_row.set_visible(bool(stages) and len(stages) < DPI_STAGES)

    def _editing_stages(self) -> bool:
        """The stage rows as one entry: True while any holds text the page did not put there
        and the cursor is in one or a dialog is up over the window, which takes the cursor (a
        stage's trash button opens one); or while the cursor is in an empty row Add a stage
        showed, which a poll would hide. Otherwise a poll shows the daemon's list
        (EntryTexts.typing)."""
        focus = self._parent.get_focus()
        rows = [(index, row) for index, row in enumerate(self.stage_rows) if row.get_visible()]
        at = next((index for index, row in rows if holds_focus(focus, row)), None)
        typed = any(self._shown.typed(row) for _index, row in rows)
        if at is None:
            return typed and self._parent.get_visible_dialog() is not None
        return typed or (at >= self._stage_count and not self.stage_rows[at].get_text().strip())

    def show_stage_colors(self, colors: object, names: object = None) -> None:
        """Each stage's dot in its indicator color, from the reply's `stage_colors` (HEX_COLOR),
        saying the color's name from its `stage_color_names` (DOT_NAMED); no dots for a model
        without them. The colors are per slot: a dot takes its stage's slot's (_dot_slots), and
        one past those, a row Add a stage showed, the slot of its position, where a write of the
        rows puts it."""
        colors = colors if isinstance(colors, list) else []
        names = names if isinstance(names, list) else []
        slots = [*self._dot_slots, *range(len(self._dot_slots), len(colors))]
        # Paired before the colors are checked, so a name stays with its color.
        wanted = [(colors[slot], names[slot] if slot < len(names) else None)
                  for slot in slots
                  if slot < len(colors) and isinstance(colors[slot], str)
                  and HEX_COLOR.fullmatch(colors[slot])]
        # A class named by the color, which each dot takes after the load, as
        # the picker's swatch does (ColorPicker._refresh_css).
        self._dot_css.update("".join(
            f".{DOT_PREFIX}{color[1:]} {{ background-color: {color}; }}"
            for color in dict.fromkeys(color for color, _name in wanted)))
        for index, dot in enumerate(self.stage_dots):
            dot.set_visible(index < len(wanted))
            if index < len(wanted):
                color, name = wanted[index]
                take_color_class(dot, DOT_PREFIX, DOT_PREFIX + color[1:])
                # In words too: the class carries the color to the eye alone.
                said = DOT_NAMED.format(name) if name and isinstance(name, str) else DOT_UNNAMED
                dot.set_tooltip_text(said)
                dot.update_property([Gtk.AccessibleProperty.LABEL], [said])

    def _show_game(self, game: dict | None) -> None:
        """The Game profile row, from the reply's `game_profile`: whose stages and rate the rows
        below hold, and Use profile 1's while they edit it and it has its own. Hidden with none
        loaded, or when the rows do not edit it and it has none of its own."""
        game = game or {}
        name, edits = game.get("name"), bool(game.get("edits"))
        own = [words for _reset, key, words in GAME_OWN if game.get(key)]
        self.game_row.set_visible(edits or bool(own))
        self.game_reset.set_visible(edits and bool(own))
        if not (edits or own):
            return
        if not edits:
            said = (f"{name} has its own {' and '.join(own)}, in force only while "
                    f"Gearbench drives the mouse in host mode.")
        elif not own:
            said = (f"{name} uses profile 1's DPI stages and report rate. A change below "
                    f"is saved as its own.")
        else:
            said = (f"{name} has its own {' and '.join(own)}, in force "
                    + ("while it is loaded." if game.get("applies")
                       else "once the mouse is in host mode.")
                    + " A change below is saved to it.")
        self.game_row.set_subtitle(said)

    def on_game_reset(self, _b: Gtk.Button | None) -> None:
        """Use profile 1's, once confirmed: a `reset` of the loaded game profile's own stages,
        then rate, each saved to its file. Sent for the mouse and layer the rows showed at the
        click, so a profile loaded meanwhile is refused, not reset."""
        game, layer, product = self._game or {}, self._rows_layer(), self._product()
        own = [(reset, words) for reset, key, words in GAME_OWN if game.get(key)]
        if not own:
            return
        requests = [dict(reset) for reset, _words in own]
        what = " and ".join(words for _reset, words in own)
        name = game.get("name")

        def next_one(_reply: dict | None = None) -> None:
            if requests:
                self._write(requests.pop(0), next_one, product=product, layer=layer)
        self._ask(f"Remove {name}'s own {what}?",
                  f"While {name} is loaded, the mouse uses onboard profile 1's {what}. "
                  "The game profile's file is saved at once.", "Remove", next_one)

    def _rows_layer(self) -> dict:
        """_write's `layer` for the Default and Shift dropdowns, the rate row, Use profile 1's
        and the factory dialog: the game profile whose own the last poll filled them from,
        or None for profile 1's. The window's status moves its layer up to a poll before
        they follow."""
        game = self._game or {}
        return {"overlay": game.get("name") if game.get("edits") else None}

    def on_rate(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active or not self._rates:
            return
        hz = self._rates[row.get_selected()]

        def done(reply: dict) -> None:
            if reply.get("written") == "live":
                self._toast(f"Report rate set to {hz} Hz live (the mouse is in host mode)")
        self._write({"command": "mouse_rate", "value": hz}, done, layer=self._rows_layer())

    def on_dpi_stages(self, _row: Adw.EntryRow) -> None:
        """A stage row was applied: every row shown is one list, sent whole.
        No loading guard, as on_dpi has none."""
        self._send_stages([row for row in self.stage_rows if row.get_visible()])

    def _send_stages(self, rows: list[Adw.EntryRow]) -> None:
        """Send what `rows` hold as the stage list, to the mouse they were filled for. A row
        Add a stage showed and left empty is no stage; a saved stage's row emptied is
        refused, since only its trash button removes a stage, after asking."""
        emptied = [n + 1 for row in rows
                   if (n := self.stage_rows.index(row)) < self._stage_count
                   and not row.get_text().strip()]
        if emptied:
            self._toast(STAGE_EMPTIED.format(emptied[0]))
            return
        stages: list[int] = []
        for row in rows:
            if not (text := row.get_text().strip()):
                continue
            try:
                stages.append(int(text))
            except ValueError:
                self._toast(f"Stage {self.stage_rows.index(row) + 1} is not a number. "
                            "Type a DPI like 1600.")
                return
        for row in rows:
            self._shown.took(row)
        # The dropdowns hold the old list's choice: one the new list no longer holds is left to
        # the daemon, which keeps the slot's position; sent as an explicit default, it is refused.
        # After a poll about another mouse, the choice taken with the rows is used.
        if self._entry_for is not None and self._entry_for != self._product():
            default, shift = self._entry_choice
        else:
            default = self._selected_stage(self.default_stage_row)
            shift = self._selected_stage(self.shift_stage_row)

        def moved(reply: dict) -> None:
            notes = [f"The {what} stage is now {reply[what]}."
                     for what, before in (("default", default), ("shift", shift))
                     if before is not None and before not in stages]
            if notes:
                self._toast(" ".join(notes))

        self._write({"command": "mouse_dpi_stages", "value": stages,
                     "default": default if default in stages else None,
                     "shift": shift if shift in stages else None}, moved,
                    product=self._entry_for, layer={"overlay": self._entry_overlay})

    def on_remove_stage(self, index: int) -> None:
        """Remove one stage once the user confirms (a delete that saves asks first: the owner's
        decision of 2026-09-27). A row Add a stage showed and nobody applied is hidden at once,
        with any after it.

        No loading guard, here or in _remove_stage: a click and a dialog's response come only
        from the user, never from a poll's set_*."""
        rows = [row for row in self.stage_rows if row.get_visible()]
        if index >= self._stage_count:
            for row in self.stage_rows[index:]:
                self._shown.show(row, "")
                row.set_visible(False)
            self.add_stage_row.set_visible(True)
            return
        # The last saved stage stays: its trash button is insensitive (_show_stages), though an
        # empty row Add a stage showed is beside it.
        if self._stage_count < 2:
            return
        # An emptied row, which an apply refuses, is named by the stage the page showed.
        text = rows[index].get_text().strip() or self._shown.last(rows[index])
        # The layer the rows were filled for, which Remove writes to: rows kept while being
        # typed into can be another mouse's or layer's than the last poll's. A game profile
        # with no stages of its own shows profile 1's, and the write gives it its own.
        where = (REMOVE_FROM_GAME.format(self._entry_overlay) if self._entry_overlay is not None
                 else REMOVE_FROM_PROFILE_1)
        seen = self._rows_filled(rows)
        self._ask(f"Remove stage {index + 1}, {text} DPI?", where, "Remove",
                  lambda: self._remove_stage(index, seen))

    def _rows_filled(self, rows: list[Adw.EntryRow]) -> tuple:
        """What the stage rows hold, with the mouse and the game profile they were filled for. A
        row Add a stage showed and left empty is no stage (_send_stages), and a poll under the
        dialog hides it, so it is left out."""
        return self._entry_for, self._entry_overlay, tuple(
            text for row in rows
            if (text := row.get_text()).strip() or self.stage_rows.index(row) < self._stage_count)

    def _remove_stage(self, index: int, seen: tuple) -> None:
        """The confirmed removal: the rows shown without it, sent as an apply sends them, with
        any typing in the others, to their mouse. Nothing, with a toast, if the rows changed
        while the dialog was up: a poll keeps them while one holds typing (_editing_stages) and
        refills them otherwise, for another mouse or game profile too, where a stage can hold
        the same value. Checked before the index, which a shorter refilled list can leave out."""
        rows = [row for row in self.stage_rows if row.get_visible()]
        if self._rows_filled(rows) != seen:
            self._toast("Nothing was removed: the stages changed while the dialog was open")
            return
        if index >= self._stage_count:
            return
        self._send_stages([row for n, row in enumerate(rows) if n != index])

    def _ask(self, heading: str, body: str, response: str, then: Callable[[], None]) -> None:
        confirm(self._parent, heading, body, response, then)

    def on_add_stage(self, _row: Adw.ButtonRow) -> None:
        """Show the next stage row, empty and focused. Nothing is sent until it is
        applied, which sends the list with it (on_dpi_stages). Its trash button takes it
        away unasked (on_remove_stage), so it works with one saved stage too."""
        index = sum(1 for row in self.stage_rows if row.get_visible())
        if index >= DPI_STAGES:
            return
        row = self.stage_rows[index]
        self._shown.show(row, "")
        row.set_visible(True)
        self.stage_removes[index].set_sensitive(True)
        self.add_stage_row.set_visible(index + 1 < DPI_STAGES)
        row.grab_focus()

    def on_factory_stages(self, _b: Gtk.Button | None) -> None:
        """Factory stages, behind a confirmation: it replaces the list, the default and the
        shift, in profile 1 or the loaded game profile's own."""
        game = self._game or {}
        dialog = Adw.AlertDialog(heading="Use the factory stages?")
        if game.get("edits"):
            where = (f"They are saved as {game.get('name')}'s own DPI stages; onboard "
                     f"profile 1 is unchanged.")
        else:
            where = "They are saved to onboard profile 1, backed up first."
        explain(dialog, paragraphs=(
            "The stages, default and shift in the mouse's ROM replace the current ones.",
            where))
        self._offer_write(dialog, "factory", "Use factory stages",
                          Adw.ResponseAppearance.SUGGESTED,
                          {"command": "mouse_dpi_stages", "factory": True},
                          layer=self._rows_layer())
        dialog.present(self._parent)

    def _selected_stage(self, row: Adw.ComboRow) -> int | None:
        index = row.get_selected()
        return self._stages[index] if 0 <= index < len(self._stages) else None

    def _stage_choice(self) -> None:
        if self._loading.active or not self._stages:
            return
        self._write({"command": "mouse_dpi_stages", "value": self._stages,
                     "default": self._selected_stage(self.default_stage_row),
                     "shift": self._selected_stage(self.shift_stage_row)},
                    layer=self._rows_layer())
