"""The tray icon on the session bus: a StatusNotifierItem and its dbusmenu, exported on a
connection of their own and registered with the StatusNotifierWatcher (`TrayExport`). The
model and the menu's answers are tray.py's; the context and the connections are
dbusloop.py's, whose hard rules hold here too.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from . import tray as tray_model
from .dbusloop import ACCESS_DENIED, RETRY, RETRY_MAX, Bus, Loop, said, utf8

if TYPE_CHECKING:
    from gi.repository import Gio, GLib

log = logging.getLogger("gearbench")

SHELL = "org.gnome.Shell"
WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM = "org.kde.StatusNotifierItem"
MENU = "com.canonical.dbusmenu"

#: Seconds between comparing the tray's model with what it shows; a command compares at once.
TRAY_REFRESH = 2.0
NO_WATCHER = ("nothing owns org.kde.StatusNotifierWatcher, so there is no tray; on GNOME, "
              "Ubuntu's AppIndicators extension provides one")
NOT_SHELL = ("a program other than GNOME Shell owns org.kde.StatusNotifierWatcher, and under "
             "GNOME the icon is offered only to the Shell; `busctl --user list | grep "
             "StatusNotifierWatcher` names that program")
#: How a watcher's refusal of the icon starts, in the journal and `tray_state`.
NOT_TAKEN = "the tray did not take the icon"

# No Activate or ContextMenu: the menu is all the icon does, and a host that finds Activate
# waits out a double click before it opens the menu (Ubuntu AppIndicators, appIndicator.js).
# SecondaryActivate and Scroll do nothing, so a middle click or a scroll logs no error in
# the shell's journal.
ITEM_XML = f"""<node><interface name="{ITEM}">
  <method name="ProvideXdgActivationToken"><arg type="s" direction="in"/></method>
  <method name="SecondaryActivate">
    <arg type="i" direction="in"/><arg type="i" direction="in"/>
  </method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewAttentionIcon"/>
  <signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
  {"".join(f'<property name="{name}" type="{kind}" access="read"/>'
           for name, kind in tray_model.SNI_TYPES.items())}
</interface></node>"""
#: The menu's own properties: dbusmenu version 3, as libdbusmenu has it.
MENU_PROPERTIES = {"Version": ("u", 3), "TextDirection": ("s", "ltr"),
                   "Status": ("s", "normal"), "IconThemePath": ("as", [])}
MENU_XML = f"""<node><interface name="{MENU}">
  {"".join(f'<property name="{name}" type="{kind}" access="read"/>'
           for name, (kind, _value) in MENU_PROPERTIES.items())}
  <method name="GetLayout">
    <arg type="i" direction="in"/><arg type="i" direction="in"/>
    <arg type="as" direction="in"/>
    <arg type="u" direction="out"/><arg type="(ia{{sv}}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{{sv}})" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="out"/>
  </method>
  <method name="Event">
    <arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="EventGroup">
    <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
  </method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <method name="AboutToShowGroup">
    <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated">
    <arg type="a(ia{{sv}})"/><arg type="a(ias)"/>
  </signal>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""


@dataclass
class _Tray:
    """The tray icon while it is wanted, and its state on the session bus."""

    #: The model, and what its menu does.
    menu: tray_model.TrayMenu
    #: The item's own connection to the session bus. It takes no well-known name: a Flatpak
    #: app not given the whole session bus reaches a peer only through a name its policy
    #: covers, so it can send neither this connection nor the daemon's other one a message,
    #: which GDBus would read whole before `_from_tray` refuses it. The tray drops the item
    #: when this connection closes, which is how `hide` takes it down.
    link: Bus = field(default_factory=lambda: Bus("session", None))
    #: The item's and the menu's objects on `link`.
    registrations: list[int] = field(default_factory=list)
    #: Why `link` did not connect, for `state`.
    link_error: str | None = None
    #: The StatusNotifierWatcher's name watch, on the daemon's own session connection, and
    #: its unique owner while it has one.
    watch: int = 0
    watcher: str | None = None
    registering: bool = False
    registered: bool = False
    #: Why the tray did not take it, or that there is no tray, for `state`.
    why: str | None = None
    #: The refresh and the registration retry, GLib timeout sources.
    refresh: GLib.Source | None = None
    retry: GLib.Source | None = None
    backoff: float = field(default_factory=lambda: RETRY)
    #: The host's XDG activation token from before a click; it lets the window take focus.
    token: str | None = None


