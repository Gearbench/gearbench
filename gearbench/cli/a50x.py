"""The Centurion headsets' subcommands: `audio`, what the daemon holds of the headset or reads
from it again, `audio-set`, a setting or the first-read values back, `audio-eq`, an instance's
equalizer or one of its presets, and `audio-mix`, the A50s' stream mix. Each asks the daemon,
which refuses every write to a model not tested on this hardware before anything is sent.
`audio-presets` keeps the preset library on this computer of the model the daemon names
(gearbench.a50x_library), asking the daemon for that model and the bands it saves, and to
write the preset it picks, through presets.run_library.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .. import a50x, a50x_library, eq_presets
from ..battery import kept_at
from ..centurion_features import equalizer, mix, mixer
from ..centurion_features.setting import Setting
from ..report import untested_note
from ..wording import NOT_READ, escaped, gain_range, route, shares_words, word_list
from .output import (
    PUT_BACK,
    Subcommands,
    bands_words,
    battery_words,
    device_words,
    print_block,
    print_reply,
    send,
    silent_note,
)
from .presets import LIBRARY_EMPTY, LibraryKind, library_arguments, library_reply, run_library


def model_names(keep: Callable[[a50x.Model], object] = lambda model: True) -> list[str]:
    """The names of the models of a50x.MODELS that `keep` takes, each once and in their
    order: the G522's two ids share one."""
    return list(dict.fromkeys(model.name for model in a50x.MODELS.values() if keep(model)))


#: Each setting as the command line names it, by its socket word: the word with a hyphen,
#: but for a setting the window names otherwise, whose socket word stays; the report's label
#: is the same with a space. Then the settings by those names.
WORD_OF = {key: {"noise_reduction": "noise-suppression"}.get(key, key.replace("_", "-"))
           for key in a50x.SETTING_OF}
SETTING_WORDS = {word: key for key, word in WORD_OF.items()}
#: The refusal of a value after PUT_BACK, which puts back every setting.
PUT_BACK_ALONE = (f"{PUT_BACK} takes no value: it puts back each setting's first-read value; "
                  "to set one setting, give its name and value")
#: The bands of a slot that is none of the presets.
CUSTOM = "custom"
#: The stream mix's sources as the command line names them, as `words` says them, with the
#: stream port first.
MIX_WORDS = {"stream": mixer.STREAM,
             **{mixer.SHOWN.get(word, word): word for word in mixer.ORDER}}
#: `audio-presets`' refusal of an equalizer whose bands the daemon has not read, the model's
#: name and the instance's.
BANDS_NOT_READ = ("the {}'s {} equalizer has not been read, so there are no bands to save: "
                  "read it with `gearbench-ctl audio --read`")
#: Its list with none in the library yet.
NONE_SAVED = LIBRARY_EMPTY.format("audio-presets --save-as NAME saves the headphones' bands")


def _value(setting: Setting, text: str) -> int | str:
    """`audio-set`'s value as the setting takes it: its word, or a whole number, which the
    daemon checks against the model's range; ValueError with the sentence for text that is
    neither."""
    if setting.names:
        setting.checked(text)
        return text
    try:
        return int(text)
    except ValueError:
        unit = f" of {setting.unit}s" if setting.unit else ""
        never = f", 0 for {setting.never}" if setting.never else ""
        raise ValueError(f"the {setting.words} is a whole number{unit}{never}, "
                         f"not {text!r}") from None


def _model(reply: dict) -> a50x.Model | None:
    """The record of the model a reply names, None for one this install does not know."""
    return a50x.MODELS.get(int(reply["product"], 16))


def _setting_words(setting: Setting, value: int | str, first: int | str | None) -> str:
    """A setting's value in the window's words (Setting.level), with the values it takes
    where it has a unit, the balance's two shares, and the value its first read found in
    parentheses, as `headset` gives what the flash holds."""
    shown = setting.level(value)
    if setting.unit:
        shown += f" ({setting.span})"
    elif setting.key == mix.KEY and isinstance(value, int):
        shown += f", {shares_words(mix.game_share(value))}"
    if first is None or first == value:
        return shown
    return f"{shown} (first read: {setting.level(first)})"


