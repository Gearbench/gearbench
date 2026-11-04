"""A headset page's preset library, the equalizer presets kept on this computer for the model
the page shows, read and saved on the worker thread, as the G432's page saves its own. Under
each equalizer's Preset row it adds Name, which renames the library's preset that plays, and,
after the bands, Save as new preset…, which asks a name and saves the bands the sliders show
as a new preset, and Delete preset, for the one that plays. The page lists the library
in its Preset row and picks one through the daemon (`request`). The file is read once the
page's reply names a model, at each of the page's reads after that and after each change.

A library is of one of two kinds, each a Shelf: a Centurion headset's (CENTURION,
gearbench.a50x_library), on the A50 X's page, and that of a model of the G432's family with an
equalizer of its own (HEADSET, gearbench.g432_library), in the equalizer group of the G432's
page. Building it runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol, Self, cast

from gi.repository import Adw, GLib, Gtk

from .. import a50x_library, eq_presets, g432_library
from ..a50x import Model
from ..centurion_features.equalizer import Band
from ..g432 import HeadsetModel
from ..wording import capitalized
from .style import (
    DELETE_HEADING,
    DELETE_OPENER,
    DELETE_PRESET,
    DELETE_RESPONSE,
    LIBRARY_UNREAD,
    NOT_DELETED,
    NOT_RENAMED,
    NOT_SAVED_NEW,
    SAVE_HEADING,
    EntryTexts,
    ask_name,
    confirm,
    displayable,
    name_button,
    name_refusal,
    offer_save_new,
    opener_row,
    presets_full,
    save_new_row,
    toast_not_saved,
    toast_note,
    watch_name,
)
from .worker import DaemonWorker, Result

#: Save as new preset…'s tooltip.
SAVE_NEW_HELP = "Keeps the bands the sliders show"
NAME = "Name"
DELETE_HELP = "The headset keeps playing its bands"
#: The confirmation says where the preset goes from: a user leaving G HUB can take Delete for
#: a write to the headset.
DELETE_BODY = ("It goes from this computer's library; "
               f"{DELETE_HELP[0].lower()}{DELETE_HELP[1:]}. This cannot be undone.")
#: A row's name for a screen reader on a page with more than one equalizer: its label, then
#: the equalizer's, so that no two controls in the window share a name.
OF_EQUALIZER = "{label}, {equalizer}"


class Keyed(Protocol):
    """A model a library is kept for, by its key: a50x.Model, g432.HeadsetModel."""

    @property
    def key(self) -> str: ...


class Shelved(Protocol):
    """A library a card shows, an eq_presets.Saved: its presets, numbered from 1, one by its
    number, and the changes the card makes but a save."""

    @property
    def presets(self) -> Sequence[eq_presets.Named]: ...

    def get(self, number: int) -> eq_presets.Record: ...

    def named(self, number: int, name: str) -> Self: ...

    def deleted(self, number: int) -> Self: ...


class Shelf[M: Keyed, L: Shelved, B](Protocol):
    """A kind of library, for models M, of libraries L, of presets of bands B: its file's
    read, change and path, the bands of a page's reply in its format (None for none it
    takes), the presets of an equalizer by number and name, the one it plays, and a library
    with a new preset."""

    def load(self, model: M) -> L: ...

    def edit(self, change: Callable[[L], L], model: M) -> L: ...

    def path(self, model: M) -> Path: ...

    def bands(self, model: M, instance: str, read: object) -> B | None: ...

    def of(self, library: L, instance: str) -> list[tuple[int, str]]: ...

    def playing(self, library: L, instance: str, bands: B) -> int | None: ...

    def added(self, library: L, name: str, instance: str, bands: B) -> L: ...


class Centurion:
    """The Centurion headsets' libraries (a50x_library): each preset of an equalizer, the
    headphones' or the microphone's, and bands in that equalizer's format (bands_of)."""

    load = staticmethod(a50x_library.load_library)
    edit = staticmethod(a50x_library.edit)
    path = staticmethod(a50x_library.library_path)

    @staticmethod
    def bands(model: Model, instance: str, read: object) -> tuple[Band, ...] | None:
        try:
            return a50x_library.bands_of(model, instance, read)
        except ValueError:
            return None

    @staticmethod
    def of(library: a50x_library.Library, instance: str) -> list[tuple[int, str]]:
        return [(number, preset.name) for number, preset in library.of(instance)]

    @staticmethod
    def playing(library: a50x_library.Library, instance: str,
                bands: tuple[Band, ...]) -> int | None:
        return library.playing(instance, bands)

    @staticmethod
    def added(library: a50x_library.Library, name: str, instance: str,
              bands: tuple[Band, ...]) -> a50x_library.Library:
        return library.added(a50x_library.Preset(name, instance, bands))


