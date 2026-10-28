"""`report`, the device report (gearbench/report.py): what the daemon holds, asked of it
first, then every Logitech USB node read, then PipeWire's cards of Logitech's and Astro's
audio devices, printed as a block to paste into an issue, or with --json whole. What to do
next, a grant or a stop of the daemon, goes to stderr, so the block holds the report alone.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import pwd
import re
import subprocess
import sys
from collections.abc import Mapping

from .. import __version__, pipewire_client
from ..apps import OTHER_TOOLS, find_other_tools, other_tool_problem, walk_processes
from ..astro import VENDOR_ID as ASTRO_VENDOR_ID
from ..battery import reading_words
from ..daemon import (
    BUSY,
    DOWN,
    UNKNOWN,
    UNREAD,
    failure,
    invoking_uid,
    listening,
    socket_path,
    use_invoking_users_runtime_dir,
)
from ..hidpp import VENDOR_ID
from ..pipewire_unit import levels
from ..report import (
    BESIDE_HELD,
    FORMAT,
    HELD,
    NO_PERMISSION,
    READ,
    REPORT_FORM,
    UNSURE,
    NotARead,
    gather,
)
from ..wording import counted, escaped, number, whole, word_list
from .output import PROBE_WAIT, START, STOP, Subcommands, print_json, send

#: The fields of each daemon reply the report keeps for a node the daemon holds: what a
#: model's support needs, nothing the owner named.
KEYBOARD_FIELDS = ("name", "product", "answering", "capabilities", "clusters", "effects",
                   "brightness_levels")
MOUSE_FIELDS = ("name", "product", "answering", "capabilities", "clusters", "effects", "dpi",
                "dpi_range", "report_rate", "report_rates")
G432_FIELDS = ("name", "product", "sidetone_found", "sidetone", "sidetone_range")
#: A node the daemon names: the only form its `device` fields take.
NODE = re.compile(r"/dev/(hidraw[0-9]{1,4})")

#: The daemon's state in the report's words, for each way its status may fail
#: (daemon.failure) but DOWN, which is "not running".
DAEMON_UNSURE = {BUSY: "running and did not answer in time",
                 UNREAD: "running, and its reply could not be read",
                 UNKNOWN: "maybe running: it could not be reached"}
#: The daemon's state where it answered `status` with an error, which says nothing of the
#: nodes it holds.
STATUS_FAILED = "running, and its status failed"


def kept(reply: Mapping[str, object], fields: tuple[str, ...]) -> dict:
    """`fields` of a daemon reply, those it has."""
    return {name: reply[name] for name in fields if name in reply}


def held_node(device: object) -> str | None:
    """The node a daemon reply's `device` names, None for no node."""
    found = NODE.fullmatch(device) if isinstance(device, str) else None
    return found[1] if found else None


def ask(request: dict) -> dict | str:
    """The daemon's reply, or what failed, as daemon.failure names it."""
    try:
        return send(request, timeout=PROBE_WAIT)
    except (OSError, json.JSONDecodeError) as exc:
        return failure(exc)


def daemon_holds() -> tuple[str, dict[str, dict], bool]:
    """The daemon's state in words, what it says of each node it holds by node name, and
    whether the models this build drives are left alone from the start: true whenever a
    daemon runs or may when `status` is asked, since its attach retries open and probe such
    a node it does not hold. Where it is false, cmd_report asks again before each such node."""
    status = ask({"command": "status"})
    if status == DOWN:
        return "not running", {}, False
    if isinstance(status, str):
        return DAEMON_UNSURE[status], {}, True
    held: dict[str, dict] = {}
    if node := held_node(status.get("device")):
        held[node] = kept(status, KEYBOARD_FIELDS)
    held.update(other_keyboards(status))
    mouse = ask({"command": "mouse", "ask": False})
    if isinstance(mouse, dict):
        for each in mouse.get("devices") or []:
            if isinstance(each, dict) and (node := held_node(each.get("device"))):
                held[node] = kept(mouse if each.get("selected") else other_mouse(each),
                                  MOUSE_FIELDS)
    g432 = ask({"command": "g432"})
    if isinstance(g432, dict) and g432.get("ok") and (node := held_node(g432.get("device"))):
        held[node] = kept(g432, G432_FIELDS)
    return "running" if status.get("ok") else STATUS_FAILED, held, True


