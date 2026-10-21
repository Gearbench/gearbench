"""Import from G HUB: a file chooser, a list of the profiles found, and a
review page that asks about names Gearbench already has.

Nothing is written until Import on the review page. Then the daemon writes
each file, one command at a time on the window's worker, as the CLI's
`import-ghub` does; `gearbench.ghub` holds the reading and mapping for both,
`gearbench.ghub_plan` the planning, and `gearbench.import_plan` the plan and
its requests.

The list suits picking two of ten or all but two of a hundred: a filter,
Select all and Select none acting on what the filter shows, a check box per
profile and a running count. The desktop's default profile is apart and
never checked by Select all: it replaces the M profiles instead of adding a
game profile.

A profile G HUB names programs for loads by hand unless its Load for check
box is checked (the owner's decision of 2026-09-28): a name from someone
else's file also matches the Linux program of that name. One that replaces
yours keeps your programs, which it would otherwise stop loading for.

`Importer` is the half the pack's dialog (`pack_dialog`) shares: the filter
and its buttons, the lists, what the check boxes do and the count, the Load
for row, the review page and the import. Both dialogs open through
`read_then_show`, one read at a time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .. import ghub, ghub_plan, import_plan
from ..wording import counted, word_list
from .style import held_while_shown, picked_path, plain_row, weakly
from .worker import Reply, unreachable

if TYPE_CHECKING:                       # pragma: no cover
    from ..macros import Macro
    from ..profiles import GameProfile
    from .window import Window

TITLE = "Import from G HUB"
FILTER_HINT = "Filter by name or program"
NO_GAMES = "No game profiles in this file"
NO_MATCH = "No game profile matches the filter"
#: The kind of the G HUB file's profiles in the dialog's pickers, lists and check boxes.
GAMES = "game"
#: The failure dialog's heading when some writes were done, and when none was.
FAILED = "Part of the import did not work"
NOTHING_IMPORTED = "The import did not work"
#: The longest a request may take. A macro or profile write is one file.
REQUEST_TIMEOUT = 10.0


def search_text(item: GameProfile | Macro) -> str:
    """What the filter matches for a game profile or a macro: its name, and a game
    profile's programs."""
    return " ".join((item.name, *getattr(item, "processes", ()))).casefold()


def selected_count(ticked: int, total: int) -> str:
    """The first page's running count: "3 of 10 selected"."""
    return f"{ticked} of {total} selected"


class Picker:
    """Which items are checked and which the filter shows; no widgets, so it
    is tested without a display.

    Select all and Select none act only on what the filter shows.
    `with_programs` holds the profiles to load for their programs, empty at
    first and untouched by the buttons. `text` gives what the filter matches
    for each, already casefolded, since only the query is casefolded here.
    """

    def __init__(self, profiles: Sequence[Any], text: Callable[[Any], str]) -> None:
        self.names = [p.name for p in profiles]
        self._text = {p.name: text(p) for p in profiles}
        self.ticked: set[str] = set()
        self.with_programs: set[str] = set()
        self.query = ""

    def shown(self, name: str) -> bool:
        return self.query.casefold() in self._text[name]

    def visible(self) -> list[str]:
        return [n for n in self.names if self.shown(n)]

    def select_all(self) -> None:
        self.ticked |= set(self.visible())

    def select_none(self) -> None:
        self.ticked -= set(self.visible())

    def set(self, name: str, on: bool) -> None:
        if on:
            self.ticked.add(name)
        else:
            self.ticked.discard(name)

    def set_programs(self, name: str, on: bool) -> None:
        if on:
            self.with_programs.add(name)
        else:
            self.with_programs.discard(name)

    def chosen(self) -> list[str]:
        """The checked items, in the order given."""
        return [n for n in self.names if n in self.ticked]


def load(path: Path) -> tuple[ghub.Found, import_plan.Existing]:
    """Read the file and what Gearbench has. On the worker: a real settings
    document is over a megabyte, and the libraries parse every file."""
    return ghub.parse(ghub.read_settings(path), path.name), import_plan.existing_from_disk()


def choose_file(window: Window) -> None:
    """Ask for the file, then read it and show what it holds."""
    dialog = Gtk.FileDialog(title=TITLE, modal=True)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    settings = Gtk.FileFilter(name="G HUB settings")
    for pattern in ("*.db", "*.json"):
        settings.add_pattern(pattern)
    filters.append(settings)
    anything = Gtk.FileFilter(name="All files")
    anything.add_pattern("*")
    filters.append(anything)
    dialog.set_filters(filters)
    dialog.open(window, None, lambda d, result: picked(window, d, result))


