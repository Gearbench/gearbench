// The focused window's process id, for Gearbench's game profiles.
//
// Exported on the Shell's own session-bus connection, the one that owns
// org.gnome.Shell, so the daemon takes these signals from that name's owner alone.
// Only Gearbench's service gets the pid (the owner's decision of 2026-09-26):
// Subscribe answers a same-user caller in a cgroup named gearbench.service, and
// FocusChanged and Stopped go to subscribers alone; Ready, sent to all, carries
// nothing. A same-user program can still read the pid: by moving itself into such a
// cgroup, or by monitoring the session bus as dbus-monitor does, since dbus-daemon
// grants BecomeMonitor to the bus's own user and a monitor gets every message. Only
// the pid goes out: a title can carry a document name or a URL. It runs on the lock
// screen too (unlock-dialog), so the daemon keeps one source through a lock.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {ExtensionState} from 'resource:///org/gnome/shell/misc/extensionUtils.js';

const PATH = '/io/github/wmuschetto/Gearbench/Focus';
const INTERFACE_NAME = 'io.github.wmuschetto.Gearbench.Focus';
const INTERFACE = `<node>
  <interface name="io.github.wmuschetto.Gearbench.Focus">
    <method name="Subscribe"><arg type="u" direction="out" name="pid"/></method>
    <signal name="FocusChanged"><arg type="u" name="pid"/></signal>
    <signal name="Stopped"/>
    <signal name="Ready"/>
  </interface>
</node>`;
const UNIT = 'gearbench.service';
const DENIED = 'org.freedesktop.DBus.Error.AccessDenied';
// What a Shell without this extension answers, so a Subscribe that outlives
// disable() is not taken for a refusal.
const GONE = 'org.freedesktop.DBus.Error.UnknownMethod';

// Whether `pid` runs in a cgroup named UNIT under this user's own service manager.
function inUnit(pid, uid) {
    if (!pid)
        return false;
    let text;
    try {
        const [, contents] = GLib.file_get_contents(`/proc/${pid}/cgroup`);
        text = new TextDecoder().decode(contents);
    } catch {
        return false;
    }
    const manager = `0::/user.slice/user-${uid}.slice/user@${uid}.service/`;
    return text.split('\n').some(
        line => line.startsWith(manager) && line.endsWith(`/${UNIT}`));
}

export default class GearbenchFocus extends Extension {
    enable() {
        this._uid = new Gio.Credentials().get_unix_user();
        // Unique name -> the watch that drops it when it leaves the bus.
        this._subscribers = new Map();
        this._exported = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._exported.export(Gio.DBus.session, PATH);
        this._handler = global.display.connect('notify::focus-window',
            () => this._send('FocusChanged', new GLib.Variant('(u)', [this._pid()])));
        this._exported.emit_signal('Ready', null);
    }

    disable() {
        // GNOME Shell 50.1 (extensionSystem.js _callExtensionDisable) disables and
        // re-enables every extension enabled after the one it disables, leaving
        // their state ACTIVE; disabling this one sets DEACTIVATING first. A rebase
        // sends no Stopped: the enable right after sends Ready and the daemon
        // subscribes again. Its subscribers still get the Stopped of a disable
        // that comes before they subscribe again; the Shell keeps this object
        // from load to unload (_callExtensionInit). From the Shell's source, not
        // yet seen in a session.
        const rebase = Main.extensionManager.lookup(this.uuid)?.state === ExtensionState.ACTIVE;
        const names = new Set([...this._rebased ?? [], ...this._subscribers.keys()]);
        this._rebased = rebase ? names : null;
        global.display.disconnect(this._handler);
        try {
            if (!rebase)
                this._send('Stopped', null, names);
        } finally {
            for (const watch of this._subscribers.values())
                Gio.bus_unwatch_name(watch);
            this._subscribers = null;
            this._exported.unexport();
            this._exported = null;
        }
    }

    SubscribeAsync(_params, invocation) {
        const sender = invocation.get_sender();
        Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionCredentials',
            new GLib.Variant('(s)', [sender]), new GLib.VariantType('(a{sv})'),
            Gio.DBusCallFlags.NONE, -1, null, (bus, result) => {
                let caller = {};
                try {
                    [caller] = bus.call_finish(result).recursiveUnpack();
                } catch {
                    // Gone before its credentials were read.
                }
                if (this._subscribers === null) {
                    invocation.return_dbus_error(GONE, 'the extension stopped');
                } else if (caller.UnixUserID !== this._uid ||
                           !inUnit(caller.ProcessID, this._uid)) {
                    invocation.return_dbus_error(DENIED, `only ${UNIT} gets the focus`);
                } else {
                    this._subscribe(sender);
                    invocation.return_value(new GLib.Variant('(u)', [this._pid()]));
                }
            });
    }

    _subscribe(name) {
        if (this._subscribers.has(name))
            return;
        const watch = Gio.bus_watch_name_on_connection(Gio.DBus.session, name,
            Gio.BusNameWatcherFlags.NONE, null, () => {
                Gio.bus_unwatch_name(watch);
                this._subscribers?.delete(name);
            });
        this._subscribers.set(name, watch);
    }

    // 0 for no window, or one whose client gave Mutter no pid.
    _pid() {
        return Math.max(global.display.focus_window?.get_pid() ?? 0, 0);
    }

    _send(signal, parameters, names = this._subscribers.keys()) {
        for (const name of names)
            Gio.DBus.session.emit_signal(name, PATH, INTERFACE_NAME, signal, parameters);
    }
}