#: The vendor ids whose PipeWire cards the report gives, as PipeWire's ALSA monitor writes
#: a card's device.vendor.id, "0x" and four lowercase digits (pw-dump 1.6.2 on the A50's
#: card, 2026-09-27): Logitech's, and Astro's, the A50 base station's.
AUDIO_VENDORS = frozenset(f"0x{vendor:04x}" for vendor in (VENDOR_ID, ASTRO_VENDOR_ID))


def pipewire_part(run: pipewire_client.Run) -> dict:
    """The audio cards of AUDIO_VENDORS in one pw-dump (audio_cards), or, in words, why
    none was read: no pw-dump, or no PipeWire session to answer it, fails the report only
    where it read no Logitech node either (cmd_report). pw-dump only reads; nothing here
    changes a card."""
    try:
        objects = pipewire_client.dump_objects(run)
    except pipewire_client.StepError as exc:
        return {"unread": str(exc), "cards": []}
    return {"unread": None, "cards": audio_cards(objects)}


#: pw-dump's environment beside this process's: libpipewire then logs nothing to stderr,
#: which a failed pw-dump's words in the block are taken from. With a client.conf it could
#: not parse it logged the file's path, in the home folder, and the line it failed on
#: (pw-dump 1.6.2, 2026-10-25); with this, only "can't create context: Invalid argument".
QUIET_PIPEWIRE = {"PIPEWIRE_DEBUG": "0"}


def pipewire_read() -> dict:
    """pipewire_part, pw-dump run with QUIET_PIPEWIRE and as the user who ran the report
    under sudo, run0 or pkexec: it reads that user's PipeWire (use_invoking_users_runtime_dir,
    which cmd_report calls first), whose socket any program of that user can serve, so it
    gets that user's rights and no more. Not checked under any of the three: that pw-dump,
    run with root's environment, finds that PipeWire."""
    quiet = functools.partial(subprocess.run, env={**os.environ, **QUIET_PIPEWIRE})
    invoker = invoking_uid()
    if invoker is None:
        return pipewire_part(quiet)
    try:
        group = pwd.getpwuid(invoker).pw_gid
    except KeyError:
        return {"unread": f"pw-dump was not run: no user has id {invoker}", "cards": []}
    return pipewire_part(functools.partial(quiet, user=invoker, group=group, extra_groups=[]))


def audio_cards(objects: list) -> list[dict]:
    """Each Audio/Device of AUDIO_VENDORS among pw-dump's objects, each value of another shape
    left out: None in its field, or no item in a list. No name of the card's or its nodes' is
    taken: PipeWire's ALSA monitor builds them from the USB serial, as
    alsa_card.usb-Logitech_G432_Gaming_Headset_000000000000-00 (2026-10-18)."""
    cards = []
    for obj in objects:
        info = (obj.get("info") if isinstance(obj, dict)
                and obj.get("type") == "PipeWire:Interface:Device" else None)
        props, params = _object(info, "props"), _object(info, "params")
        vendor = _text(props.get("device.vendor.id"))
        if props.get("media.class") != "Audio/Device" or vendor not in AUDIO_VENDORS:
            continue
        cards.append({
            "vendor": vendor, "product": _text(props.get("device.product.id")),
            "description": _text(props.get("device.description")),
            "profile_set": _text(props.get("device.profile-set")),
            "profiles": [name for entry in _entries(params, "EnumProfile")
                         if (name := _text(entry.get("name"))) is not None],
            "active_profile": next((name for entry in _entries(params, "Profile")
                                    if (name := _text(entry.get("name"))) is not None), None),
            "output_routes": [_route(entry) for entry in _entries(params, "Route")
                              if entry.get("direction") == "Output"]})
    return cards


def _object(holder: object, name: str) -> dict:
    """`holder`'s `name`, where both are JSON objects, else an empty one."""
    value = holder.get(name) if isinstance(holder, dict) else None
    return value if isinstance(value, dict) else {}


def _entries(params: dict, name: str) -> list[dict]:
    """The objects in the list of a card's params `name`, none where it is no list."""
    value = params.get(name)
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _route(route: dict) -> dict:
    """One output route of a card's Route params, as the report gives it."""
    props = _object(route, "props")
    volumes, base = levels(props.get("channelVolumes")), props.get("volumeBase")
    return {"index": route.get("index") if whole(route.get("index")) else None,
            "name": _text(route.get("name")),
            "device": route.get("device") if whole(route.get("device")) else None,
            "channel_volumes": None if volumes is None else list(volumes),
            "volume_base": base if number(base) else None}


