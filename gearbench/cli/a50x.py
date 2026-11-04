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
from typing import cast

from .. import a50x, a50x_library, eq_presets
from ..battery import kept_at
from ..centurion_features import equalizer, mix, mixer, noise_reduction
from ..centurion_features.setting import Setting
from ..report import untested_note
from ..wording import (
    NO_SETTING,
    NOT_READ,
    bands_words,
    escaped,
    gain_range,
    route,
    shares_words,
    word_list,
    written,
)
from .output import (
    PUT_BACK,
    Subcommands,
    battery_words,
    device_words,
    print_block,
    print_reply,
    send,
    silent_note,
    writes_lines,
)
from .presets import LIBRARY_EMPTY, LibraryKind, library_arguments, library_reply, run_library


def model_names(keep: Callable[[a50x.Model], object] = lambda model: True) -> list[str]:
    """The names of the models of a50x.MODELS that `keep` takes, each once and in their
    order: the G522's two ids share one."""
    return list(dict.fromkeys(model.name for model in a50x.MODELS.values() if keep(model)))


#: Each setting as the command line names it, by its socket word: the word with a hyphen,
#: but for a setting the window names otherwise, whose socket word stays; the report's label
#: is the same with a space. Then the settings by those names.
WORD_OF = {key: {noise_reduction.KEY: "noise-suppression"}.get(key, key.replace("_", "-"))
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
                  "read it with " + a50x.AUDIO_READ)
#: The same refusal while the headset is away behind its receiver, which no read reaches
#: until it answers again: the model's name, the instance's.
BANDS_NOT_READ_AWAY = ("the {}'s {} equalizer has not been read and its headset is not "
                       "answering, so there are no bands to save: switch the headset on or "
                       "bring it near, then read it with " + a50x.AUDIO_READ)
#: The equalizer `audio-presets --save-as` saves with no --from, and the refusal of
#: --from without --save-as.
SAVED_FROM = "headphones"
FROM_ALONE = "--from names the equalizer --save-as saves; give it with --save-as NAME"
#: Its list with none in the library yet.
NONE_SAVED = LIBRARY_EMPTY.format("`gearbench-ctl audio-presets --save-as NAME` saves the "
                                  "headphones' bands")


def _value(setting: Setting, text: str) -> int | str:
    """`audio-set`'s value as the setting takes it: its word, or a whole number, which the
    daemon checks against the model's range, or the word `audio` prints for a 0, which is
    sent as 0; ValueError with the sentence for text that is none of them."""
    if setting.names:
        setting.checked(text)
        return text
    if setting.never and text.strip().lower() == setting.never:
        return 0
    try:
        return int(text)
    except ValueError:
        unit = f" of {setting.unit}s" if setting.unit else ""
        never = f"{setting.never} or " if setting.never else ""
        raise ValueError(f"the {setting.words} is {never}a whole number{unit}, "
                         f"not {text!r}") from None


def _setting_words(setting: Setting, value: int | str, first: int | str | None) -> str:
    """A setting's value in the window's words (Setting.level), with the values it takes
    where it has a unit, the balance's two shares, and the value its first read found in
    parentheses, as `headset` gives what the flash holds."""
    shown = setting.level(value)
    if setting.unit:
        shown += f" ({setting.span})"
    elif setting.key == mix.KEY and isinstance(value, int):
        shown += f", {shares_words(value, mix.WIRE[-1])}"
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


def _setting_line(setting: Setting, reply: dict) -> tuple[str, str]:
    """A setting's line of `audio`'s report: its label, and its value (_setting_words) or
    "not read"."""
    value = reply["settings"].get(setting.key)
    return (WORD_OF[setting.key].replace("-", " "),
            NOT_READ if value is None
            else _setting_words(setting, value, reply["first"].get(setting.key)))


def _device_lines(reply: dict, model: a50x.Model | None) -> list[tuple[str | None, str]]:
    """The device line, with the route in the window's words for a model this install
    knows, and under it, for a headset away behind its receiver, the note a mouse's has
    (silent_note)."""
    lines: list[tuple[str | None, str]] = [("device", device_words(
        reply["device"], reply["name"], None if model is None else route(model.dongle)))]
    if reply.get("answering") is False:
        lines.append((None, silent_note(reply)))
    return lines