def picked(window: Window, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
    if (path := picked_path(dialog, result, window.toast)) is not None:
        open_path(window, path)


def read_then_show[T](window: Window, work: Callable[[], T],
                      show: Callable[[T], None]) -> None:
    """Run `work` on the worker, then `show` what it read. One read at a time, the G HUB
    import's and the pack's together: a second dialog would hold an Existing from before
    the first one's import, and the daemon would refuse its writes. The flag goes when the
    read lands: no click reaches the window's menu or the Game profiles dialog under a
    presented dialog."""
    if getattr(window, "import_reading", False):
        return
    window.import_reading = True

    def done(result: T | Exception) -> None:
        window.import_reading = False
        if isinstance(result, Exception):
            window.refused({"error": str(result)})
            return
        show(result)
    window.worker.run(work, done)


def open_path(window: Window, path: Path) -> None:
    """Read `path` on the worker, then show the import dialog for it (read_then_show)."""
    def show(result: tuple[ghub.Found, import_plan.Existing]) -> None:
        found, existing = result
        # Gio hands a name that is not UTF-8 back with surrogates
        # (surrogateescape), which no GTK string takes.
        ImportDialog(window, found, existing,
                     GLib.filename_display_name(path.name)).present(window)
    read_then_show(window, lambda: load(path), show)


class Importer(Adw.Dialog):
    """What the G HUB import and the pack share: the first page's filter, Select
    all, Select none, check box handler and count, the review page and the
    import. A subclass puts a Picker in `pickers` for each kind it lists, then
    builds the first page: `_controls` makes its top, `_list` a kind's list and
    `_load_for_row` a profile's Load for row, and `next` is its button. Each
    Import check box goes in `checks`, connected to `_on_check` when its item is
    a picker's. The subclass answers `make_plan` and `plan_title`."""

    #: The clash choices, as the drop-down names them and in its order. The
    #: first is the default (default_choice), and so writes nothing over
    #: anything of the user's.
    CLASHES: ClassVar[dict[str, str]] = {import_plan.KEEP: "Keep both",
                                         import_plan.REPLACE: "Replace",
                                         import_plan.SKIP: "Skip"}

    def __init__(self, window: Window, existing: import_plan.Existing, title: str) -> None:
        super().__init__(title=title, content_width=600, content_height=680)
        self.win = window
        self.existing = existing
        #: The clash answers, by (kind, imported name).
        self.choices: dict[tuple[str, str], str] = {}
        #: The first page's pickers and lists, by kind.
        self.pickers: dict[str, Picker] = {}
        self.lists: dict[str, Gtk.ListBox] = {}
        #: The Import check boxes, by (kind, name).
        self.checks: dict[tuple[str, str], Gtk.CheckButton] = {}
        #: Set while the rows sync to the pickers, so the check boxes' handler does not echo it.
        self._syncing = False
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        self.next = Gtk.Button(label="Next", css_classes=["suggested-action", "pill"],
                               halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        self.next.connect("clicked", weakly(self._on_next))

    @property
    def default_choice(self) -> str:
        """The answer a clash has until one is picked: the first of CLASHES."""
        return next(iter(self.CLASHES))

    # Stricter than Adw.Dialog.present, whose parent is optional: this dialog needs its window.
    def present(self, parent: Gtk.Widget) -> None:  # type: ignore[override]
        """Show it, held until it closes: its callers keep nothing of it, and every handler
        that reaches it is weakly's (held_while_shown)."""
        held_while_shown(self)
        super().present(parent)

    def _controls(self, body: Gtk.Box) -> None:
        """The filter, then Select all, Select none and the count, into `body`."""
        self.search = Gtk.SearchEntry(placeholder_text=FILTER_HINT, hexpand=True)
        # The placeholder is no name to a screen reader.
        self.search.update_property([Gtk.AccessibleProperty.LABEL], [FILTER_HINT])
        # Through weakly, as every handler on a widget inside the dialog.
        self.search.connect("search-changed", weakly(self._on_filter))
        select_all = Gtk.Button(label="Select all")
        select_all.connect("clicked", weakly(self._on_select), True)
        select_none = Gtk.Button(label="Select none")
        select_none.connect("clicked", weakly(self._on_select), False)
        self.count = Gtk.Label(xalign=1, hexpand=True, css_classes=["dimmed", "numeric"])
        buttons = Gtk.Box(spacing=6)
        for widget in (select_all, select_none, self.count):
            buttons.append(widget)
        body.append(self.search)
        body.append(buttons)

    def _list(self, kind: str, placeholder: str) -> Gtk.ListBox:
        """The first page's list of `kind`, showing what its picker's filter matches."""
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        listbox.set_placeholder(Gtk.Label(label=placeholder, margin_top=12, margin_bottom=12,
                                          css_classes=["dimmed"]))
        listbox.set_filter_func(lambda row, p=self.pickers[kind]: p.shown(row.get_name()))
        self.lists[kind] = listbox
        return listbox

    def _load_for_row(self, kind: str, profile: GameProfile | ghub.GhubProfile, subtitle: str,
                      toggled: Callable[..., object], *args: object) -> Adw.ActionRow:
        """A profile's Load for row. Its check box is ticked when the picker of `kind`
        loads the profile for its programs, and calls `toggled` with `args` when it
        changes."""
        title = f"Load for {word_list(profile.processes)}"
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER,
                                active=profile.name in self.pickers[kind].with_programs)
        check.update_property([Gtk.AccessibleProperty.LABEL], [title])
        check.connect("toggled", toggled, *args)
        row = plain_row(Adw.ActionRow, title, subtitle, activatable_widget=check)
        row.add_prefix(check)
        return row

    def _on_check(self, check: Gtk.CheckButton, kind: str, name: str) -> None:
        if self._syncing:
            return
        self.pickers[kind].set(name, check.get_active())
        self._update_count()

    def _on_filter(self, entry: Gtk.SearchEntry) -> None:
        for kind, picker in self.pickers.items():
            picker.query = entry.get_text().strip()
            self.lists[kind].invalidate_filter()

    def _on_select(self, _button: Gtk.Button, on: bool) -> None:
        self._syncing = True
        try:
            for kind, picker in self.pickers.items():
                if on:
                    picker.select_all()
                else:
                    picker.select_none()
                for name in picker.names:
                    self.checks[kind, name].set_active(name in picker.ticked)
        finally:
            self._syncing = False
        self._update_count()

    def extra_ticked(self) -> bool:
        """Whether something ticked outside the pickers is enough for Next."""
        return False

    def _update_count(self) -> None:
        """Count what is ticked of every item, shown by the filter or not, and let Next
        go once anything is."""
        ticked = sum(len(p.ticked) for p in self.pickers.values())
        total = sum(len(p.names) for p in self.pickers.values())
        self.count.set_label(selected_count(ticked, total))
        self.next.set_sensitive(bool(ticked) or self.extra_ticked())

    def make_plan(self) -> import_plan.Plan:
        raise NotImplementedError

    def plan_title(self) -> str:
        raise NotImplementedError

    def _page(self, tag: str, title: str, subtitle: str, body: Gtk.Widget,
              button: Gtk.Button) -> Adw.NavigationPage:
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title, subtitle=subtitle))
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=Adw.Clamp(child=body), hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True))
        view.add_top_bar(header)
        view.add_bottom_bar(button)
        return Adw.NavigationPage(child=view, title=title, tag=tag)

    # -- the second page: review and import --------------------------------

    def _on_next(self, _button: Gtk.Button) -> None:
        # The view refuses a second page of the tag, and holds a popped review page until its
        # slide ends. A second click can reach Next before the push's first frame covers it,
        # as when a long review page builds: the answers on the page shown would then update
        # the refused page's widgets.
        if self.nav.find_page("review") is not None:
            return
        self.plan = self.make_plan()
        self.summary = Gtk.Label(wrap=True, xalign=0, css_classes=["title-4"])
        self.written = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self.said = Gtk.Label(wrap=True, xalign=0, css_classes=["dimmed"])
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                       margin_bottom=12, margin_start=12, margin_end=12)
        body.append(self.summary)
        self.clash_group = Adw.PreferencesGroup(title="Already in Gearbench",
                                                description="Choose what to do with each.")
        self.clash_rows: dict[import_plan.Clash, Adw.ComboRow] = {}
        body.append(self.clash_group)
        written = Adw.PreferencesGroup(title="What is written")
        written.add(self.written)
        body.append(written)
        body.append(self.said)
        self.import_button = Gtk.Button(label="Import",
                                        css_classes=["suggested-action", "pill"],
                                        halign=Gtk.Align.CENTER, margin_top=6,
                                        margin_bottom=6)
        self.import_button.connect("clicked", weakly(self._on_import))
        self.nav.push(self._page("review", "Review", self.plan_title(), body,
                                 self.import_button))
        self._show_plan()

    def _clash_row(self, clash: import_plan.Clash) -> Adw.ComboRow:
        subtitle = import_plan.CLASH_WORDS[clash.kind]
        if clash.existing != clash.name:
            subtitle += f', yours is "{clash.existing}"'
        order = list(self.CLASHES)
        row = plain_row(Adw.ComboRow, clash.name, subtitle,
                        model=Gtk.StringList.new(list(self.CLASHES.values())))
        row.set_selected(order.index(self.choices.get((clash.kind, clash.name),
                                                      self.default_choice)))
        row.connect("notify::selected", weakly(self._on_clash), clash)
        return row

    def _on_clash(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec,
                  clash: import_plan.Clash) -> None:
        self.choices[(clash.kind, clash.name)] = list(self.CLASHES)[row.get_selected()]
        self.plan = self.make_plan()
        self._show_plan()

    def _show_plan(self) -> None:
        plan = self.plan
        # An answer can bring a clash or take one away: a profile skipped
        # takes its macros' clashes with it, and they come back with their
        # answers, which are kept in `choices`.
        for clash in plan.clashes:
            if clash not in self.clash_rows:
                self.clash_rows[clash] = self._clash_row(clash)
                self.clash_group.add(self.clash_rows[clash])
        shown = set(plan.clashes)
        for clash, row in self.clash_rows.items():
            row.set_visible(clash in shown)
        self.clash_group.set_visible(bool(plan.clashes))
        self.summary.set_label(import_plan.plan_summary(plan))
        self.written.set_label("\n".join(import_plan.plan_lines(plan)))
        self.said.set_label("\n".join(plan.said))
        self.said.set_visible(bool(plan.said))
        self.import_button.set_sensitive(not plan.empty and not plan.unresolved)

    def _on_import(self, _button: Gtk.Button) -> None:
        """Send the plan, each request after the reply to the one before,
        then say how it went. One at a time, since a profile whose macro
        was refused or got no answer is not sent (`import_plan.held_back`)."""
        requests = import_plan.requests(self.plan)
        # Nothing to go back to or change while the daemon writes.
        self.nav.set_sensitive(False)
        self.set_can_close(False)
        sent: list[tuple[dict, dict]] = []

        def send_next() -> None:
            while len(sent) < len(requests):
                request = requests[len(sent)]
                held = import_plan.held_back(request, sent)
                if held is None:
                    self.win.worker.call(request, done, timeout=REQUEST_TIMEOUT)
                    return
                sent.append((request, held))
            self.finish(sent)

        def done(reply: Reply) -> None:
            if isinstance(reply, Exception):
                # No answer is not a refusal: a request sent to a daemon too
                # busy to answer in time is still read and done (held_back).
                reply = {"ok": False, "error": unreachable(reply), "unanswered": True}
            sent.append((requests[len(sent)], reply))
            send_next()

        send_next()

    def finish(self, sent: list[tuple[dict, dict]]) -> None:
        done, failed = import_plan.reply_lines(sent)
        self.nav.set_sensitive(True)
        self.set_can_close(True)
        self.close()
        if failed:
            self.win.explain(f"Imported {len(done)} of {len(sent)}.", "\n".join(failed),
                             heading=FAILED if done else NOTHING_IMPORTED)
        else:
            self.win.toast(import_plan.plan_summary(self.plan, "Imported"))
        # A macro saved moves the status' `macros` digest, which rereads the Macros tab
        # (Window.take_macros), so the tab is not read here.
        self.win.resync()


