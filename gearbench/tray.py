"""The tray icon's model: what the icon, its tooltip and its menu show, and the
com.canonical.dbusmenu layout the menu becomes.

No gi and no D-Bus here, so every state the menu can be in is tested without a bus.
daemon/tray.py takes a `Snapshot`, `build` turns it into a `Model`, and `TrayMenu` keeps the
model shown and answers sni.py's questions about it.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .apps import PATH_MAX
from .battery import (
    BATTERY_WARNINGS,
    FULL,
    ON_BATTERY,
    UNKNOWN,
    amount_words,
    icon_name,
)

#: Where sni.py exports the item and its menu: KDE's paths. The item is registered
#: by ITEM_PATH, and its Menu property names MENU_PATH.
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

ACTIVE, NEEDS_ATTENTION = "Active", "NeedsAttention"
#: The app's symbolic icon, which install.sh and the package put in the hicolor theme
#: (desktop/icons/symbolic/).
ICON = "io.github.gearbench.Gearbench-symbolic"
PROBLEM_ICON = "dialog-warning-symbolic"
LOW_BATTERY_ICON = "battery-caution-symbolic"
#: Below this on battery the icon asks for attention: the lower of the two low-battery
#: notices, compared as they compare, by the whole percent shown: 14.6 % shows as 15 % and is
#: not low.
LOW_BATTERY = min(BATTERY_WARNINGS)
#: The longest problem line in the menu. GNOME's menu neither wraps nor has a
#: maximum width, and a problem can run to hundreds of characters, wider than a screen.
PROBLEM_MAX = 80
#: Where a problem's message may be cut: after a clause, or before an aside.
CLAUSE_END = re.compile(r"[,;:.] | \(")

#: The key of the item that opens the window; it sends no request.
OPEN = "open"

#: battery.py's states that say nothing beyond the percentage.
PLAIN = (None, ON_BATTERY, UNKNOWN)

#: The StatusNotifierItem's properties and their D-Bus types.
SNI_TYPES = {
    "Category": "s", "Id": "s", "Title": "s", "Status": "s", "WindowId": "i",
    "IconThemePath": "s", "Menu": "o", "ItemIsMenu": "b", "IconName": "s",
    "IconPixmap": "a(iiay)", "OverlayIconName": "s", "OverlayIconPixmap": "a(iiay)",
    "AttentionIconName": "s", "AttentionIconPixmap": "a(iiay)", "AttentionMovieName": "s",
    "ToolTip": "(sa(iiay)ss)",
}
#: A menu item's properties and their D-Bus types. One left out has the
#: dbusmenu default: a visible, enabled, standard item with no label.
ITEM_TYPES = {
    "type": "s", "label": "s", "enabled": "b", "icon-name": "s",
    "toggle-type": "s", "toggle-state": "i", "children-display": "s",
}


@dataclass(frozen=True)
class Battery:
    """One device's battery as the daemon last knew it."""

    key: str
    name: str
    percent: float | None
    state: str | None
    #: Power going in or on external power: not low, however low the number.
    plugged: bool
    approximate: bool
    answering: bool
    #: A keyboard on its cable with no reading, which is charging or full.
    cabled: bool
    #: battery.icon_name's for the whole reading, the icon the window's Battery row shows
    #: too, but for a keyboard on its cable with no reading: the adapter here, a missing
    #: battery there.
    icon: str

    @classmethod
    def of(cls, key: str, name: str, reading: dict | None, *, answering: bool = True,
           cabled: bool = False) -> Battery:
        """From a battery.read() reading, or None when there is none."""
        reading = reading or {}
        return cls(key, name, percent=reading.get("percent"), state=reading.get("state"),
                   plugged=bool(reading.get("charging") or reading.get("external_power")),
                   approximate=bool(reading.get("approximate")),
                   answering=answering, cabled=cabled, icon=icon_name(reading, cabled))

    @property
    def low(self) -> bool:
        return (self.percent is not None and round(self.percent) < LOW_BATTERY
                and not self.plugged)

    def words(self) -> str:
        """`G915: 80%, charging`, one line of the menu and the tooltip."""
        amount = (amount_words(self.percent, self.approximate)
                  if self.percent is not None else None)
        if self.state == FULL:
            said = FULL
        elif amount is not None:
            said = amount if self.state in PLAIN else f"{amount}, {self.state}"
        elif self.state not in PLAIN:
            said = self.state
        elif self.cabled:
            said = "on its cable"
        else:
            said = "battery not read yet" if self.answering else "not answering"
            return f"{self.name}: {said}"
        return f"{self.name}: {said}" + ("" if self.answering else ", not answering")