def _report(reply: dict) -> None:
    """`audio`'s report: the device, its framing and features, the battery, then the rest in
    the window's groups' order: the balance, the microphone's boom and settings, each
    equalizer, the stream mix, the other settings, and what refuses its writes."""
    model = a50x.reply_model(reply)
    lines = _device_lines(reply, model)
    bridge = reply.get("bridge")
    if reply["framing"] is not None:
        lines.append(("frames", reply["framing"] if bridge is None
                      else f"{reply['framing']}, through its receiver's bridge at index {bridge}"))
        lines.append(("features", ", ".join(reply["listed"]) or "none listed"))
    lines.append(("battery", battery_words(reply["battery"], kept_at(reply))))
    settings = model.settings if model else a50x.SETTING_OF.values()
    mixed = model is None or model.mixer
    # The balance under the battery, as the window's status group has it: the headset's
    # dial sets it.
    lines += [_setting_line(setting, reply) for setting in settings if setting.key == mix.KEY]
    if mixed:
        lines.append(("boom", _boom_words(reply.get("mic_live"))))
    lines += [_setting_line(setting, reply) for setting in settings
              if setting.key in a50x.MIC_SETTINGS]
    for instance in (model.equalizers if model else a50x.INSTANCES):
        read = reply["equalizer"].get(instance)
        lines.append((instance, NOT_READ if read is None else _equalizer_words(read)))
    if mixed:
        lines.append(("stream mix", _mix_words(reply.get("mix"))))
    lines += [_setting_line(setting, reply) for setting in settings
              if setting.key != mix.KEY and setting.key not in a50x.MIC_SETTINGS]
    lines += writes_lines(reply["writes_refused"])
    if reply["untested"]:
        # Under the device line and above a silent headset's note, as each report that
        # gives the note puts it.
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
    """A setting written and read back, refused without --json before anything is written
    for one the attached model lacks (_setting_lacked), or with PUT_BACK and no value each
    first-read value put back: the request's `reset`."""
    if args.setting == PUT_BACK:
        if args.value is not None:
            print(f"error: {PUT_BACK_ALONE}", file=sys.stderr)
            return 1
        request: dict = {"command": "a50x_set", "reset": True}
    else:
        setting = a50x.SETTING_OF[SETTING_WORDS[args.setting]]
        try:
            if args.value is None:
                raise ValueError(f"give the {setting.words} a value")
            request = {"command": "a50x_set", "setting": setting.key,
                       "value": _value(setting, args.value)}
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not args.json and (lacked := _setting_lacked(args.setting)) is not None:
            return lacked
    return _reported(request, args.json)


def _setting_lacked(word: str) -> int | None:
    """1 once the daemon's refusal of its `a50x` read, or the refusal of the setting `word`
    names where the attached model lacks it, is printed; None where the model has it, or
    where this install does not know the model, whose setting the daemon then checks."""
    # The choices are every model's settings, and the daemon's refusal of one the model
    # lacks names them by their socket words: refused here in this command's words. With
    # --json the daemon's own refusal is printed instead.
    reply = send({"command": "a50x"})
    if not reply.get("ok"):
        return print_reply(reply, False)
    model = a50x.reply_model(reply)
    key = SETTING_WORDS[word]
    if model is None or any(own.key == key for own in model.settings):
        return None
    settings = word_list([WORD_OF[own.key] for own in model.settings], "and")
    print(f"error: {NO_SETTING.format(escaped(reply['name']), written(word), settings)}",
          file=sys.stderr)
    return 1


def _gains(text: str) -> list[float]:
    """`--gains`' G1,G2,...: numbers of dB, or ValueError with the sentence; their count and
    range are the daemon's to refuse (checked_gains)."""
    try:
        return [float(part) for part in text.split(",")]
    except ValueError:
        raise ValueError("--gains is ten numbers of dB split by commas, like "
                         f"0,2,0,-1.5,3,0,0,0,0,0, not {text!r}") from None


def _bare_gains(words: list[str]) -> list[float]:
    """The gains given bare, 1.2.1's form of --gains; ValueError with the sentence for a
    word that is no number, such as a preset's name typed without --pick."""
    try:
        return [float(word) for word in words]
    except ValueError:
        raise ValueError("give the equalizer's gains with --gains G1,G2,... or a preset "
                         f"with --pick NAME, not {' '.join(words)!r}") from None