def other_keyboards(status: Mapping) -> dict[str, dict]:
    """What the daemon says of each keyboard it drives beside the leader's (status's
    `keyboards_driven`, the leader's first), by node name: `status` asked with `keyboard`
    naming its model, which answers with that keyboard's entries. A reply that fails, a
    refusal among them, names no node and adds nothing."""
    held: dict[str, dict] = {}
    for each in (status.get("keyboards_driven") or [])[1:]:
        if not (isinstance(each, dict) and isinstance(each.get("model"), str)):
            continue
        reply = ask({"command": "status", "keyboard": each["model"]})
        if isinstance(reply, dict) and (node := held_node(reply.get("device"))):
            held[node] = kept(reply, KEYBOARD_FIELDS)
    return held


def other_mouse(each: dict) -> dict:
    """What the daemon says of an attached mouse beside the selected one, asked by its
    product id with nothing sent to it (`mouse` with `device` and `ask` false), else its
    entry in the selected one's `devices`, its name, product and `answering` alone: a daemon
    that did not answer, refused, or is older than `device` and so answered for the selected
    mouse."""
    reply = ask({"command": "mouse", "ask": False, "device": each.get("product")})
    if (isinstance(reply, dict) and reply.get("ok")
            and reply.get("product") == each.get("product")):
        return reply
    return each


def others_running() -> dict[str, list[int]]:
    """The OTHER_TOOLS running now and their pids (apps.find_other_tools); none for a /proc
    that cannot be read."""
    try:
        return find_other_tools(walk_processes())
    except OSError:
        return {}


#: The refusal with no Logitech node and a PipeWire that listed no card, and the one for a
#: PipeWire that was not read, whose cause is said: PipeWire alone finds an A50 Gen 4.
NO_DEVICE = "error: no Logitech or Astro device found; plug one in and run the report again"
NO_DEVICE_UNREAD = ("error: no Logitech device found on USB, and PipeWire, which finds an A50 "
                    "Gen 4, was not read: {}; plug one in, or fix that, and run the report "
                    "again")


def cmd_report(args: argparse.Namespace) -> int:
    """Gather the report and print it, then what to do about what it could not read."""
    # Under sudo, run0 or pkexec, the daemon's socket and pw-dump's PipeWire are both
    # found in the invoking user's runtime folder.
    use_invoking_users_runtime_dir()
    state, held, unsure = daemon_holds()
    others = others_running()
    try:
        # A daemon `status` found down may start while the nodes are read: systemd restarts
        # a failed one 2 s after it stops (RestartSec).
        nodes = gather(held, lambda: unsure or listening(socket_path()))
    except NotARead as exc:
        print(f"error: {exc}, so the report stopped with it unsent; this is a fault in "
              "Gearbench: please report it", file=sys.stderr)
        return 1
    # An A50 Gen 4 has no Logitech node: its base station is on Astro's vendor id.
    pipewire = pipewire_read()
    if not nodes and not pipewire["cards"]:
        unread = pipewire["unread"]
        # The unread words can hold pw-dump's stderr.
        print(escaped(NO_DEVICE if unread is None else NO_DEVICE_UNREAD.format(unread)),
              file=sys.stderr)
        return 1
    report = {"format": FORMAT, "gearbench": __version__, "kernel": os.uname().release,
              "daemon": state, "others_running": [OTHER_TOOLS[comm].name for comm in others],
              "nodes": nodes, "pipewire": pipewire}
    if args.json:
        print_json(report)
    else:
        print("```text")
        for line in report_lines(report):
            print(escaped(line))
        print("```")
    # Before the advice, so a terminal shows the block first whatever buffers stdout.
    sys.stdout.flush()
    warnings = [f"warning: {other_tool_problem(comm, pids)}; it can take the report's "
                "replies" for comm, pids in others.items()]
    for line in [*warnings, *advice(nodes, block=not args.json)]:
        print(escaped(line), file=sys.stderr)
    return 0


# -- the text form -------------------------------------------------------------------

#: x0000's feature type bits, as the feature table says them.
FLAG_WORDS = ((0x80, "obsolete"), (0x40, "hidden"), (0x20, "engineering"))


