"""The Headset tab's stack: the A50's page and each `add` puts in, by what `status` says is
attached; the one Spatial sound group in the box of the page that shows; and the polls of
each page attached (`HeadsetStack.poll`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from gi.repository import GObject, Gtk

from ..astro import NAME as A50_NAME
from .style import HEADSET_TAB, gray_switched_off, switched_off_title, top_switcher


@dataclass
class AddedPage:
    """A page of HeadsetStack's other than the A50's: the names `status` gives its headsets,
    its read of what the daemon holds (`refresh`), run as `status` first names one and at a
    poll while the page is not the one showing on the tab, its own poll while it is
    (`tick`), and whether the last status named one."""

    names: frozenset[str]
    refresh: Callable[[], None]
    tick: Callable[[], None]
    there: bool = False


class HeadsetStack:
    """The Headset tab: the A50's page or one `add` put in, each titled by the model attached,
    by what `status` says is attached, with a switcher between those attached while more than
    one is, on which one `status` gives as not answering is switched off. The A50's shows while
    none is. `poll` runs the reads of each page attached
    (AddedPage). `a50_box` is the A50's page's box, and `spatial`, the one Spatial sound
    group, goes last in the box of the page that shows: a widget has one parent."""

    def __init__(self, a50: Gtk.Widget, a50_box: Gtk.Box, spatial: Gtk.Widget) -> None:
        self._spatial = spatial
        #: Each page's box, by its name in the stack.
        self._boxes = {"a50": a50_box}
        a50_box.append(spatial)
        #: The pages other than the A50's, in the order `add` put them in, by name.
        self._added: dict[str, AddedPage] = {}
        self.stack = Gtk.Stack()
        self.stack.add_titled(a50, "a50", A50_NAME)
        # Ahead of the window's own handler (Polls.on_page), which reads the group on the
        # page that came up.
        self.stack.connect("notify::visible-child-name", self._place)
        self.switcher = top_switcher(self.stack)
        # Its name for a screen reader, the tab's noun, as the Keyboard and Mouse tabs' are;
        # each button is named by its headset.
        self.switcher.update_property([Gtk.AccessibleProperty.LABEL], [HEADSET_TAB])
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.widget.append(self.switcher)
        self.widget.append(self.stack)

    def add(self, name: str, title: str, widget: Gtk.Widget, box: Gtk.Box,
            names: frozenset[str], refresh: Callable[[], None],
            tick: Callable[[], None]) -> None:
        """Another headset's page: `names` are its models' names in `status`'s headsets,
        `box` where the Spatial sound group goes while it shows, and `refresh` and `tick` its
        reads (AddedPage). Hidden until `status` first names one, and titled then by the
        model named."""
        self.stack.add_titled(widget, name, title).set_visible(False)
        self._boxes[name] = box
        self._added[name] = AddedPage(names, refresh, tick)

    def take(self, status: dict) -> None:
        names = status.get("headsets") or ()
        silent = status.get("headsets_silent") or ()
        came: list[Callable[[], None]] = []
        there = {"a50": status.get("headset") is True}
        titles = {"a50": A50_NAME}
        # Before the flip, whose read (Polls.on_page) asks a page only while its headset is
        # attached.
        for name, page in self._added.items():
            model = next((model for model in names if model in page.names), None)
            if model is not None:
                # By the model: a page serves several, and the title it was added with
                # names one of them or none.
                titles[name] = model
                if not page.there:
                    came.append(page.refresh)
            page.there = there[name] = model is not None
        # A headset that does not answer is switched off on the switcher, as a keyboard or a
        # mouse is on theirs.
        for name, title in titles.items():
            self._page(name).set_title(switched_off_title(title, title in silent))
        gray_switched_off(self.switcher, {index for index, name in enumerate(self._boxes)
                                          if titles.get(name) in silent})
        shown = [name for name, here in there.items() if here] or ["a50"]
        # Hidden after the flip: hiding the page that shows flips the stack to its first page,
        # the A50's even while that page is hidden, and each flip reads (Polls.on_page).
        for name in shown:
            self._page(name).set_visible(True)
        if self.stack.get_visible_child_name() not in shown:
            self.stack.set_visible_child_name(shown[0])
        for name in there.keys() - set(shown):
            self._page(name).set_visible(False)
        self.switcher.set_visible(len(shown) > 1)
        for told in came:
            told()

    def _page(self, name: str) -> Gtk.StackPage:
        return self.stack.get_page(cast(Gtk.Widget, self.stack.get_child_by_name(name)))

    def poll(self, on_tab: bool) -> None:
        """Read each page the last status named a headset of: the one showing, while the tab
        is up (`on_tab`), by its own poll (`tick`), which may run systemctl and pw-dump;
        every other by the daemon's answer from what it holds (`refresh`), which sends the
        headset nothing."""
        showing = self.stack.get_visible_child_name()
        for name, page in self._added.items():
            if page.there:
                (page.tick if on_tab and name == showing else page.refresh)()

    def _place(self, _stack: Gtk.Stack, _pspec: GObject.ParamSpec) -> None:
        cast(Gtk.Box, self._spatial.get_parent()).remove(self._spatial)
        self._boxes[cast(str, self.stack.get_visible_child_name())].append(self._spatial)