def cmd_audio_eq(args: argparse.Namespace) -> int:
    """An instance's ten gains, by --gains or as ten numbers, or a preset of it, written and
    read back, or with none given the instance's equalizer as the daemon holds it, under the
    device line and the note `audio` gives while the headset is away, refused for an
    instance the attached model has none of."""
    if args.gains_option is not None and args.gains:
        print("error: give the equalizer's gains with --gains or as ten numbers, one of the "
              "two", file=sys.stderr)
        return 1
    try:
        gains = (_bare_gains(args.gains) if args.gains_option is None
                 else _gains(args.gains_option))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.pick is not None:
        if gains:
            print("error: give the equalizer ten gains or a preset, one of the two",
                  file=sys.stderr)
            return 1
        return _reported({"command": "a50x_eq", "instance": args.instance,
                          "preset": args.pick}, args.json)
    if gains:
        return _reported({"command": "a50x_eq", "instance": args.instance,
                          "gains": gains}, args.json)
    reply = send({"command": "a50x"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    model = a50x.reply_model(reply)
    if model is not None and (refused := a50x.equalizer_refused(model, args.instance,
                                                                 model.equalizers)):
        print(f"error: {refused}", file=sys.stderr)
        return 1
    read = reply["equalizer"].get(args.instance)
    # A headset away: its last bands under the device line and the note `audio` gives.
    shown = _device_lines(reply, model) if reply.get("answering") is False else []
    print_block([*shown, (args.instance, NOT_READ if read is None else _equalizer_words(read))])
    return 0


def cmd_audio_mix(args: argparse.Namespace) -> int:
    """One source's level or mute, or the stream port's level, written and read back."""
    if (args.level is None) == (args.muted is None):
        print("error: give the stream mix a level or one of --mute and --unmute, one of the "
              "two", file=sys.stderr)
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
        return library_reply(args, a50x.reply_model, command="a50x")

    load = staticmethod(a50x_library.load_library)

    @staticmethod
    def edit(change: Callable[[eq_presets.Saved[a50x_library.Preset]],
                              eq_presets.Saved[a50x_library.Preset]],
             model: a50x.Model) -> eq_presets.Saved[a50x_library.Preset]:
        # Saved's changes give the library of the kind they change: a Library's, a Library.
        return a50x_library.edit(
            cast("Callable[[a50x_library.Library], a50x_library.Library]", change), model)

    path = staticmethod(a50x_library.library_path)

    @staticmethod
    def saved_preset(args: argparse.Namespace, reply: dict, model: a50x.Model,
                     name: str) -> a50x_library.Preset | None:
        """A preset of `model`'s library named `name` of the bands --from's equalizer plays,
        as `reply`, the daemon's, holds them; None once why it cannot be is said."""
        source = args.source or SAVED_FROM
        # Before the read: no read gives an equalizer the model does not have.
        if (refused := a50x.equalizer_refused(model, source, model.equalizers)) is not None:
            print(f"error: {refused}", file=sys.stderr)
            return None
        read = reply["equalizer"].get(source)
        if read is None:
            unread = BANDS_NOT_READ_AWAY if reply.get("answering") is False else BANDS_NOT_READ
            print(f"error: {unread.format(escaped(reply['name']), source)}",
                  file=sys.stderr)
            return None
        try:
            return a50x_library.preset_of({"name": name, "instance": source,
                                           "bands": read["bands"]}, model)
        except ValueError as exc:
            print(f"error: {escaped(exc)}", file=sys.stderr)
            return None

    @staticmethod
    def words(preset: a50x_library.Preset) -> str:
        """The preset's name, its equalizer and each band's frequency and gain."""
        return f"{preset.name}, {preset.instance}: " + bands_words(
            (band.freq, band.gain) for band in preset.bands)

    entry = staticmethod(a50x_library.entry)
    report = staticmethod(_report)


CENTURION_PRESETS: LibraryKind[a50x.Model, a50x_library.Preset] = CenturionPresets()


def cmd_audio_presets(args: argparse.Namespace) -> int:
    """The library of the model the daemon names (presets.run_library): with --pick,
    NUMBER's bands written into the slot its equalizer plays, and the headset's report in
    the list's place; --from without --save-as is refused."""
    if args.source is not None and args.save_as is None:
        print(f"error: {FROM_ALONE}", file=sys.stderr)
        return 1
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
        said = (word_list(setting.names) if setting.names
                else f"{setting.never} or {number}" if setting.never else number)
        takes.setdefault(said, []).append(WORD_OF[key])
    ranges = "; ".join(f"{', '.join(words)}: {said}" for said, words in takes.items())
    put = sub.add("audio-set", help="set one setting of the headset the audio command shows, "
                                    "or put back each setting's first-read value with "
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
    eq.add_argument("--gains", dest="gains_option", metavar="G1,G2,...",
                    help=f"the ten bands' gains, {gain_range(low, high)}, lowest band first, "
                         "each band's frequency kept as read; left out, the equalizer is shown")
    # The ten numbers bare, its form in 1.2.1, still work, left out of the help: headset-eq
    # and g432-headset-eq take --gains. As text, so a word typed for --pick or --gains is
    # refused with those options named.
    eq.add_argument("gains", nargs="*", help=argparse.SUPPRESS)
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
                         "model of the headset the audio command shows, or pick one to write "
                         "its bands into its equalizer")
    saved.add_argument("--save-as", metavar="NAME",
                       help="save the bands that --from's equalizer plays now as a new preset "
                            f"named NAME; at most {eq_presets.PRESETS_MAX} presets in all")
    saved.add_argument("--from", dest="source", choices=list(a50x.INSTANCES),
                       help="the equalizer --save-as saves, the headphones' when left out")
    library_arguments(saved, picks="write its bands into the slot its equalizer plays; "
                                   "G HUB's presets in the headset stay as they are")
    saved.set_defaults(func=cmd_audio_presets)
