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
from itertools import zip_longest
from pathlib import Path

from .apps import PATH_MAX
from .battery import (
    CRITICAL_PERCENT,
    below,
    icon_name,
    level_words,
    notice_levels,
    on_power,
)
from .wording import (
    ASLEEP_OR_SWITCHED_OFF,
    NO_GAME_PROFILE,
    NOT_ANSWERING_WORDS,
    NOT_READ,
    READ_AT,
    capitalized,
    clock_shown,
    home_shortened,
    route,
)

#: Where sni.py exports the item and its menu: KDE's paths. The item is registered
#: by ITEM_PATH, and its Menu property names MENU_PATH.
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

ACTIVE, NEEDS_ATTENTION = "Active", "NeedsAttention"
#: The app's symbolic icon, which install.sh and the package put in the hicolor theme
#: (desktop/icons/symbolic/).
ICON = "io.github.gearbench.Gearbench-symbolic"
#: The codes of the problems that say a device does not answer, named once for the daemon's
#: modules that give them and for ASLEEP_PROBLEMS.
KEYBOARD_NO_ANSWER_CODE = "keyboard_no_answer"
KEYBOARD_NOT_ANSWERING_CODE = "keyboard_not_answering"
HEADSET_NOT_ANSWERING_CODE = "headset_not_answering"
A50X_HEADSET_AWAY_CODE = "a50x_headset_away"
#: A device's sign for its state, which a device's Status row draws (gui.style.DOT_STATES)
#: and a problem line takes by its code: the warning sign for a device that does not answer,
#: such as one asleep, switched off or out of range, the error sign for any other, a device
#: not available. The panel icon takes the worst listed line's (problems_icon).
ASLEEP_PROBLEMS = frozenset({KEYBOARD_NO_ANSWER_CODE, KEYBOARD_NOT_ANSWERING_CODE,
                             HEADSET_NOT_ANSWERING_CODE, A50X_HEADSET_AWAY_CODE})
ASLEEP_ICON, NOT_AVAILABLE_ICON = "dialog-warning-symbolic", "dialog-error-symbolic"
#: Where GNOME Shell does not run, the folder the item points the tray host at
#: (IconThemePath): the panel's and the problem lines' two signs in Adwaita's shapes, under
#: names of the package's own (TRAY_SIGNS) and with KDE's color-scheme style, so KDE Plasma
#: draws them in the panel's text color as GNOME does (the owner's decision of 2026-10-28).
#: Plasma's menu looks an item's icon-name up with the same loader as the panel's icon
#: (PlasmaDBusMenuImporter's iconForName, given iconLoader()). Relies on
#: KDE Plasma 6.8's host as read in its source
#: (statusnotifieritemsource.cpp, and KIconLoader's findMatchingIcon and processSvg, which
#: recolors only an SVG with that style), and seen on Plasma 6.8.1 on 2026-11-03, the sign
#: in the panel's text color under Breeze, Breeze Dark and CachyOS-Nord: it looks a name up in
#: the icon theme in force and in hicolor before this folder, and Breeze draws
#: dialog-error-symbolic as a red box; in each theme it also tries the name cut at its last
#: hyphen, again and again, so no such cut of a name here may be a theme's, as the app's id
#: would be (ICON). Not sent under GNOME: the AppIndicator extension looks each name the item
#: gives up in this folder alone (appIndicator.js, _getIconData), and it holds neither ICON
#: nor a battery's icon.
TRAY_ICONS = Path(__file__).resolve().parent / "gui" / "icons" / "tray"
TRAY_SIGNS = {ASLEEP_ICON: "gearbench-warning-symbolic",
              NOT_AVAILABLE_ICON: "gearbench-error-symbolic"}
#: The longest problem or name line in the menu. GNOME's menu neither wraps nor has a
#: maximum width, and a problem or a name can run to hundreds of characters, wider than a
#: screen.
PROBLEM_MAX = 80
#: Where a problem's message may be cut: after a clause, or before an aside.
CLAUSE_END = re.compile(r"[,;:.] | \(")