@dataclass(frozen=True)
class Stages:
    """The selected mouse's DPI stages in force and the DPI it is known to be on."""

    device: str
    stages: tuple[int, ...]
    dpi: int | None


@dataclass(frozen=True)
class Snapshot:
    """What the daemon knows that the tray shows."""

    batteries: tuple[Battery, ...] = ()
    problems: tuple[str, ...] = ()
    #: None with no mouse selected, or one with no DPI stages or not answering.
    stages: Stages | None = None
    profiles: tuple[str, ...] = ()
    loaded: str | None = None
    #: Each M profile's number and label while a keyboard that switches them here is connected
    #: (keyboard.m_profiles_in_menu), empty otherwise. `active` is the M profile in force.
    m_profiles: tuple[tuple[int, str], ...] = ()
    active: int = 1
    keep_loaded: bool = False
    lock_lights: bool = True
    dpi_notices: bool = True
    #: None while no mouse with lighting is attached.
    mouse_sync: bool | None = None


@dataclass(frozen=True)
class Item:
    """One menu item. `key` names it across refreshes, so it keeps its id."""

    key: str
    label: str = ""
    icon: str = ""
    separator: bool = False
    #: "checkmark" or "radio", with `on` its state.
    toggle: str = ""
    on: bool = False
    enabled: bool = True
    children: tuple[Item, ...] = ()
    #: What choosing it sends to the daemon, as (field, value) pairs; None
    #: for nothing, as for the game profile choice already in force.
    request: tuple[tuple[str, object], ...] | None = None

    def props(self) -> dict[str, object]:
        """Its dbusmenu properties: a separator's type, or the label and the
        others not at their default."""
        if self.separator:
            return {"type": "separator"}
        # Only the first: Ubuntu's extension unescapes once, dropping the first underscore
        # before another character, and shows any other escaped one doubled. A host that
        # follows the dbusmenu spec takes a later lone one as a mnemonic.
        found: dict[str, object] = {"label": re.sub(r"_(?=[^_])", "__", self.label, count=1)}
        if self.icon:
            found["icon-name"] = self.icon
        if not self.enabled:
            found["enabled"] = False
        if self.toggle:
            found["toggle-type"] = self.toggle
            found["toggle-state"] = int(self.on)
        if self.children:
            found["children-display"] = "submenu"
        return found


@dataclass(frozen=True)
class Model:
    title: str
    status: str
    attention_icon: str
    tooltip: tuple[str, ...]
    #: The root item, key "", whose children are the menu.
    menu: Item

    def sni(self) -> dict[str, object]:
        """The StatusNotifierItem's properties, by SNI_TYPES."""
        # The description is markup to the spec, so it is escaped.
        description = html.escape("\n".join(self.tooltip), quote=False)
        return {
            "Category": "Hardware", "Id": "gearbench", "Title": self.title,
            "Status": self.status, "WindowId": 0, "IconThemePath": "", "Menu": MENU_PATH,
            "ItemIsMenu": True, "IconName": ICON, "IconPixmap": [],
            "OverlayIconName": "", "OverlayIconPixmap": [],
            "AttentionIconName": self.attention_icon, "AttentionIconPixmap": [],
            "AttentionMovieName": "", "ToolTip": ("", [], self.title, description),
        }