def report_lines(report: Mapping) -> list[str]:
    """The text form, a line each: the run, then each node, then each audio card."""
    lines = [(f"Gearbench {report['gearbench']} device report, format {report['format']}, "
              f"Linux {report['kernel']}"),
             f"Gearbench daemon: {report['daemon']}",
             f"PipeWire: {_pipewire_words(report['pipewire'])}"]
    if report["others_running"]:
        lines.append(f"Also running: {word_list(report['others_running'], 'and')}")
    for node in report["nodes"]:
        lines += ["", *node_lines(node)]
    for card in report["pipewire"]["cards"]:
        lines += ["", *card_lines(card)]
    return lines


def _pipewire_words(part: Mapping) -> str:
    """What the PipeWire part holds, for the report's head."""
    if part["unread"] is not None:
        return f"not read: {part['unread']}"
    if not part["cards"]:
        return "no Logitech or Astro audio card"
    return f"{counted(len(part['cards']), 'Logitech or Astro audio card')}, at the end"


def card_lines(card: Mapping) -> list[str]:
    """One audio card's lines: its ids and names, its profiles and its output routes."""
    vendor = card["vendor"].removeprefix("0x")
    ids = (f"{vendor}:{card['product'].removeprefix('0x')}" if card["product"]
           else f"{vendor}, no product id")
    lines = [(f"PipeWire card {ids}"
              f"{', ' + card['description'] if card['description'] else ''}"
              f"{', profile set ' + card['profile_set'] if card['profile_set'] else ''}"),
             f"  profiles: {_words(card['profiles'])}",
             f"  active profile: {_words(card['active_profile'])}"]
    routes = [f"  output route {_words(route['index'])}, {_words(route['name'])}, device "
              f"{_words(route['device'])}: channel volumes {_words(route['channel_volumes'])}, "
              f"volume base {_words(route['volume_base'])}" for route in card["output_routes"]]
    return lines + (routes or ["  output routes: none"])


def node_lines(node: Mapping) -> list[str]:
    """One node's lines: its sysfs part, its state, and what was read."""
    descriptor = node["descriptor"]
    where = (f"interface {node['interface']}" if node["interface"] is not None
             else "behind a receiver")
    lines = [(f"{node['node']}: 046d:{node['product']}"
              f"{', ' + node['model'] if node['model'] else ''}, {where}, "
              f"driver {node['driver'] or 'none'}"),
             (f"  report descriptor: {counted(descriptor['bytes'], 'byte')}, HID++ "
              f"{descriptor['hidpp'] or 'none'}"
              f"{', key reports too' if descriptor['key_reports'] else ''}"
              f"{', cut short' if descriptor['cut'] else ''}")]
    if node["state"] != READ:
        lines.append(f"  {node['state']}")
        lines += [f"  paired: {_words(node['paired'])}"] if "paired" in node else []
        lines += [f"  the daemon says: {key} {_words(value)}" for key, value in
                  node.get("daemon", {}).items()]
        return lines
    lines.append(f"  device index {node['device_index']}, HID++ {node.get('protocol', '?')}")
    lines += _identity_lines(node)
    # A table that was not read is said in `failed`, not as one with no feature.
    lines += _feature_lines(node["features"]) if "features" in node else []
    lines += _section_lines(node)
    lines += ([f"  two reads back to back, {node['reply_order']['result']}"]
              if "reply_order" in node else [])
    lines.append(f"  failed: {'; '.join(node['failed']) or 'nothing'}")
    return lines


def _identity_lines(node: Mapping) -> list[str]:
    lines = []
    if "name" in node:
        lines.append(f"  name: {node['name']['name']}, a {node['name']['type']}")
    if "device" in node:
        device = node["device"]
        if "model_ids" in device:
            # An older 0x0003 has no model ids, or no extended model id (report.device_info).
            extended = (f", extended {device['extended_model_id']}"
                        if "extended_model_id" in device else "")
            transports = (word_list(device["transports"], "and") if device["transports"]
                          else "no transport")
            lines.append(f"  model ids: {' '.join(device['model_ids'])}{extended}, on "
                         f"{transports}")
        # An entity with no prefix, as the G515's type 8 has, gets no word for it.
        lines += [f"  firmware: {fw['type']} {fw['prefix'] + ' ' if fw['prefix'] else ''}"
                  f"{fw['version']}{' (active)' if fw['active'] else ''}, transport id "
                  f"{fw['transport_id']}" for fw in device["firmware"]]
    return lines