class Headset:
    """The libraries of the G432's family's models with an equalizer of their own
    (g432_library): one equalizer, the headphones', every preset of it, and bands of a
    frequency and a whole dB."""

    load = staticmethod(g432_library.load_library)
    edit = staticmethod(g432_library.edit)
    path = staticmethod(g432_library.library_path)

    @staticmethod
    def bands(_model: HeadsetModel, _instance: str,
              read: object) -> tuple[g432_library.Band, ...] | None:
        try:
            return g432_library.bands_from(read)
        except ValueError:
            return None

    @staticmethod
    def of(library: g432_library.Library, _instance: str) -> list[tuple[int, str]]:
        return [(number, preset.name) for number, preset in enumerate(library.presets, 1)]

    @staticmethod
    def playing(library: g432_library.Library, _instance: str,
                bands: tuple[g432_library.Band, ...]) -> int | None:
        return library.playing(bands)

    @staticmethod
    def added(library: g432_library.Library, name: str, _instance: str,
              bands: tuple[g432_library.Band, ...]) -> g432_library.Library:
        return library.added(g432_library.Preset(name, bands))


CENTURION: Shelf[Model, a50x_library.Library, tuple[Band, ...]] = Centurion()
HEADSET: Shelf[HeadsetModel, g432_library.Library, tuple[g432_library.Band, ...]] = Headset()
#: What holds an equalizer's rows: the Centurion page's expander of it, the G432's page's
#: group.
Holder = Adw.ExpanderRow | Adw.PreferencesGroup


def _add(holder: Holder, row: Gtk.Widget) -> None:
    if isinstance(holder, Adw.ExpanderRow):
        holder.add_row(row)
    else:
        holder.add(row)


def say_library_failure(toast: Callable[..., object], path: Path, error: object,
                        failed: str) -> str | None:
    """A read's or a change's failure for the preset library at `path`, toasted: an OSError
    with the path (toast_not_saved), any other refusal in words, `failed` the toast's line
    when it is too long. Answers why the file takes no change from now on, for the library's
    description: a LibraryPartial's sentence, the file read in part since the last read;
    else None."""
    if isinstance(error, OSError):
        toast_not_saved(toast, path, error)
    else:
        toast_note(toast, capitalized(str(error)), failed, one_sentence=True)
    return str(error) if isinstance(error, eq_presets.LibraryPartial) else None


@dataclass
class EqualizerRows[B]:
    """One equalizer's rows and what they show: the holder of its rows, what its Preset row
    says it plays, the bands it played at the page's last reply, None while they are not
    read, the bands Save as new keeps (the reply's, or the sliders' a page holds: keep), and
    whether that reply let its writes through, False once the page's headset is gone
    (writable)."""

    holder: Holder
    shown: Callable[[], str]
    name: Adw.EntryRow
    save: Adw.ButtonRow
    delete_row: Adw.ActionRow
    delete_button: Gtk.Button
    bands: B | None = None
    kept: B | None = None
    writable: bool = False


