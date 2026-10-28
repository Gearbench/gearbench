"""A headset page's preset library, the equalizer presets kept on this computer for the model
the page shows, read and saved on the worker thread, as the G432's page saves its own. Under
each equalizer's Preset row it adds Name, which renames the library's preset that plays, and,
after the bands, Save as new preset…, which asks a name and saves the bands the equalizer
plays as a new preset, and Delete preset, for the one that plays. The page lists the library
in its Preset row and picks one through the daemon (`request`). The file is read once the
page's reply names a model, at each of the page's reads after that and after each change.

A library is of one of two kinds, each a Shelf: a Centurion headset's (CENTURION,
gearbench.a50x_library), on the A50 X's page, and that of a model of the G432's family with an
equalizer of its own (HEADSET, gearbench.g432_library), in the equalizer group of the G432's
page. Building it runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    DELETE_RESPONSE,
    LIBRARY_UNREAD,
    NOT_DELETED,
    NOT_RENAMED,
    NOT_SAVED_NEW,
    SAVE_HEADING,
    EntryTexts,
    ask_name,
    confirm,
    delete_preset_row,
    displayable,
    name_refusal,
    offer_save_new,
    save_new_row,
    toast_not_saved,
    toast_note,
    watch_name,
)
from .worker import DaemonWorker, Result

#: Save as new preset…'s tooltip.
SAVE_NEW_HELP = "Keeps the bands playing now"
NAME = "Name"
DELETE_HELP = "The headset keeps playing its bands"
DELETE_BODY = f"{DELETE_HELP}. This cannot be undone."
#: A library read's toast line when its failure is too long for a toast, the whole failure
#: behind Details.
NOT_LOADED = LIBRARY_UNREAD


class Keyed(Protocol):
    """A model a library is kept for, by its key: a50x.Model, g432.HeadsetModel."""

    @property
    def key(self) -> str: ...


class Shelved(Protocol):
    """A library a card shows, an eq_presets.Saved: its presets, numbered from 1, and the
    changes the card makes but a save."""

    @property
    def presets(self) -> Sequence[eq_presets.Named]: ...

    def named(self, number: int, name: str) -> Self: ...

    def deleted(self, number: int) -> Self: ...


class Shelf[M: Keyed, L: Shelved, B](Protocol):
    """A kind of library, for models M, of libraries L, of presets of bands B: its file's
    read, change and path, the bands of a page's reply in its format (None for none it
    takes), the presets of an equalizer by number and name, the one it plays, the daemon's
    request that picks one, and a library with a new preset."""

    def load(self, model: M) -> L: ...

    def edit(self, change: Callable[[L], L], model: M) -> L: ...

    def path(self, model: M) -> Path: ...

    def bands(self, model: M, instance: str, read: object) -> B | None: ...

    def of(self, library: L, instance: str) -> list[tuple[int, str]]: ...

    def playing(self, library: L, instance: str, bands: B) -> int | None: ...

    def request(self, library: L, number: int) -> dict: ...

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
    def request(library: a50x_library.Library, number: int) -> dict:
        return library.get(number).request()

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
        return [(number, preset.name) for number, preset in zip(library.numbers,
                                                                library.presets, strict=True)]

    @staticmethod
    def playing(library: g432_library.Library, _instance: str,
                bands: tuple[g432_library.Band, ...]) -> int | None:
        return library.playing(bands)

    @staticmethod
    def request(library: g432_library.Library, number: int) -> dict:
        return library.get(number).request()

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


class LibraryCard[M: Keyed, L: Shelved, B]:
    """The library of the model shown, of the kind `shelf` keeps, and each equalizer's rows
    for it: `changed` is the page's, which lists it again in its Preset rows once a read or
    a change lands. `model` is the one the page's last reply named, None before one and for
    a model this install does not know, and `library` its file's last read, None before the
    first and while it does not read."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 changed: Callable[[], None], shelf: Shelf[M, L, B]) -> None:
        self._worker = worker
        self._toast = toast
        self._changed = changed
        self._shelf = shelf
        self.model: M | None = None
        self.library: L | None = None
        #: Why the file takes no change: it does not read, or was read in part.
        self.refused: str | None = None
        #: A change is out, which the window's close waits for; a read is out.
        self.busy = self._reading = False
        self._texts = EntryTexts()
        #: Each equalizer's holder of its rows and its rows, by its socket word, what its
        #: Preset row says it plays, the bands it played at the page's last reply, None while
        #: they are not read, and whether that reply let its writes through, False once the
        #: page's headset is gone (writable).
        self._holders: dict[str, Holder] = {}
        self._shown: dict[str, Callable[[], str]] = {}
        self.name_rows: dict[str, Adw.EntryRow] = {}
        self.save_rows: dict[str, Adw.ButtonRow] = {}
        self.delete_rows: dict[str, Adw.ActionRow] = {}
        self.delete_buttons: dict[str, Gtk.Button] = {}
        self._bands: dict[str, B | None] = {}
        self._writable: dict[str, bool] = {}

    def add_rows(self, instance: str, holder: Holder, shown: Callable[[], str]) -> None:
        """`instance`'s rows, Name in `holder` now and the rest after its bands (to_end), each
        hidden until a reply gives bands; `shown` gives what its Preset row says it plays, the
        name Save as new starts from."""
        self._holders[instance], self._shown[instance] = holder, shown
        self._bands[instance], self._writable[instance] = None, False
        name = self.name_rows[instance] = Adw.EntryRow(title=NAME, show_apply_button=True)
        name.connect("apply", self.on_name, instance)
        watch_name(name)
        _add(holder, name)
        self.save_rows[instance] = save_new_row(SAVE_NEW_HELP, self.on_save_new, instance)
        self.delete_rows[instance], self.delete_buttons[instance] = delete_preset_row(
            DELETE_HELP, self.on_delete, instance)
        self.to_end(instance)
        # Off until a reply gives bands: a page shows its rows before its first read.
        self._show_rows(instance)

    def to_end(self, instance: str) -> None:
        """Save as new and Delete preset after every row `instance`'s holder holds now: the
        page adds band rows as its replies give them."""
        holder = self._holders[instance]
        for row in (self.save_rows[instance], self.delete_rows[instance]):
            if row.get_parent() is not None:
                holder.remove(row)
            _add(holder, row)

    # -- what the page reads -------------------------------------------------

    def of(self, instance: str) -> list[tuple[int, str]]:
        """The library's presets of `instance`, each its number and name; none before a
        read."""
        return [] if self.library is None else self._shelf.of(self.library, instance)

    def playing(self, instance: str) -> int | None:
        """The number of the library's preset `instance` plays, by the bands of the page's
        last reply; None for none."""
        bands = self._bands.get(instance)
        return None if bands is None or self.library is None else (
            self._shelf.playing(self.library, instance, bands))

    def request(self, number: int) -> dict:
        """The daemon's request that picks the library's preset `number`, one of those `of`
        gave."""
        return self._shelf.request(cast("L", self.library), number)

    def said(self) -> str | None:
        """Why the file takes no change, as markup for the description of the page's
        equalizer group, where it stays while it lasts: the sentence quotes the file's path, a
        lone surrogate in it as U+FFFD, and its cause. None while the file takes one."""
        return None if self.refused is None else GLib.markup_escape_text(
            displayable(self.refused))

    # -- what the page shows -------------------------------------------------

    def model_shown(self, model: M | None) -> None:
        """The library of `model`, the one the page's reply names, read once it is another
        model's than the one shown; none for None. The G522's two ids are one model."""
        if (None if model is None else model.key) == (
                None if self.model is None else self.model.key):
            return
        self.model, self.library, self.refused = model, None, None
        for instance in self._holders:
            self._show_rows(instance)
        self.refresh()

    def show(self, instance: str, bands: object, writable: bool) -> None:
        """`instance`'s rows for the bands of the page's reply, a list of the reply's band
        objects or anything else for none read, while its writes are let through or not: the
        bands in the format of the model shown (the shelf's `bands`), none for a model this
        install does not know."""
        self._bands[instance] = (None if self.model is None
                                 else self._shelf.bands(self.model, instance, bands))
        self.writable(instance, writable)

    def writable(self, instance: str, writable: bool) -> None:
        """`instance`'s rows on or off as its writes are let through or not, for the bands of
        the page's last reply: off too once the page's headset is gone."""
        self._writable[instance] = writable
        self._show_rows(instance)

    def _show_rows(self, instance: str) -> None:
        """Save as new for bands a preset takes, and Name and Delete preset while a
        preset of the library plays, each on while its writes are let through, no change is
        out and the file takes a change; Save as new also needs the library read and room in it."""
        number = self.playing(instance)
        name, delete = self.name_rows[instance], self.delete_rows[instance]
        name.set_visible(number is not None)
        delete.set_visible(number is not None)
        if number is not None and not self._typing(name):
            self._texts.show(name, self._name(number))
        changeable = self._writable[instance] and self.refused is None and not self.busy
        name.set_sensitive(changeable)
        delete.set_sensitive(changeable)
        save, read = self.save_rows[instance], self._bands[instance] is not None
        save.set_visible(read)
        # Grayed out with the library full, the reason its tooltip, as on the other pages.
        offer_save_new(save, changeable and read and self.library is not None,
                       None if self.library is None else self.library.presets, SAVE_NEW_HELP)

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
        self._landed(model, result, NOT_LOADED)

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
        else:
            self._say_failure(model, result, failed)
            if isinstance(result, eq_presets.LibraryPartial):
                self.refused = str(result)
        for instance in self._holders:
            self._show_rows(instance)
        self._changed()

    def _say_failure(self, model: M, error: object, failed: str) -> None:
        """A change to `model`'s library that failed: a save's error with the file's path, or
        a refusal in words, `failed` the toast's line when it is too long."""
        if isinstance(error, OSError):
            toast_not_saved(self._toast, self._shelf.path(model), error)
        else:
            toast_note(self._toast, capitalized(str(error)), failed, one_sentence=True)

    # -- changing --------------------------------------------------------------

    def on_save_new(self, row: Adw.ButtonRow, instance: str) -> None:
        """Ask the new preset's name, what the equalizer's Preset row says it plays to start,
        then save the bands it plays at the click under it, in the library of the model shown
        then. The row is on only with its bands and a library read (_show_rows)."""
        bands, model = cast("B", self._bands[instance]), cast("M", self.model)
        ask_name(row, self._shown[instance](),
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
        self._texts.took(row)
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
        for instance in self._holders:
            self._show_rows(instance)
        self._worker.run(partial(self._shelf.edit, change, model),
                         lambda result: self._acted(model, result, failed))

    def _acted(self, model: M, result: Result, failed: str) -> None:
        self.busy = False
        # Said whichever model shows when it lands, and for a file that does not read:
        # _landed takes only the one shown's, and holds that refusal for the page's group
        # (said), which a read's says without a toast.
        if isinstance(result, Exception) and (not self._shows(model) or isinstance(
                result, eq_presets.LibraryUnread)):
            self._say_failure(model, result, failed)
        self._landed(model, result, failed)