def _bands_words(bands: list[dict]) -> str:
    """Each band's frequency and gain, as the window gives them (bands_words)."""
    return bands_words((band["freq"], band["gain"]) for band in bands)


def _equalizer_words(read: dict) -> str:
    """The preset an instance plays, or custom, and each band's frequency and gain."""
    return f"{read['preset'] or CUSTOM}: {_bands_words(read['bands'])}"


def _mix_words(mix: dict | None) -> str:
    return NOT_READ if mix is None else mixer.words(mix)


def _boom_words(live: bool | None) -> str:
    """The microphone's boom: the window's Live or Muted, then where the boom is."""
    return NOT_READ if live is None else "live, down" if live else "muted, up"


def _report(reply: dict) -> None:
    """`audio`'s report: the device, its framing and features, the battery, each setting,
    the stream mix and the microphone's boom, each equalizer, and what refuses its
    writes."""
    device, model = reply["device"], _model(reply)
    # The route in the window's words, as on every device line, for a model this install
    # knows.
    lines: list[tuple[str | None, str]] = [("device", device_words(
        device, reply["name"], None if model is None else route(model.dongle)))]
    if reply.get("answering") is False:
        # Away behind its receiver: under the device line, as a mouse's.
        lines.append((None, silent_note(reply)))
    bridge = reply.get("bridge")
    if reply["framing"] is not None:
        lines.append(("frames", reply["framing"] if bridge is None
                      else f"{reply['framing']}, through its receiver's bridge at index {bridge}"))
        lines.append(("features", ", ".join(reply["listed"]) or "none listed"))
    lines.append(("battery", battery_words(reply["battery"], kept_at(reply))))
    settings, first = reply["settings"], reply["first"]
    for setting in (model.settings if model else a50x.SETTING_OF.values()):
        value = settings.get(setting.key)
        lines.append((WORD_OF[setting.key].replace("-", " "),
                      NOT_READ if value is None
                      else _setting_words(setting, value, first.get(setting.key))))
    if model is None or model.mixer:
        lines.append(("stream mix", _mix_words(reply.get("mix"))))
        lines.append(("boom", _boom_words(reply.get("mic_live"))))
    for instance in (model.equalizers if model else a50x.INSTANCES):
        read = reply["equalizer"].get(instance)
        lines.append((instance, NOT_READ if read is None else _equalizer_words(read)))
    if reply["writes_refused"]:
        lines.append(("writes", f"refused: {reply['writes_refused']}"))
    if reply["untested"]:
        # Under the device line, as `mouse`, `g432-sidetone` and `status` put it.
        lines.insert(1, (None, untested_note(reply["name"])))
    print_block(lines)


def _reported(request: dict, as_json: bool) -> int:
    """`request` sent, and its reply printed as `audio`'s report, or whole with --json or for
    a refusal."""
    reply = send(request)
    if as_json or not reply.get("ok"):
        return print_reply(reply, as_json)
    _report(reply)
    return 0


def cmd_audio(args: argparse.Namespace) -> int:
    """What the daemon holds of the headset, every feature read again first with --read."""
    return _reported({"command": "a50x", "read": True} if args.read else {"command": "a50x"},
                     args.json)


def cmd_audio_set(args: argparse.Namespace) -> int:
    """A setting written and read back, or with PUT_BACK and no value each first-read value
    put back: the request's `reset`."""
    if args.setting == PUT_BACK:
        if args.value is not None:
            print(f"error: {PUT_BACK_ALONE}", file=sys.stderr)
            return 1
        request: dict = {"command": "a50x_set", "reset": True}
    else:
        setting = a50x.SETTING_OF[SETTING_WORDS[args.setting]]
        try:
            if args.value is None:
                raise ValueError(f"give the {setting.words} to set")
            request = {"command": "a50x_set", "setting": setting.key,
                       "value": _value(setting, args.value)}
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return _reported(request, args.json)