class LibraryCard[M: Keyed, L: Shelved, B]:
    """The library of the model shown, of the kind `shelf` keeps, and each equalizer's rows
    for it: `changed` is the page's, which lists it again in its Preset rows once a read or
    a change lands, and `kept_up` is told of a change that failed (Window.kept_up). `model`
    is the one the page's last reply named, None before one and for a model this install
    does not know, and `library` its file's last read, None before the first and while it
    does not read."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 kept_up: Callable[[], None], changed: Callable[[], None],
                 shelf: Shelf[M, L, B]) -> None:
        self._worker = worker
        self._toast = toast
        self._kept_up = kept_up
        self._changed = changed
        self._shelf = shelf
        self.model: M | None = None
        self.library: L | None = None
        #: Why the file takes no change: it does not read, or was read in part.
        self.refused: str | None = None
        #: A change is out, which the window's close waits for; a read is out.
        self.busy = self._reading = False
        self._texts = EntryTexts()
        #: Each equalizer's rows, by its socket word.
        self.rows: dict[str, EqualizerRows[B]] = {}

    def add_rows(self, instance: str, holder: Holder, shown: Callable[[], str],
                 equalizer: str | None) -> None:
        """`instance`'s rows, Name in `holder` now and the rest after its bands (to_end), each
        hidden until a reply gives bands; `shown` gives what its Preset row says it plays, the
        name Save as new starts from. `equalizer`, the equalizer's name on a page with more
        than one, None on a page with one, follows each control's label in its name for a
        screen reader (OF_EQUALIZER)."""
        name = Adw.EntryRow(title=NAME, show_apply_button=True)
        name.connect("apply", self.on_name, instance)
        watch_name(name)
        _add(holder, name)
        rows = self.rows[instance] = EqualizerRows(
            holder, shown, name, save_new_row(SAVE_NEW_HELP, self.on_save_new, instance),
            *opener_row(DELETE_PRESET, DELETE_HELP, DELETE_OPENER, self.on_delete, instance))
        if equalizer is not None:
            # The entry row's text is what takes the focus, and so what a screen reader says.
            for control, label in ((cast("Gtk.Text", name.get_delegate()), NAME),
                                   (rows.save, SAVE_HEADING),
                                   (rows.delete_button, DELETE_PRESET)):
                name_button(control, OF_EQUALIZER.format(label=label, equalizer=equalizer))
        self.to_end(instance)
        # Off until a reply gives bands: a page shows its rows before its first read.
        self._show_rows(instance)

    def to_end(self, instance: str) -> None:
        """Save as new and Delete preset after every row `instance`'s holder holds now: the
        page adds band rows as its replies give them."""
        rows = self.rows[instance]
        for row in (rows.save, rows.delete_row):
            if row.get_parent() is not None:
                rows.holder.remove(row)
            _add(rows.holder, row)

    # -- what the page reads -------------------------------------------------

    def of(self, instance: str) -> list[tuple[int, str]]:
        """The library's presets of `instance`, each its number and name; none before a
        read."""
        return [] if self.library is None else self._shelf.of(self.library, instance)

    def playing(self, instance: str) -> int | None:
        """The number of the library's preset `instance` plays, by the bands of the page's
        last reply; None for none."""
        bands = self.rows[instance].bands
        return None if bands is None or self.library is None else (
            self._shelf.playing(self.library, instance, bands))

    def request(self, number: int) -> dict:
        """The daemon's request that picks the library's preset `number`, one of those `of`
        gave."""
        return cast("L", self.library).get(number).request()

    def said(self) -> str | None:
        """The library's word for the description of the page's equalizer group, as markup,
        where it stays while it lasts: why the file takes no change, the sentence quoting the
        file's path, a lone surrogate in it as U+FFFD, and its cause; else, with the library
        full, why Save as new preset… is grayed out (style.presets_full). None for neither."""
        if self.refused is not None:
            return GLib.markup_escape_text(displayable(self.refused))
        return presets_full(None if self.library is None else self.library.presets)

    # -- what the page shows -------------------------------------------------

    def model_shown(self, model: M | None) -> None:
        """The library of `model`, the one the page's reply names, read once it is another
        model's than the one shown; none for None. The G522's two ids are one model."""
        if (None if model is None else model.key) == (
                None if self.model is None else self.model.key):
            return
        self.model, self.library, self.refused = model, None, None
        for instance in self.rows:
            self._show_rows(instance)
        self.refresh()

    def show(self, instance: str, bands: object, writable: bool) -> None:
        """`instance`'s rows for the bands of the page's reply, a list of the reply's band
        objects or anything else for none read, while its writes are let through or not: the
        bands in the format of the model shown (the shelf's `bands`), none for a model this
        install does not know. Save as new keeps them until `keep` gives others."""
        rows = self.rows[instance]
        rows.bands = rows.kept = (None if self.model is None
                                  else self._shelf.bands(self.model, instance, bands))
        self.writable(instance, writable)

    def keep(self, instance: str, bands: object) -> None:
        """`bands`, in the format of the page's reply, for Save as new to keep in place of the
        reply's, for sliders a page holds where the user moved them, until the next `show`."""
        self.rows[instance].kept = (None if self.model is None
                                    else self._shelf.bands(self.model, instance, bands))

    def writable(self, instance: str, writable: bool) -> None:
        """`instance`'s rows on or off as its writes are let through or not, for the bands of
        the page's last reply: off too once the page's headset is gone."""
        self.rows[instance].writable = writable
        self._show_rows(instance)

    def _show_rows(self, instance: str) -> None:
        """Save as new for bands a preset takes, and Name and Delete preset while a
        preset of the library plays, each on while its writes are let through, no change is
        out and the file takes a change; Save as new also needs the library read, room in it
        and none of its presets playing."""
        number, rows = self.playing(instance), self.rows[instance]
        name, delete = rows.name, rows.delete_row
        name.set_visible(number is not None)
        delete.set_visible(number is not None)
        # Not while a change is out: the library is the one before it, so a rename's row
        # would show the old name until the change lands. Grayed out meanwhile, the row
        # loses the cursor, so the landing fills it.
        if number is not None and not self.busy and not self._typing(name):
            self._texts.show(name, self._name(number))
        changeable = rows.writable and self.refused is None and not self.busy
        name.set_sensitive(changeable)
        delete.set_sensitive(changeable)
        save, read = rows.save, rows.bands is not None
        save.set_visible(read)
        # Grayed out while a preset of the library plays, whose twin the library refuses
        # (eq_presets.ALREADY_SAVED), the reason its tooltip: one group line serves two
        # equalizers on the A50 X's page.
        held = (None if number is None
                else capitalized(eq_presets.ALREADY_SAVED.format(number, self._name(number))))
        save.set_tooltip_text(SAVE_NEW_HELP if held is None else held)
        # Grayed out with the library full, the reason the group's line (said).
        offer_save_new(save, changeable and read and self.library is not None and held is None,
                       None if self.library is None else self.library.presets)

    def _name(self, number: int) -> str:
        """The name of the library's preset `number`, one it holds."""
        return cast("L", self.library).presets[number - 1].name

    def _typing(self, row: Adw.EntryRow) -> bool:
        root = row.get_root()
        return isinstance(root, Gtk.Window) and self._texts.typing(root, row)

    # -- reading ---------------------------------------------------------------

    def refresh(self) -> None:
        """Read the model's file on the worker, unless no model is shown or a read or a
        change is out."""
        if self.model is None or self.busy or self._reading:
            return
        self._reading = True
        self._worker.run(partial(self._shelf.load, self.model),
                         partial(self._loaded, self.model))

    def _loaded(self, model: M, result: Result) -> None:
        self._reading = False
        self._landed(model, result, LIBRARY_UNREAD)

    def _shows(self, model: M) -> bool:
        """Whether `model` is the one shown: the G522's two ids are one model."""
        return self.model is not None and model.key == self.model.key

    def _landed(self, model: M, result: Result, failed: str) -> None:
        """A read's or a change's result for `model`'s library, taken while that model is the
        one shown; for another, or when none shows, `refresh` reads the one shown, if any."""
        if self._shows(model):
            self._take(model, result, failed)
        else:
            self.refresh()

    def _take(self, model: M, result: Result, failed: str) -> None:
        """A read's or a change's result: the presets, or why the file takes no change (said),
        and a change's failure toasted, held when the file was read in part. The work gives a
        library or raises."""
        if isinstance(result, eq_presets.Saved):
            self.library = cast("L", result)
            self.refused = result.left_out or None
        elif isinstance(result, eq_presets.LibraryUnread):
            self.library, self.refused = None, str(result)
        elif (held := say_library_failure(self._toast, self._shelf.path(model), result,
                                          failed)) is not None:
            self.refused = held
        for instance in self.rows:
            self._show_rows(instance)
        self._changed()

    # -- changing --------------------------------------------------------------

    def on_save_new(self, row: Adw.ButtonRow, instance: str) -> None:
        """Ask the new preset's name, what the equalizer's Preset row says it plays to start,
        then save the bands at the click under it, those `keep` last gave, else the reply's,
        in the library of the model shown then. The row is on only with its bands, a library
        read and none of its presets playing (_show_rows). Bands a preset of the file has
        already as the save reads it are refused (eq_presets.ALREADY_SAVED)."""
        rows, model = self.rows[instance], cast("M", self.model)
        bands = cast("B", rows.kept)
        ask_name(row, rows.shown(),
                 lambda name: self._act(
                     lambda saved: self._shelf.added(saved, name, instance, bands),
                     NOT_SAVED_NEW, model),
                 SAVE_HEADING)

    def on_name(self, row: Adw.EntryRow, instance: str) -> None:
        """The library's preset `instance` plays renamed as typed, where the file holds it
        then among `instance`'s own (eq_presets.number_among)."""
        # The row shows only while one plays (_show_rows), so a library and its model show.
        number = cast("int", self.playing(instance))
        name = row.get_text().strip()
        if (refusal := name_refusal(name)) is not None:
            self._toast(refusal)
            return
        shown = self._name(number)
        self._act(lambda saved: saved.named(eq_presets.number_among(
            self._shelf.of(saved, instance), number, shown), name), NOT_RENAMED,
            cast("M", self.model))

    def on_delete(self, button: Gtk.Button, instance: str) -> None:
        """The library's preset `instance` plays deleted once asked, where the file of the
        model shown at the click holds it then among `instance`'s own; the headset keeps its
        bands."""
        # The row shows only while one plays (_show_rows), so a library and its model show.
        number, model = cast("int", self.playing(instance)), cast("M", self.model)
        shown = self._name(number)

        def delete() -> None:
            self._act(lambda saved: saved.deleted(eq_presets.number_among(
                self._shelf.of(saved, instance), number, shown)), NOT_DELETED, model)
        confirm(button, DELETE_HEADING.format(shown), DELETE_BODY, DELETE_RESPONSE, delete)

    def _act(self, change: Callable[[L], L], failed: str, model: M) -> None:
        """`change` made to `model`'s library on the worker: the model shown at the click,
        which a dialog's answer can follow after the page shows another or none, since the
        polls go on while a dialog is open."""
        self.busy = True
        for instance in self.rows:
            self._show_rows(instance)
        self._worker.run(partial(self._shelf.edit, change, model),
                         lambda result: self._acted(model, result, failed))

    def _acted(self, model: M, result: Result, failed: str) -> None:
        self.busy = False
        if isinstance(result, Exception):
            self._kept_up()
            # Said whichever model shows when it lands, and for a file that does not read:
            # _landed takes only the one shown's, and holds that refusal for the page's group
            # (said), which a read's says without a toast.
            if not self._shows(model) or isinstance(result, eq_presets.LibraryUnread):
                say_library_failure(self._toast, self._shelf.path(model), result, failed)
        self._landed(model, result, failed)