class TrayExport:
    """The tray icon while it is wanted (`show` to `hide`), exported on the session bus. The
    tray.TrayMenu's `act` and `opening` run from the loop's `dispatch()`; its `source` runs
    there too, and from `show` and `refresh`, which the daemon calls outside it. `connected`,
    `dropped` and `shell_appeared` are how its holder tells it that the daemon's own session
    connection is set up or gone, or that the Shell appeared."""

    def __init__(self, loop: Loop, session: Bus) -> None:
        self._loop = loop
        self._session = session
        self._tray: _Tray | None = None

    def show(self, menu: tray_model.TrayMenu) -> None:
        """Show `menu` as a StatusNotifierItem: now, or once the session bus and a tray to
        show it in are there."""
        if self._tray is not None:
            return
        self._tray = _Tray(menu=menu)
        menu.refresh()
        # Not on a connection the bus closed, whose `closed` the next dispatch brings: GLib
        # prints a critical line of its own for a watch made on one, and the next connection's
        # setup exports the tray (`connected`).
        if self._session.conn is not None and not self._session.conn.is_closed():
            self._export_tray(self._session, self._tray)

    def hide(self) -> None:
        """Take the icon down: its connection closed, which the tray drops the item at, and
        its watch gone."""
        tray, self._tray = self._tray, None
        if tray is None:
            return
        self._tray_idle(tray)
        self._unlink_tray(tray)
        if tray.watch:
            self._loop.gio.bus_unwatch_name(tray.watch)
            self._session.watchers.remove(tray.watch)

    def refresh(self) -> None:
        """Compare the menu's model with what the tray shows and signal what
        changed; nothing while no tray is there to show it."""
        tray = self._tray
        if tray is not None and tray.registrations and tray.watcher is not None:
            self._tray_emit(tray, tray.menu.refresh())

    def state(self) -> tuple[bool, str | None]:
        """Whether the icon shows and, when it does not, why; (False, None)
        while it is not wanted."""
        tray = self._tray
        if tray is None:
            return False, None
        if not self._loop.available:
            return False, "python3-gi is not installed"
        if self._session.address is None:
            return False, "there is no session bus"
        if self._session.conn is None:
            return False, "not connected to the session bus"
        if tray.registered:
            return True, None
        if tray.link_error is not None:
            return False, f"could not connect the icon to the session bus: {tray.link_error}"
        return False, tray.why or "registering with the tray"

    def connected(self, bus: Bus) -> None:
        """The daemon's own session connection is set up: export the icon on it, if one is
        wanted."""
        if self._tray is not None:
            self._export_tray(bus, self._tray)

    def dropped(self) -> None:
        """The daemon's own session connection is gone: the watcher's watch went with it, and
        the item's own connection goes too; the next session connection opens a new one."""
        tray = self._tray
        if tray is not None:
            self._tray_idle(tray)
            self._unlink_tray(tray)
            tray.link_error = None
            tray.watch = 0

    def shell_appeared(self) -> None:
        """A watcher passed over for not being the Shell's may be the Shell's now."""
        if self._tray is not None:
            self._register(self._tray)

    def take_token(self) -> str | None:
        """The host's XDG activation token from before a click, once, or None: it lets the
        window take focus."""
        if self._tray is None:
            return None
        token, self._tray.token = self._tray.token, None
        return token

    def _export_tray(self, bus: Bus, tray: _Tray) -> None:
        """Connect the item's own connection, and follow the StatusNotifierWatcher on `bus`,
        the daemon's: the item is registered with each new owner once both are there."""
        tray.link.address = bus.address
        self._link_tray(tray)

        def appeared(_conn: object, _name: str, owner: str) -> None:
            if self._tray is tray:
                self._watcher_appeared(tray, owner)

        def vanished(_conn: object, _name: str) -> None:
            if self._tray is tray:
                self._tray_idle(tray)
                tray.why = NO_WATCHER
                # A topic of its own, which a tray coming back does not
                # reset: Ubuntu's tray goes at every lock (2026-09-26).
                self._loop.say("tray watcher", NO_WATCHER, logging.INFO)
        with self._loop.home():
            # On a connected bus: `show` exports on an open connection, and without one leaves
            # it to `connected`, which the connection's setup calls.
            tray.watch = self._loop.gio.bus_watch_name_on_connection(
                cast("Gio.DBusConnection", bus.conn), WATCHER,
                self._loop.gio.BusNameWatcherFlags.NONE, self._loop.guard(appeared),
                self._loop.guard(vanished))
        bus.watchers.append(tray.watch)

    def _watcher_appeared(self, tray: _Tray, owner: str) -> None:
        """A tray, or a new one: whatever the last one held went with it."""
        self._tray_idle(tray)
        tray.watcher, tray.why, tray.backoff = owner, None, RETRY
        # "tray" stays as it was: a shown icon (`done` in _register) or a failed registration
        # (`refused`) makes its next failure news, so a program that takes the watcher's name
        # over and over is warned of once in between.
        self.refresh()
        self._register(tray)

        def tick() -> None:
            self.refresh()
            tray.refresh = self._loop.timeout(TRAY_REFRESH, tick)
        tray.refresh = self._loop.timeout(TRAY_REFRESH, tick)

    def _link_tray(self, tray: _Tray) -> None:
        """Connect the item's own connection to the session bus, then export the item and its
        menu on it and register them. A failure, or a close while the daemon's own session
        connection stays, is tried again on the reconnect's backoff."""
        link = tray.link
        link.retry = None
        cancellable = link.cancellable = self._loop.gio.Cancellable()

        def linked(_source: object, result: Gio.AsyncResult) -> None:
            conn: Gio.DBusConnection | None = None
            try:
                conn = self._loop.gio.DBusConnection.new_for_address_finish(result)
            except self._loop.glib.Error as err:
                why: str | None = said(err)
            else:
                why = "it hung up after Hello" if conn.is_closed() else None
            if link.cancellable is not cancellable:
                # Hidden, or the session connection gone, while it connected: halt() canceled
                # the connect, and a canceled connect's finish raises IOErrorEnum.CANCELLED,
                # even one that connected first (GTask's check-cancellable), so there is no
                # connection here.
                return
            if why is not None:
                tray.link_error = why
                # A topic of its own, which a shown icon does not reset.
                self._loop.say("tray connection", f"no tray icon: could not connect it to the "
                               f"session bus: {why}; trying again, {RETRY:g} s apart doubling "
                               f"to {RETRY_MAX:g} s")
                self._relink(tray)
                return
            # Nothing for `why`: the connect gave a connection, and it is open.
            conn = cast("Gio.DBusConnection", conn)
            link.conn, link.backoff, tray.link_error = conn, RETRY, None
            self._loop.quiet("tray connection")

            def closed(_closing: object, _vanished: bool, _error: GLib.Error | None) -> None:
                # A bus that went closes the daemon's own connection too, and its drop
                # (`dropped`) stops this retry.
                log.debug("the tray icon's connection to the session bus closed")
                self._unlink_tray(tray)
                self._relink(tray)
            link.closed_handler = conn.connect("closed", self._loop.guard(closed))
            item, menu = (self._loop.gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                          for xml in (ITEM_XML, MENU_XML))
            with self._loop.home():
                tray.registrations = [
                    conn.register_object_with_closures2(
                        tray_model.ITEM_PATH, item, self._loop.guard(self._item_call),
                        self._loop.guard(self._item_property), None),
                    conn.register_object_with_closures2(
                        tray_model.MENU_PATH, menu, self._loop.guard(self._menu_call),
                        self._loop.guard(self._menu_property), None)]
            self._register(tray)
        # _export_tray gives the link its connected bus's address before the first connect.
        self._loop.open(cast("str", link.address), cancellable, linked)

    def _relink(self, tray: _Tray) -> None:
        self._loop.backoff(tray.link, lambda: self._link_tray(tray))

    def _unlink_tray(self, tray: _Tray) -> None:
        """Close the item's connection, or stop it connecting: the tray drops the item."""
        tray.link.halt()
        conn, tray.link.conn = tray.link.conn, None
        if conn is not None:
            conn.disconnect(tray.link.closed_handler)
            for registration in tray.registrations:
                conn.unregister_object(registration)
            # Asynchronous: close_sync flushes and waits for the worker. GDBus ends the close
            # from an idle on the thread's default context, which outside dispatch() is the
            # process's own, one the daemon never runs.
            with self._loop.home():
                conn.close(None, None)
        tray.registrations.clear()
        tray.registering = tray.registered = False

    def _tray_idle(self, tray: _Tray) -> None:
        """No tray to show it in: nothing timed, nothing registered."""
        for source in (tray.refresh, tray.retry):
            if source is not None:
                source.destroy()
        tray.refresh = tray.retry = tray.watcher = None
        tray.registering = tray.registered = False

    def _register(self, tray: _Tray) -> None:
        """RegisterStatusNotifierItem by the item's path, from its own connection, whose unique
        name the watcher takes for the item's; once that connection and a tray are there. Under
        GNOME only the Shell's watcher is asked, the rule `_may_be_tray` keeps, as `_from_tray`
        does: one another program holds gets no registration. A refusal or a timeout is tried again
        on the reconnect's backoff: the AppIndicators extension answers only once the shell has
        started."""
        owner, conn = tray.watcher, tray.link.conn
        if self._tray is not tray or conn is None or owner is None or tray.registering \
                or tray.registered:
            return
        if not self._may_be_tray(owner):
            tray.why = NOT_SHELL
            self._loop.say("tray", f"no tray icon: {NOT_SHELL}")
            return
        tray.registering = True

        def current() -> bool:
            return self._tray is tray and tray.watcher == owner and tray.link.conn is conn

        def done(_reply: tuple) -> None:
            if current():
                tray.registering, tray.registered, tray.why, tray.backoff = False, True, None, RETRY
                self._loop.quiet("tray")
                log.debug("the tray icon is registered with %s", owner)

        def again() -> None:
            tray.retry = None
            self._register(tray)

        def refused(err: GLib.Error) -> None:
            # A connection that closed under the call fails it before its "closed", whose
            # handler connects the item again; one let go of fails it as canceled.
            if not current() or self._loop.benign(err):
                return
            tray.registering, tray.why = False, f"{NOT_TAKEN}: {said(err)}"
            self._loop.say("tray", f"{tray.why}; trying again, {RETRY:g} s apart doubling to "
                                   f"{RETRY_MAX:g} s")
            self._loop.backoff(tray, again)
        self._loop.call(tray.link, owner, WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem",
                        self._loop.glib.Variant("(s)", (tray_model.ITEM_PATH,)), None, done,
                        refused)

    def _tray_emit(self, tray: _Tray, changes: tray_model.Changes) -> None:
        """The signals for what changed: the item's, and the menu's layout and
        its items' properties."""
        item = tray_model.ITEM_PATH
        signals: list[tuple[str, str, str, GLib.Variant | None]] = [
            (item, ITEM, name, None) for name, changed in (
                ("NewAttentionIcon", changes.attention), ("NewToolTip", changes.tooltip))
            if changed]
        if changes.status:
            # tray.TrayMenu.take reports a change of status only from one model to the next.
            signals.append((item, ITEM, "NewStatus", self._loop.glib.Variant(
                "(s)", (cast("tray_model.Model", tray.menu.model).status,))))
        if changes.layout:
            signals.append((tray_model.MENU_PATH, MENU, "LayoutUpdated",
                            self._loop.glib.Variant("(ui)", (tray.menu.revision, 0))))
        if changes.items or changes.removed:
            signals.append((tray_model.MENU_PATH, MENU, "ItemsPropertiesUpdated",
                            self._loop.glib.Variant("(a(ia{sv})a(ias))", (
                                [(i, self._item_props(p)) for i, p in changes.items],
                                changes.removed))))
        # `refresh` checks that the item's objects are registered, and _menu_call runs as a
        # call to one: _link_tray registers them on this connection, and _unlink_tray takes
        # both away.
        conn = cast("Gio.DBusConnection", tray.link.conn)
        for path, interface, name, args in signals:
            try:
                conn.emit_signal(None, path, interface, name, args)
            except self._loop.glib.Error as err:
                # Closed under it: the link's "closed" handler, or the daemon's own
                # connection's for the whole bus, says so once dispatched.
                log.debug("the tray's %s was not sent: %s", name, err.message)
                return

    def _item_props(self, props: Mapping[str, object]) -> dict[str, object]:
        return {name: self._loop.glib.Variant(tray_model.ITEM_TYPES[name], utf8(value))
                for name, value in props.items()}

    def _node(self, node: tray_model.Node) -> tuple:
        found, props, children = node
        return (found, self._item_props(props),
                [self._loop.glib.Variant("(ia{sv}av)", self._node(child)) for child in children])

    def _from_tray(self, sender: str) -> _Tray | None:
        """The tray if a call on the item or its menu is the tray's, else None: from the
        StatusNotifierWatcher's owner, and under GNOME only if that is the Shell. A program
        that is not a Flatpak app, or one given the whole session bus, can call the item's
        connection, and one allowed to own org.kde.* can hold the watcher's name while the
        AppIndicators extension is off at the lock screen."""
        tray = self._tray
        return (tray if tray is not None and sender == tray.watcher
                and self._may_be_tray(sender) else None)

    def _may_be_tray(self, name: str | None) -> bool:
        """Whether `name` may be the tray: under GNOME only the Shell may, so it must
        own org.gnome.Shell too; without a Shell, any watcher may."""
        return self._session.owners.get(SHELL) in (None, name)

    def _item_property(self, _conn: object, sender: str, _path: str, _interface: str,
                       name: str) -> object:
        values = tray.menu.sni() if (tray := self._from_tray(sender)) is not None else {}
        # None, for a read that raced `hide` or is not the tray's, is an
        # error to the caller, and GetAll leaves the property out.
        return (self._loop.glib.Variant(tray_model.SNI_TYPES[name], utf8(values[name]))
                if name in values else None)

    def _item_call(self, _conn: object, sender: str, _path: str, _interface: str,
                   method: str, parameters: GLib.Variant,
                   invocation: Gio.DBusMethodInvocation) -> None:
        if (tray := self._from_tray(sender)) is None:
            self._not_the_tray(invocation)
            return
        if method == "ProvideXdgActivationToken":
            (tray.token,) = parameters.unpack()
        invocation.return_value(None)

    def _menu_property(self, _conn: object, sender: str, _path: str, _interface: str,
                       name: str) -> object:
        return self._loop.glib.Variant(*MENU_PROPERTIES[name]) if self._from_tray(sender) else None

    def _menu_call(self, _conn: object, sender: str, _path: str, _interface: str,
                   method: str, parameters: GLib.Variant,
                   invocation: Gio.DBusMethodInvocation) -> None:
        """The dbusmenu methods. A click runs from Event, on this thread, and
        what it changed is signaled before the reply to the next."""
        if (tray := self._from_tray(sender)) is None:
            self._not_the_tray(invocation)
            return
        menu, args = tray.menu, parameters.unpack()
        if method == "GetLayout":
            found = menu.layout(*args)
            if found is None:
                self._no_item(invocation, args[0])
                return
            revision, node = found
            reply = self._loop.glib.Variant("(u(ia{sv}av))", (revision, self._node(node)))
        elif method == "GetGroupProperties":
            reply = self._loop.glib.Variant("(a(ia{sv}))", ([
                (i, self._item_props(props)) for i, props in menu.group_properties(*args)],))
        elif method == "GetProperty":
            found, name = args
            props = menu.properties(found, [name])
            if name not in props:
                self._no_item(invocation, found, name)
                return
            reply = self._loop.glib.Variant("(v)", (self._item_props(props)[name],))
        elif method in ("Event", "EventGroup"):
            events = [args] if method == "Event" else args[0]
            unknown = [found for found, event, _data, _time in events
                       if not menu.event(found, event)]
            if method == "Event" and unknown:
                self._no_item(invocation, unknown[0])
                return
            invocation.return_value(None if method == "Event"
                                    else self._loop.glib.Variant("(ai)", (unknown,)))
            self.refresh()
            return
        else:
            ids = [args[0]] if method == "AboutToShow" else args[0]
            changes = menu.about_to_show(0 if 0 in ids else -1)
            self._tray_emit(tray, changes)
            reply = (self._loop.glib.Variant("(b)", (changes.layout,)) if method == "AboutToShow"
                     else self._loop.glib.Variant("(aiai)", (
                         [i for i in ids if menu.known(i)] if changes.layout else [],
                         [i for i in ids if not menu.known(i)])))
        invocation.return_value(reply)

    @staticmethod
    def _not_the_tray(invocation: Gio.DBusMethodInvocation) -> None:
        invocation.return_dbus_error(ACCESS_DENIED, "only the tray reads and clicks this item")

    @staticmethod
    def _no_item(invocation: Gio.DBusMethodInvocation, found: int,
                 name: str | None = None) -> None:
        what = f"no menu item {found}" + (f" with a property {name!r}" if name is not None else "")
        invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", what)