def cmd_audio_eq(args: argparse.Namespace) -> int:
    """An instance's ten gains, or a preset of it, written and read back, or with neither
    given the instance's equalizer as the daemon holds it, refused for an instance the
    attached model has none of."""
    if args.pick is not None:
        if args.gains:
            print("error: give the equalizer ten gains or a preset, not both",
                  file=sys.stderr)
            return 1
        return _reported({"command": "a50x_eq", "instance": args.instance,
                          "preset": args.pick}, args.json)
    if args.gains:
        return _reported({"command": "a50x_eq", "instance": args.instance,
                          "gains": args.gains}, args.json)
    reply = send({"command": "a50x"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    model = _model(reply)
    if model is not None and (refused := a50x.equalizer_refused(model, args.instance,
                                                                 model.equalizers)):
        print(f"error: {refused}", file=sys.stderr)
        return 1
    read = reply["equalizer"].get(args.instance)
    print_block([(args.instance, NOT_READ if read is None else _equalizer_words(read))])
    return 0


def cmd_audio_mix(args: argparse.Namespace) -> int:
    """One source's level or mute, or the stream port's level, written and read back."""
    if (args.level is None) == (args.muted is None):
        print("error: give the stream mix a level, or --mute or --unmute, one of the two",
              file=sys.stderr)
        return 1
    request: dict = {"command": "a50x_mix", "source": MIX_WORDS[args.source]}
    if args.level is not None:
        request["level"] = args.level
    else:
        request["muted"] = args.muted
    return _reported(request, args.json)


class CenturionPresets:
    """`audio-presets`' library, a Centurion model's (presets.LibraryKind)."""

    command = "audio-presets"
    none_saved = NONE_SAVED

    @staticmethod
    def attached(args: argparse.Namespace) -> tuple[dict, a50x.Model] | None:
        """The daemon's `a50x` reply and the record of the model it names, whose library
        `audio-presets` keeps; None once why there is none is said (presets.library_reply)."""
        return library_reply(args, _model, command="a50x")

    @staticmethod
    def load(model: a50x.Model) -> eq_presets.Saved[a50x_library.Preset]:
        return a50x_library.load_library(model)

    @staticmethod
    def edit(change: Callable[[eq_presets.Saved[a50x_library.Preset]],
                              eq_presets.Saved[a50x_library.Preset]],
             model: a50x.Model) -> eq_presets.Saved[a50x_library.Preset]:
        # Saved's changes give the library of the kind they change: a Library's, a Library.
        return a50x_library.edit(
            cast("Callable[[a50x_library.Library], a50x_library.Library]", change), model)

    @staticmethod
    def path(model: a50x.Model) -> Path:
        return a50x_library.library_path(model)

    @staticmethod
    def saved_preset(args: argparse.Namespace, reply: dict, model: a50x.Model,
                     name: str) -> a50x_library.Preset | None:
        """A preset of `model`'s library named `name` of the bands --from's equalizer plays,
        as `reply`, the daemon's, holds them; None once why it cannot be is said."""
        # Before the read: no read gives an equalizer the model does not have.
        if (refused := a50x.equalizer_refused(model, args.source, model.equalizers)) is not None:
            print(f"error: {refused}", file=sys.stderr)
            return None
        read = reply["equalizer"].get(args.source)
        if read is None:
            print(f"error: {BANDS_NOT_READ.format(escaped(reply['name']), args.source)}",
                  file=sys.stderr)
            return None
        try:
            return a50x_library.preset_of({"name": name, "instance": args.source,
                                           "bands": read["bands"]}, model)
        except ValueError as exc:
            print(f"error: {escaped(exc)}", file=sys.stderr)
            return None

    @staticmethod
    def words(preset: a50x_library.Preset) -> str:
        """The preset's name, its equalizer and each band's frequency and gain."""
        entry = a50x_library.entry(preset)
        return f"{entry['name']}, {entry['instance']}: {_bands_words(entry['bands'])}"

    @staticmethod
    def entry(preset: a50x_library.Preset) -> dict:
        return a50x_library.entry(preset)

    @staticmethod
    def report(reply: dict) -> None:
        _report(reply)


CENTURION_PRESETS: LibraryKind[a50x.Model, a50x_library.Preset] = CenturionPresets()


def cmd_audio_presets(args: argparse.Namespace) -> int:
    """The library of the model the daemon names (presets.run_library): with --pick,
    NUMBER's bands written into the slot its equalizer plays, and the headset's report in
    the list's place."""
    return run_library(args, CENTURION_PRESETS)


def add_audio(sub: Subcommands) -> None:
    """The Centurion headsets' subcommands, in the order `gearbench-ctl --help` lists
    them."""
    show = sub.add("audio", help=f"show the {word_list(model_names())} headset: its battery, "
                                 "settings and equalizers, as the daemon last read them")
    show.add_argument("--read", action="store_true",
                      help="read every feature from the headset again first")
    show.set_defaults(func=cmd_audio)

    # The settings that take the same values share one entry, in the order of their first.
    number = "a whole number in the model's range, which the audio command gives"
    takes: dict[str, list[str]] = {}
    for key, setting in a50x.SETTING_OF.items():
        takes.setdefault(word_list(setting.names) if setting.names else number,
                         []).append(WORD_OF[key])
    ranges = "; ".join(f"{', '.join(words)}: {said}" for said, words in takes.items())
    put = sub.add("audio-set", help="set one of the settings of the headset the audio command "
                                    "shows, or put each value its first read found back with "
                                    f"{PUT_BACK}; refused on a headset whose writes are not "
                                    "tested yet")
    put.add_argument("setting", choices=[*SETTING_WORDS, PUT_BACK])
    put.add_argument("value", nargs="?", help=ranges)
    put.set_defaults(func=cmd_audio_set)

    low, high = equalizer.GAIN_RANGE
    eq = sub.add("audio-eq", help="show or set the headphones' or the microphone's equalizer in "
                                  "the headset the audio command shows, or copy one of its "
                                  "presets into it; setting or copying is refused on a headset "
                                  "whose writes are not tested yet")
    eq.add_argument("instance", choices=list(a50x.INSTANCES))
    eq.add_argument("gains", nargs="*", type=float, metavar="GAIN",
                    help=f"the ten bands' gains, {gain_range(low, high)}, lowest band first, "
                         "each band's frequency kept as read; left out, the equalizer is shown")
    presets = "; ".join(f"{instance}: {word_list(names)}"
                        for instance, names in a50x.PRESETS.items())
    eq.add_argument("--pick", metavar="NAME",
                    help=f"copy the {word_list(model_names(lambda model: model.presets))}'s "
                         f"preset NAME into the equalizer ({presets})")
    eq.set_defaults(func=cmd_audio_eq)

    mixers = word_list(model_names(lambda model: model.mixer))
    mix = sub.add("audio-mix", help=f"set the stream mix of the {mixers}: a source's level or "
                                    "its mute, or the stream port's level, the rest as read; "
                                    "refused on a headset whose writes are not tested yet")
    mix.add_argument("source", choices=list(MIX_WORDS))
    mix.add_argument("level", nargs="?", type=int,
                     help=f"its level, 0 to {mixer.LEVELS}, as G HUB's 0 to 100%%")
    muted = mix.add_mutually_exclusive_group()
    muted.add_argument("--mute", dest="muted", action="store_const", const=True,
                       help="mute the source")
    muted.add_argument("--unmute", dest="muted", action="store_const", const=False,
                       help="unmute it")
    mix.set_defaults(func=cmd_audio_mix)

    saved = sub.add("audio-presets",
                    help="show or change the preset library Gearbench keeps on this computer for the "
                         "model of the headset the audio command shows, or pick one, which "
                         "writes its bands into the equalizer it is of")
    saved.add_argument("--save-as", metavar="NAME",
                       help="save the bands the equalizer --from names plays now as a new "
                            f"preset named NAME; at most {eq_presets.PRESETS_MAX} presets in all")
    saved.add_argument("--from", dest="source", choices=list(a50x.INSTANCES),
                       default="headphones",
                       help="the equalizer --save-as saves, the headphones' when left out")
    library_arguments(saved, picks="write its bands into the slot its equalizer plays; "
                                   "G HUB's presets in the headset stay as they are")
    saved.set_defaults(func=cmd_audio_presets)