def _feature_lines(rows: list) -> list[str]:
    # A FeatureSet v0 gives no versions (report.feature_table): a row has no version, not v0.
    unversioned = any(row["version"] is None for row in rows)
    note = "; FeatureSet v0 gives no versions" if unversioned else ""
    lines = [f"  features: {len(rows)} beside the root{note}"]
    for row in rows:
        flags = [word for bit, word in FLAG_WORDS if row["flags"] & bit]
        version = "" if row["version"] is None else f" v{row['version']}"
        lines.append(f"    0x{row['index']:02x} {row['id']}{version}"
                     f"{' ' + ', '.join(flags) if flags else ''}")
    return lines


def _effects(cluster: Mapping) -> str:
    return ", ".join(f"{e['name']} {e['id']}" for e in cluster["effects"]) or "no effects"


def _section_lines(node: Mapping) -> list[str]:
    """The sections read from lighting to the equalizer, in read_device's order."""
    lines = []
    for cluster in node.get("lighting", {}).get("clusters", []):
        lines.append(f"  lighting cluster {cluster['index']}, {cluster['name']} at "
                     f"{cluster['location']}: {_effects(cluster)}")
    if node.get("lighting", {}).get("brightness_levels"):
        light = node["lighting"]
        lines.append(f"  brightness: {light['brightness']}%, levels "
                     f"{_words(light['brightness_levels'])}%")
    lines += _per_key_lines(node.get("per_key"))
    for zone in node.get("zones", {}).get("zones", []):
        lines.append(f"  zone {zone['index']}, {zone['name']} at {zone['location']}: "
                     f"{_effects(zone)}")
    if "onboard" in node:
        memory = node["onboard"]
        lines.append(f"  onboard memory: {counted(memory['profiles'], 'profile')}, "
                     f"{counted(memory['factory_profiles'], 'factory profile')}, "
                     f"{counted(memory['buttons'], 'button')}, "
                     f"{counted(memory['sector_count'], 'sector')} of "
                     f"{counted(memory['sector_size'], 'byte')}, "
                     f"format {memory['profile_format']}, {memory['mode']} mode, running "
                     f"sector {memory['running_sector']}; its factory sectors are in --json")
    if "battery" in node:
        # level_words has no words for a reading with no percent on battery or in an unknown
        # state, as a 0x1001 or 0x1F20 one with no cell; its voltage is what a cell's curve
        # is built from. In volts after the level, as status and g432-sidetone give it.
        reading = node["battery"]
        volts = f" ({reading['volts']} V)" if "volts" in reading else ""
        lines.append(f"  battery: {reading_words(reading) or reading['state']}{volts}")
    if "dpi" in node:
        dpi = node["dpi"]
        lines.append(f"  DPI: {dpi['dpi']}, default {dpi['default']}, range "
                     f"{_span_words(dpi['range'])}, {counted(dpi['sensors'], 'sensor')}")
    if "report_rate" in node:
        rate = node["report_rate"]
        lines.append(f"  report rate: every {rate['interval_ms']} ms, of "
                     f"{', '.join(f'{hz} Hz' for hz in rate['rates']) or 'none listed'}")
    lines += _extended_lines(node)
    lines += _control_lines(node.get("control_list"))
    if "sidetone" in node:
        lines.append(f"  sidetone: {node['sidetone']['level']}")
    if "equalizer" in node:
        eq = node["equalizer"]
        # getEqInfo's bytes as they came: an end of 0 is dbRange's (g432.eq_range).
        lines.append(f"  equalizer: {counted(eq['bands'], 'band')} at "
                     f"{_words(eq['frequencies'])} Hz, dB range {eq['db_range']}, minimum "
                     f"{eq['db_min']}, maximum {eq['db_max']}, capabilities "
                     f"0x{eq['capabilities']:02x}; "
                     f"gain bytes saved {_words(eq['saved_gains'])}, active "
                     f"{_words(eq['active_gains'])}")
    return lines


def _extended_lines(node: Mapping) -> list[str]:
    """0x2202's DPI, 0x8061's rate and 0x1B0C's buttons, each in a line, where the node has
    them."""
    lines = []
    if "extended_dpi" in node:
        dpi = node["extended_dpi"]
        lines.append(f"  DPI: {dpi['dpi']}, Y {dpi['dpi_y']}, lift-off {dpi['lift_off']}, "
                     f"range {_span_words(dpi['range'])}, flags 0x{dpi['flags']:02x}")
    if "extended_report_rate" in node:
        rate = node["extended_report_rate"]
        now = f"{rate['hz']} Hz" if rate["hz"] else "a rate no source names"
        listed = [f"{hz} Hz" for hz in rate["rates"]] + [
            f"bit {each}" for each in rate["unnamed_bits"]]
        lines.append(f"  report rate: {now} (bit {rate['bit']}), of "
                     f"{', '.join(listed) or 'none listed'}")
    if "analog_buttons" in node:
        analog = node["analog_buttons"]
        lines.append(f"  analog buttons: capabilities {analog['capabilities']}; left "
                     f"{analog['buttons'][0]}, right {analog['buttons'][1]}")
    return lines