#: The key of the item that opens the window; it sends no request.
OPEN = "open"

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
    #: A keyboard on its cable, which is charging or full: with no reading, it is shown as
    #: on its cable.
    cabled: bool
    #: battery.icon_name's for the whole reading, the icon the window's Battery row shows
    #: too.
    icon: str
    #: When a reading kept for a device that does not answer was taken, in seconds since the
    #: epoch; None while it answers or for a time not known.
    read_at: float | None = None
    #: Below this on battery the icon asks for attention: the lower level of the device's
    #: notices preset (battery.notice_levels), compared as the notices compare, by the whole
    #: percent shown, so 14.6 % shows as 15 % and is not below 15.
    low_below: int = CRITICAL_PERCENT

    @classmethod
    def of(cls, key: str, name: str, reading: dict | None, *, answering: bool = True,
           cabled: bool = False, read_at: float | None = None,
           preset: str | None = None) -> Battery:
        """From a battery.read() reading, or None when there is none; `read_at` is when one
        kept while the device does not answer was taken, and `preset` the device's notices
        preset, whose lower level is `low_below` (battery.notice_levels)."""
        reading = reading or {}
        return cls(key, name, percent=reading.get("percent"), state=reading.get("state"),
                   plugged=on_power(reading),
                   approximate=bool(reading.get("approximate")),
                   answering=answering, cabled=cabled, icon=icon_name(reading, cabled),
                   read_at=None if answering else read_at,
                   low_below=notice_levels(preset)[1])

    @property
    def low(self) -> bool:
        return below(self.percent, self.state, self.plugged, self.low_below)

    def words(self) -> str:
        """`G915: 80%, charging`, one line of the menu and the tooltip, the reading in the
        window's words (battery.level_words), and a device that does not answer in its Status
        dot's, but a keyboard on its cable, which is only "not answering"."""
        said = level_words(self.percent, self.approximate, self.state)
        if not said and not self.cabled:
            unread = f"battery {NOT_READ}" if self.answering else ASLEEP_OR_SWITCHED_OFF
            return f"{self.name}: {unread}"
        kept = ("" if self.answering
                else READ_AT.format(clock_shown(self.read_at)) if self.read_at is not None
                else f", {NOT_ANSWERING_WORDS if self.cabled else ASLEEP_OR_SWITCHED_OFF}")
        return f"{self.name}: {said or route('cable')}{kept}"


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
    #: Each problem's code, in `problems`' order (Daemon.problems), for its line's icon.
    problem_codes: tuple[str, ...] = ()
    #: None with no mouse selected, or one with no DPI stages or not answering.
    stages: Stages | None = None
    profiles: tuple[str, ...] = ()
    loaded: str | None = None
    #: Each M profile's number and label while a keyboard that switches them here is connected
    #: (keyboard.m_profiles_in_menu), empty otherwise. `active` is the M profile in force.
    m_profiles: tuple[tuple[int, str], ...] = ()
    active: int = 1
    keep_loaded: bool = False
    #: None while no keyboard with lighting is driven.
    lock_lights: bool | None = None
    #: None while no mouse with a DPI the daemon sets is attached.
    dpi_notices: bool | None = None
    #: None while no mouse with lighting is attached.
    mouse_sync: bool | None = None
    #: None while no headset with lights is attached.
    headset_sync: bool | None = None
    #: The keyboard the mice and the headset follow, in words (keyboard.followed).
    leader: str = "keyboard"


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
        found: dict[str, object] = {"label": self.label}
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

    def sni(self, *, gnome: bool) -> dict[str, object]:
        """The StatusNotifierItem's properties, by SNI_TYPES; where GNOME Shell does not run
        (`gnome` false), with the tray's own signs (TRAY_ICONS)."""
        # The description is markup to the spec, so it is escaped.
        description = html.escape("\n".join(self.tooltip), quote=False)
        return {
            "Category": "Hardware", "Id": "gearbench", "Title": self.title,
            "Status": self.status, "WindowId": 0,
            "IconThemePath": "" if gnome else str(TRAY_ICONS), "Menu": MENU_PATH,
            "ItemIsMenu": True, "IconName": ICON, "IconPixmap": [],
            "OverlayIconName": "", "OverlayIconPixmap": [],
            "AttentionIconName": tray_sign(self.attention_icon, gnome=gnome),
            "AttentionIconPixmap": [],
            "AttentionMovieName": "", "ToolTip": ("", [], self.title, description),
        }


def tray_sign(icon: str, *, gnome: bool) -> str:
    """The name the item gives the tray for `icon`, the panel's or a menu line's: a sign's own
    in TRAY_ICONS where GNOME Shell does not run (`gnome` false); any other icon, and every
    one under GNOME, as it is."""
    return icon if gnome else TRAY_SIGNS.get(icon, icon)


def tray_label(label: str, *, gnome: bool) -> str:
    """A menu item's label escaped so the host shows it as it is. Under GNOME only the first
    underscore before another character is doubled: the AppIndicators extension unescapes once
    (dbusMenu.js, `replace(/_([^_])/, '$1')`) and shows any other escaped one doubled.
    Elsewhere every underscore is doubled: a host that follows the dbusmenu spec, as KDE
    Plasma's does (libdbusmenuqt's swapMnemonicChar, read 2026-10-31), takes the first lone one
    as a mnemonic and drops each later one and a trailing one."""
    return re.sub(r"_(?=[^_])", "__", label, count=1) if gnome else label.replace("_", "__")