class ImportDialog(Importer):
    """The two pages: pick, then review and import."""

    def __init__(self, window: Window, found: ghub.Found, existing: import_plan.Existing,
                 source: str) -> None:
        super().__init__(window, existing, TITLE)
        self.found = found
        self.pickers[GAMES] = Picker(found.games, ghub.GhubProfile.search_text)
        self.desktop_ticked = False
        #: The Notes rows, whose last line says what loads the profile.
        self._notes: dict[str, Adw.ActionRow] = {}
        #: The rows whose details have been built, by profile name.
        self._filled: set[str] = set()

        self.nav.add(self._pick_page(source))
        self._update_count()

    # -- the first page: pick ----------------------------------------------

    def _pick_page(self, source: str) -> Adw.NavigationPage:
        # With game profiles, the list is empty only when the filter hides them all.
        listbox = self._list(GAMES, NO_MATCH if self.found.games else NO_GAMES)
        for profile in self.found.games:
            listbox.append(self._profile_row(profile))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                       margin_bottom=12, margin_start=12, margin_end=12)
        self._controls(body)
        body.append(listbox)
        desktop = self.found.desktop
        if desktop is not None:
            lit = " and the G915's lighting" if desktop.lighting is not None else ""
            group = Adw.PreferencesGroup(
                title="Desktop default",
                description=f"Instead of a game profile, its bindings{lit} replace those "
                            "of your M1, M2 and M3.")
            group.add(self._profile_row(desktop))
            body.append(group)
            # G HUB keeps one per application, but a file can hold more: the list offers
            # the first, and names the rest, as import-ghub --list shows each.
            unoffered = [p.name for p in self.found.profiles if p.desktop][1:]
            if unoffered:
                lead = ("Another desktop default" if len(unoffered) == 1
                        else "Other desktop defaults")
                body.append(Gtk.Label(
                    label=f"{lead} in the file, not offered: {', '.join(unoffered)}. "
                          "gearbench-ctl import-ghub --profile imports one by its name.",
                    wrap=True, xalign=0, css_classes=["dimmed"]))
        if self.found.other_devices:
            body.append(Gtk.Label(
                label="Also in the file, not imported: " + ghub_plan.other_devices_text(self.found),
                wrap=True, xalign=0, css_classes=["dimmed"]))
        return self._page("pick", "Choose profiles", source, body, self.next)

    def _profile_row(self, profile: ghub.GhubProfile) -> Adw.ExpanderRow:
        subtitle = ghub_plan.summary(profile)
        if profile.ghub_name:
            subtitle = f'G HUB calls it "{profile.ghub_name}". {subtitle}'
        # Not markup: a profile's name and G HUB's name for it are anyone's text.
        row = plain_row(Adw.ExpanderRow, profile.name, subtitle, name=profile.name)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        check.update_property([Gtk.AccessibleProperty.LABEL], [f"Import {profile.name}"])
        if profile.desktop:
            check.connect("toggled", weakly(self._on_desktop))
        else:
            check.connect("toggled", weakly(self._on_check), GAMES, profile.name)
        row.add_prefix(check)
        self.checks[GAMES, profile.name] = check
        # Filled on first opening: a hundred profiles are a thousand rows.
        row.connect("notify::expanded", weakly(self._fill_row), profile)
        return row

    def _fill_row(self, row: Adw.ExpanderRow, _pspec: GObject.ParamSpec,
                  profile: ghub.GhubProfile) -> None:
        if profile.name in self._filled or not row.get_expanded():
            return
        self._filled.add(profile.name)
        if profile.processes:
            row.add_row(self._load_for_row(GAMES, profile, "", weakly(self._on_programs), profile))
        for heading, lines in ghub_plan.details(profile, self.found, self.existing.m_names,
                                                profile.name in self.pickers[GAMES].with_programs):
            detail = plain_row(Adw.ActionRow, heading, "\n".join(lines),
                               subtitle_selectable=True)
            if heading == ghub_plan.NOTES:
                self._notes[profile.name] = detail
            row.add_row(detail)

    def _on_programs(self, check: Gtk.CheckButton, profile: ghub.GhubProfile) -> None:
        on = check.get_active()
        self.pickers[GAMES].set_programs(profile.name, on)
        self._notes[profile.name].set_subtitle("\n".join(ghub_plan.notes(profile, on)))

    def _on_desktop(self, check: Gtk.CheckButton) -> None:
        self.desktop_ticked = check.get_active()
        self._update_count()

    def extra_ticked(self) -> bool:
        return self.desktop_ticked

    def chosen(self) -> list[str]:
        # Ticked on the desktop default's own row (_on_desktop), which _pick_page builds only
        # for a file that has one.
        extra = [cast(ghub.GhubProfile, self.found.desktop).name] if self.desktop_ticked else []
        return self.pickers[GAMES].chosen() + extra

    # -- what the review page shows ----------------------------------------

    def make_plan(self) -> import_plan.Plan:
        return ghub_plan.make_plan(self.found, self.chosen(), self.existing, self.choices,
                                   default=self.default_choice,
                                   with_programs=self.pickers[GAMES].with_programs)

    def plan_title(self) -> str:
        return counted(len(self.chosen()), "profile")