def request(**fields: object) -> tuple[tuple[str, object], ...]:
    return tuple(fields.items())


def problem_line(message: str) -> str:
    """A problem's message cut for its menu line, in the daemon's case (problem_items
    capitalizes it): whole up to PROBLEM_MAX characters. A longer
    one gets the home directory as ~, then is cut at the last clause end that fits, else at a
    space, and ends in an ellipsis. A clause end inside a file's name or an aside is not cut
    at, nor one after a colon: the name says which file, and the others explain what comes
    before. Names and asides are looked for in the first PROBLEM_MAX + PATH_MAX characters,
    as far as a path that starts in the line can run. A message says first what is wrong;
    the tooltip and `gearbench-ctl status` give it whole."""
    if len(message) <= PROBLEM_MAX:
        return message
    message = message.replace(f"{Path.home()}/", "~/")
    if len(message) <= PROBLEM_MAX:
        return message
    # A .json file's path first: its name may hold a clause end or a lone
    # parenthesis, which the aside mask would pair with a later one. A file's error can
    # quote a megabyte of it, and each mask's work grows as the square of what it reads.
    head = message[:PROBLEM_MAX + PATH_MAX]
    plain = re.sub(r"(?<!\S)[~/]\S.*?\.json\b", lambda path: "x" * len(path[0]), head)
    plain = re.sub(r"\(.*?\)", lambda aside: "(" + "x" * (len(aside[0]) - 1), plain)
    ends = []
    for end in CLAUSE_END.finditer(plain, 0, PROBLEM_MAX + 1):
        ends.append(end.start())
        if end[0] == ": ":
            break
    cut = ends[-1] if ends else message.rfind(" ", 0, PROBLEM_MAX)
    return message[:cut if cut > 0 else PROBLEM_MAX - 1] + "…"


def problem_items(problems: Sequence[str]) -> tuple[Item, ...]:
    """A line per problem, keyed by place so one that goes or changes is a
    new label under the same id. It starts with a capital, as the menu's other
    labels do and the window's device tabs show a problem."""
    lines = (problem_line(message) for message in problems)
    return tuple(Item(f"problem:{i}", line[:1].upper() + line[1:], PROBLEM_ICON, enabled=False)
                 for i, line in enumerate(lines))


def dpi_items(stages: Stages) -> tuple[Item, ...]:
    """A radio item per stage, the first at the known DPI marked. A marked
    stage is sent again when chosen: the G305's own DPI button moves it
    where the daemon cannot see."""
    marked = stages.stages.index(stages.dpi) if stages.dpi in stages.stages else None
    return tuple(Item(f"dpi:{i}", f"{dpi} DPI", toggle="radio", on=i == marked,
                      request=request(command="mouse_dpi", value=dpi, device=stages.device))
                 for i, dpi in enumerate(stages.stages))


def switch(label: str, on: bool, *, command: str) -> Item:
    """A checkmark item, keyed by its command, that sends the other state."""
    return Item(command, label, toggle="checkmark", on=on,
                request=request(command=command, value=not on))


def profile_menu(snap: Snapshot) -> Item:
    """The Game profile submenu: None, each profile, Keep loaded."""
    names = snap.profiles + ((snap.loaded,) if snap.loaded and snap.loaded not in snap.profiles
                             else ())
    choices = [Item("unload", "None", toggle="radio", on=snap.loaded is None,
                    request=None if snap.loaded is None else request(command="unload"))]
    choices += [Item(f"profile:{name}", name, toggle="radio", on=name == snap.loaded,
                     request=None if name == snap.loaded else request(command="load", value=name))
                for name in names]
    keep = switch("Keep loaded", snap.keep_loaded, command="keep_loaded")
    return Item("profiles", "Game profile",
                children=(*choices, Item("separator:keep_loaded", separator=True), keep))