def request(**fields: object) -> tuple[tuple[str, object], ...]:
    return tuple(fields.items())


def problem_line(message: str) -> str:
    """A problem's message cut for its menu line, in the daemon's case (problem_items
    capitalizes it), the home directory as ~ as the window writes it: whole up to PROBLEM_MAX
    characters. A longer one is cut at the last clause end that fits, else at a space, else
    at PROBLEM_MAX - 1 characters, and ends in an ellipsis. A clause end inside the path of a
    .json file (one that starts with ~ or /) or an aside is not cut at, nor one after a
    colon: the path says which file, and the others explain what comes before. Paths and
    asides are looked for in the first PROBLEM_MAX + PATH_MAX characters, as far as a path
    that starts in the line can run. A message says first what is wrong; the tooltip and
    `gearbench-ctl status` give it whole."""
    message = home_shortened(message)
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


def name_line(name: str) -> str:
    """A profile's label cut for its menu line: whole up to PROBLEM_MAX characters, else its
    first PROBLEM_MAX - 1 and an ellipsis."""
    return name if len(name) <= PROBLEM_MAX else name[:PROBLEM_MAX - 1] + "…"


def problems_icon(problems: Sequence[str], codes: Sequence[str]) -> str:
    """The panel's attention icon while `problems` are listed, with their `codes`: the worst
    line's sign, one sign with one meaning (the owner's decision of 2026-10-27): the
    warning sign while every one is a device that does not answer (ASLEEP_PROBLEMS), the
    error sign while any other is listed, a problem given no code included. With none, the
    error sign, so the icon changes only when one that does not answer comes."""
    asleep = bool(problems) and all(code in ASLEEP_PROBLEMS for _message, code
                                    in zip_longest(problems, codes, fillvalue=""))
    return ASLEEP_ICON if asleep else NOT_AVAILABLE_ICON


def problem_items(problems: Sequence[str], codes: Sequence[str]) -> tuple[Item, ...]:
    """A line per problem, keyed by place so one that goes or changes is a
    new label under the same id. It starts with a capital, as the menu's other
    labels do and the window's device tabs show a problem, and its icon is the window's
    for its code's state (ASLEEP_PROBLEMS); a problem given no code, as a drawing's, takes
    the error sign."""
    return tuple(Item(f"problem:{i}", capitalized(problem_line(message)),
                      ASLEEP_ICON if code in ASLEEP_PROBLEMS else NOT_AVAILABLE_ICON,
                      enabled=False)
                 for i, (message, code) in enumerate(zip_longest(problems, codes, fillvalue="")))


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


def listed_profiles(library: Sequence[str], loaded: str | None) -> tuple[str, ...]:
    """The game profiles a front end lists: the library's, then the loaded one the library
    lacks, since the daemon keeps a loaded profile whose file is now unreadable or gone."""
    return (*library, *((loaded,) if loaded and loaded not in library else ()))


def profile_menu(snap: Snapshot) -> Item:
    """The Game profile submenu: No game profile, each profile, and Keep loaded while one is
    loaded, as the Keyboard tab shows its switch."""
    names = listed_profiles(snap.profiles, snap.loaded)
    choices = [Item("unload", NO_GAME_PROFILE, toggle="radio", on=snap.loaded is None,
                    request=None if snap.loaded is None else request(command="unload"))]
    choices += [Item(f"profile:{name}", name_line(name), toggle="radio", on=name == snap.loaded,
                     request=None if name == snap.loaded else request(command="load", value=name))
                for name in names]
    keep = () if snap.loaded is None else (
        Item("separator:keep_loaded", separator=True),
        switch("Keep loaded", snap.keep_loaded, command="keep_loaded"))
    return Item("profiles", "Game profile", children=(*choices, *keep))


def m_profile_menu(snap: Snapshot) -> Item:
    """The M profile submenu: each M profile, the one in force marked. Choosing another
    switches to it, as its M key does on a keyboard that has one."""
    choices = tuple(
        Item(f"m_profile:{number}", name_line(label), toggle="radio", on=number == snap.active,
             request=None if number == snap.active else request(command="profile", value=number))
        for number, label in snap.m_profiles)
    return Item("m_profiles", "M profile", children=choices)


def switch_items(snap: Snapshot) -> tuple[Item, ...]:
    """The switches, each while its device is there (Snapshot's None)."""
    found: list[Item] = []
    if snap.lock_lights is not None:
        found.append(switch("Lights off when locked", snap.lock_lights, command="lock_lights"))
    if snap.dpi_notices is not None:
        found.append(switch("DPI notices", snap.dpi_notices, command="dpi_notices"))
    if snap.mouse_sync is not None:
        found.append(switch(f"Mouse follows the {snap.leader}", snap.mouse_sync,
                            command="mouse_sync"))
    if snap.headset_sync is not None:
        found.append(switch(f"Headset follows the {snap.leader}", snap.headset_sync,
                            command="headset_sync"))
    return tuple(found)