def _span_words(span: list[int] | None) -> str:
    """A DPI list's range in words, by band where it has a fourth value (sensor.DpiRange.reply)."""
    return "none listed" if not span else (
        f"{span[0]} to {span[1]}, in steps of {span[2]}"
        + (f" to {span[3]}, by band" if len(span) == 4 else ""))


#: The control ids a line of the text form gives of 0x1B10's list.
CONTROLS_A_LINE = 16


def _control_lines(controls: Mapping | None) -> list[str]:
    """0x1B10's count in a line, then its control ids, CONTROLS_A_LINE a line, each line led
    by the key id of its first."""
    if controls is None:
        return []
    ids = controls["controls"]
    head = f"  control list: {counted(controls['count'], 'control')}, the control id at each key id"
    return [head, *(f"    0x{first:02x} {' '.join(ids[first:first + CONTROLS_A_LINE])}"
                    for first in range(0, len(ids), CONTROLS_A_LINE))]


def _per_key_lines(keys: Mapping | None) -> list[str]:
    """0x8081's zones in a line, as runs, and each page the device refused."""
    if keys is None:
        return []
    runs = ", ".join(str(first) if first == last else f"{first} to {last}"
                     for first, last in keys["zones"]) or "none"
    refused = "".join(f"; page {page['page']} refused: {page['refused']}"
                      for page in keys["pages"] if "refused" in page)
    return [f"  per-key zones: {runs}{refused}"]


def _words(value: object) -> str:
    """A value from a reply in words: a list's items with commas, none for nothing."""
    if isinstance(value, list):
        return ", ".join(_words(item) for item in value) or "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "none" if value is None else str(value)


# -- what to do next ------------------------------------------------------------------

#: Why a node left UNSURE was not read.
DAEMON_MAY_OPEN = ("The daemon opens the devices this build drives while it looks for them, "
                   "so those it does not hold were not read.")
#: The steps to a read of every node the daemon kept the report from.
FULL_READ = f"For the full read: `{STOP}`, run the report again, then `{START}`."


def advice(nodes: list[dict], *, block: bool) -> list[str]:
    """Where to paste the block above, where the text form printed one (`block`), and what
    the person who ran the report can do about a node the daemon holds or may open, one on
    the USB device of a node it holds, and one this account may not open."""
    paste = f"Paste the block above, both ``` lines included, into a device report: {REPORT_FORM}"
    lines = [paste] if block else []
    held = [node["node"] for node in nodes if node["state"] == HELD]
    beside = [node["node"] for node in nodes if node["state"] == BESIDE_HELD]
    not_read: list[str] = []
    if held:
        not_read.append(f"Gearbench holds {word_list(held, 'and')}, so the report has only "
                        f"what the daemon says of {'it' if len(held) == 1 else 'each'}.")
    if beside:
        not_read.append(f"{word_list(beside, 'and')} {'shares' if len(beside) == 1 else 'share'} "
                        "a USB device with a node Gearbench holds, so the report sent "
                        f"{'it' if len(beside) == 1 else 'them'} nothing.")
    if any(node["state"] == UNSURE for node in nodes):
        not_read.append(DAEMON_MAY_OPEN)
    if not_read:
        # The stop and start are said once, after the last line of the devices not read.
        not_read[-1] += f" {FULL_READ}"
    lines += not_read
    for node in nodes:
        if node["state"] == NO_PERMISSION:
            # "No permission to open", the words of the daemon, probe and the node's state.
            lines.append(f"No permission to open {node['node']}. To grant it until the "
                         f"device is unplugged: `sudo setfacl -m u:$USER:rw /dev/{node['node']}`")
            if node["descriptor"]["key_reports"]:
                lines.append(f"{node['node']} carries the device's key reports too: until "
                             "it is unplugged, any program you run could read what is typed "
                             "on it.")
    return lines


def add_report(sub: Subcommands) -> None:
    sub.add("report", help="read every Logitech device on USB, writing nothing, to paste "
                           "into an issue").set_defaults(func=cmd_report)