def m_profile_menu(snap: Snapshot) -> Item:
    """The M profile submenu: each M profile, the one in force marked. Choosing another
    switches to it, as its M key does on a keyboard that has one."""
    choices = tuple(
        Item(f"m_profile:{number}", label, toggle="radio", on=number == snap.active,
             request=None if number == snap.active else request(command="profile", value=number))
        for number, label in snap.m_profiles)
    return Item("m_profiles", "M profile", children=choices)


def switch_items(snap: Snapshot) -> tuple[Item, ...]:
    found = [switch("Lights off when locked", snap.lock_lights, command="lock_lights"),
             switch("DPI notices", snap.dpi_notices, command="dpi_notices")]
    if snap.mouse_sync is not None:
        found.append(switch("Sync mouse with keyboard", snap.mouse_sync, command="mouse_sync"))
    return tuple(found)


def build(snap: Snapshot, title: str) -> Model:
    """The icon, tooltip and menu for one snapshot."""
    low = any(battery.low for battery in snap.batteries)
    groups = (
        # The AppIndicators extension shows no tooltip, so the menu is where they can be read.
        ("problems", problem_items(snap.problems)),
        ("batteries", tuple(Item(f"battery:{b.key}", b.words(), b.icon, enabled=False)
                            for b in snap.batteries)),
        (OPEN, (Item(OPEN, f"Open {title}"),)),
        ("dpi", dpi_items(snap.stages) if snap.stages is not None else ()),
        # Without a game profile, None would be the submenu's only choice.
        ("profiles", ((m_profile_menu(snap),) if snap.m_profiles else ())
         + ((profile_menu(snap),) if snap.profiles or snap.loaded else ())),
        ("switches", switch_items(snap)),
    )
    items: list[Item] = []
    for name, group in groups:
        if items and group:
            items.append(Item(f"separator:{name}", separator=True))
        items.extend(group)
    return Model(
        title=title,
        status=NEEDS_ATTENTION if snap.problems or low else ACTIVE,
        attention_icon=LOW_BATTERY_ICON if low and not snap.problems else PROBLEM_ICON,
        tooltip=tuple(b.words() for b in snap.batteries) + snap.problems,
        menu=Item("", children=tuple(items)),
    )


#: A menu layout as sni.py sends it: (id, properties, children).
Node = tuple[int, dict[str, object], list["Node"]]


@dataclass
class Changes:
    """What one refresh changed, so only those signals go out. The icon is always
    ICON, and the daemon builds every model with the title APP_NAME, so no NewIcon
    or NewTitle goes out; a model whose title changes needs NewTitle back."""

    status: bool = False
    attention: bool = False
    tooltip: bool = False
    #: Items came or went: the host reads the layout again. Ubuntu's
    #: AppIndicators extension reads only `type` and `children-display` there,
    #: and all of a new item's properties, but keeps the rest of an item it
    #: has (dbusMenu.js's _doLayoutUpdate, gnome-shell-ubuntu-extensions
    #: 50.26.04.8).
    layout: bool = False
    #: The properties that changed on items in the last model, all of those of
    #: an item back since an earlier one, and those now at their default,
    #: whether or not the layout changed too.
    items: list[tuple[int, dict[str, object]]] = field(default_factory=list)
    removed: list[tuple[int, list[str]]] = field(default_factory=list)