def build(snap: Snapshot, title: str) -> Model:
    """The icon, tooltip and menu for one snapshot."""
    lows = [battery for battery in snap.batteries if battery.low]
    groups = (
        # The AppIndicators extension shows no tooltip, so the menu is where they can be read.
        ("problems", problem_items(snap.problems, snap.problem_codes)),
        ("batteries", tuple(Item(f"battery:{b.key}", b.words(), b.icon, enabled=False)
                            for b in snap.batteries)),
        (OPEN, (Item(OPEN, f"Open {title}"),)),
        ("dpi", dpi_items(snap.stages) if snap.stages is not None else ()),
        # Without a game profile, No game profile would be the submenu's only choice.
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
        status=NEEDS_ATTENTION if snap.problems or lows else ACTIVE,
        # A low battery asks with the lowest one's level icon, as the very-low notice does:
        # the caution sign is a battery fault's, on the menu's line under it.
        attention_icon=(min(lows, key=lambda b: b.percent or 0.0).icon
                        if lows and not snap.problems
                        else problems_icon(snap.problems, snap.problem_codes)),
        # The problems first, as on the menu, whole and with the home folder as ~.
        tooltip=(*(capitalized(home_shortened(problem)) for problem in snap.problems),
                 *(b.words() for b in snap.batteries)),
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
    #: Items came, went or moved: the host reads the layout again. Ubuntu's
    #: AppIndicators extension reads only `type` and `children-display` there,
    #: and all of a new item's properties, but keeps the rest of an item it
    #: has (dbusMenu.js's _doLayoutUpdate, gnome-shell-ubuntu-extensions
    #: 50.26.04.8).
    layout: bool = False
    #: The properties that changed on items in the last model, all of those of
    #: an item back since an earlier one, and in `removed` those now at their
    #: default, whether or not the layout changed too.
    items: list[tuple[int, dict[str, object]]] = field(default_factory=list)
    removed: list[tuple[int, list[str]]] = field(default_factory=list)


class TrayMenu:
    """The model the desktop shows, and what its menu does.

    `source` builds a model, `act` sends a chosen item's request with its label, `open_app`
    opens the window and `opening` runs as the menu opens. Ids are handed out
    per item key and never reused, so an item whose label changes keeps its id
    and the host updates it in place.
    """

    def __init__(self, source: Callable[[], Model],
                 act: Callable[[dict[str, object], str], None],
                 open_app: Callable[[], object], opening: Callable[[], None]) -> None:
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
        #: Each menu's items, by id, as the last GetLayout that listed them gave them. KDE
        #: Plasma reads a submenu's items only when AboutToShow says they changed or it has
        #: none, and at a LayoutUpdated reads the whole menu one level deep (libdbusmenuqt's
        #: dbusmenuimporter.cpp, refresh and slotAboutToShowDBusCallFinished).
        self._served: dict[int, tuple[int, ...]] = {}

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

    def sni(self, *, gnome: bool) -> dict[str, object]:
        return self.model.sni(gnome=gnome) if self.model is not None else {}

    def layout(self, parent: int, depth: int, names: Sequence[str]) -> tuple[int, Node] | None:
        """GetLayout: the tree under `parent`, `depth` levels down (-1 for
        all), with only `names` of each item's properties unless it is
        empty. None for an id the menu does not have. It keeps the items it lists under
        each menu, for `stale`."""
        if parent not in self._items:
            return None
        return self.revision, self._node(parent, depth, names)

    def _node(self, found: int, depth: int, names: Sequence[str]) -> Node:
        children = [] if depth == 0 else [
            self._node(self._ids[child.key], depth - 1, names)
            for child in self._items[found].children]
        if depth != 0:
            self._served[found] = tuple(child[0] for child in children)
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

    def stale(self, found: int) -> bool:
        """AboutToShow's answer for `found`: whether its items differ from those the last
        GetLayout listing them gave, a change an earlier refresh signaled included. False for
        an id the menu does not have."""
        item = self._items.get(found)
        return item is not None and self._served.get(found) != tuple(
            self._ids[child.key] for child in item.children)

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
                self._act(dict(item.request), item.label)
        return True

    def about_to_show(self, found: int) -> Changes:
        """The host is about to show the menu (0) or a submenu. The menu reads what may
        be stale first; either then takes a fresh model."""
        if found == 0:
            self._opening()
        return self.refresh()