class TrayMenu:
    """The model the desktop shows, and what its menu does.

    `source` builds a model, `act` sends a chosen item's request, `open_app`
    opens the window and `opening` runs as the menu opens. Ids are handed out
    per item key and never reused, so an item whose label changes keeps its id
    and the host updates it in place.
    """

    def __init__(self, source: Callable[[], Model], act: Callable[[dict[str, object]], None],
                 open_app: Callable[[], None], opening: Callable[[], None]) -> None:
        self._source = source
        self._act = act
        self._open_app = open_app
        self._opening = opening
        self.model: Model | None = None
        #: GetLayout's revision: 1 for the first model's layout, and one more at each change.
        self.revision = 1
        self._ids: dict[str, int] = {"": 0}
        self._items: dict[int, Item] = {}
        self._props: dict[int, dict[str, object]] = {}
        #: Each id's properties when it was last shown, those of items gone since too. The
        #: AppIndicators extension reads no layout while its menu is closed, so it still has
        #: an item that went and came back meanwhile, as it last read it, or with none of its
        #: properties when it asked for them after the item went (_requestProperties). A
        #: property signal makes it read every item's properties as the menu opens
        #: (dbusMenu.js's _onSignal and _doLayoutUpdate).
        self._shown: dict[int, dict[str, object]] = {}
        self._shape: tuple | None = None

    def refresh(self) -> Changes:
        return self.take(self._source())

    def take(self, model: Model) -> Changes:
        """Show `model`, and say how it differs from the last one. The first
        says nothing: the host reads the whole of it."""
        old, self.model = self.model, model
        items: dict[int, Item] = {}
        shape = self._walk(model.menu, items)
        props = {i: item.props() for i, item in items.items()}
        changes = Changes()
        if old is not None:
            changes = Changes(status=old.status != model.status,
                              attention=old.attention_icon != model.attention_icon,
                              tooltip=old.tooltip != model.tooltip,
                              layout=shape != self._shape)
            for i, now in props.items():
                before = self._shown.get(i)
                if before is None:
                    continue    # a new item: the host reads its properties after the layout
                # One back since the last model goes whole: the host may have asked for its
                # properties while it was gone, and kept it with none.
                changed = (now if i not in self._props
                           else {k: v for k, v in now.items() if before.get(k) != v})
                gone = sorted(set(before) - set(now))
                if changed:
                    changes.items.append((i, changed))
                if gone:
                    changes.removed.append((i, gone))
        if changes.layout:
            self.revision += 1
        self._items, self._props, self._shape = items, props, shape
        self._shown.update(props)
        return changes

    def _walk(self, item: Item, items: dict[int, Item]) -> tuple:
        """Give `item` and its children ids; answer the shape of the tree."""
        found = self._ids.setdefault(item.key, len(self._ids))
        items[found] = item
        return (found, item.separator, tuple(self._walk(c, items) for c in item.children))

    # -- what sni.py asks --------------------------------------------------------

    def sni(self) -> dict[str, object]:
        return self.model.sni() if self.model is not None else {}

    def layout(self, parent: int, depth: int, names: Sequence[str]) -> tuple[int, Node] | None:
        """GetLayout: the tree under `parent`, `depth` levels down (-1 for
        all), with only `names` of each item's properties unless it is
        empty. None for an id the menu does not have."""
        if parent not in self._items:
            return None
        return self.revision, self._node(parent, depth, names)

    def _node(self, found: int, depth: int, names: Sequence[str]) -> Node:
        children = [] if depth == 0 else [
            self._node(self._ids[child.key], depth - 1, names)
            for child in self._items[found].children]
        return found, self.properties(found, names), children

    def properties(self, found: int, names: Sequence[str]) -> dict[str, object]:
        props = self._props.get(found, {})
        return {k: v for k, v in props.items() if not names or k in names}

    def group_properties(self, ids: Sequence[int],
                         names: Sequence[str]) -> list[tuple[int, dict[str, object]]]:
        """GetGroupProperties: every item for no ids, and ids it lacks left out."""
        wanted = ids or sorted(self._items)
        return [(i, self.properties(i, names)) for i in wanted if i in self._items]

    def known(self, found: int) -> bool:
        return found in self._items

    def event(self, found: int, event: str) -> bool:
        """A host's Event: a click on an enabled item opens the window for OPEN, or sends
        the item's request when it has one. False for an unknown id."""
        item = self._items.get(found)
        if item is None:
            return False
        if event == "clicked" and item.enabled:
            if item.key == OPEN:
                self._open_app()
            elif item.request:
                self._act(dict(item.request))
        return True

    def about_to_show(self, found: int) -> Changes:
        """The host is about to show the menu (0) or a submenu. The menu reads what may
        be stale first; either then takes a fresh model."""
        if found == 0:
            self._opening()
        return self.refresh()
