"""The names and sentences of each headset's equalizer that gearbench.g432_eq runs, the G432's
and those of the models of its family with no equalizer of their own (EQ_MODELS): its units',
sink's, nodes' and files' names from the model's key, its description from its name, and each
sentence g432_eq says with those in. Standard library only, never run in the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import pipewire_client, pipewire_unit, runtime_folder, spatial
from .g432 import MODELS, HeadsetModel

#: The models whose equalizer runs here: those G HUB gives none of their own (the owner's
#: decision of 2026-10-21, "In the headset, no cut": "A headset without one keeps the
#: G432's PipeWire equalizer").
EQ_MODELS = tuple(model for model in MODELS if not model.onboard_eq)
#: Each by its key, as the watch's command line names it.
EQ_KEYED = {model.key: model for model in EQ_MODELS}
#: The watch unit's name, by the model's key.
WATCH_NAME_OF = "gearbench-{}-eq-watch"


@dataclass(frozen=True)
class Words:
    """A model's equalizer's names and sentences (`words`). A {} is put in where the sentence
    is said."""

    unit: str
    #: The name in the journal of the unit that watches for the headset's return while
    #: `unit` runs, and that unit.
    watch_name: str
    watch_unit: str
    sink: str
    output: str
    description: str
    conf_name: str
    #: Beside the conf, what switch_on and switch_off keep between them (Record). The runtime
    #: folder goes at a logout, and so does the hold: it is set unsaved, so WirePlumber gives
    #: the headset the level it saved at its next start or plug-in. A PipeWire restart ends
    #: the hold and keeps the file, so its level counts only while the headset is held
    #: (is_held).
    record_name: str
    #: switch_on's refusals: no output of the headset's in PipeWire, its card on a profile
    #: with no output (its input alone, or off; not yet checked on a G432), and a runtime
    #: folder unset or relative.
    no_headset: str
    no_output: str
    no_runtime: str
    #: switch_on's note when the sink could not take the headset's volume, with the cause;
    #: the headset is then not held, so the sink's first volume, 1.0, is never over a headset
    #: at 0 dB.
    not_carried: str
    #: Why there was no sink to set: the unit's process made none in time.
    no_sink: str
    #: switch_on's refusal when the unit stopped as it started, before any pick or hold.
    stopped: str
    #: switch_on's note when the headset's own output could not be held, with the cause.
    not_held: str
    #: switch_off's note when the headset's own volume could not be put back: the cause, then
    #: the level in GNOME's percent.
    not_put_back: str
    #: not_put_back's for a put-back that carried a mute too: the equalizer's, or with its
    #: volume unread the headset's own from before the hold.
    not_put_back_muted: str
    #: Why there is nothing to hold: the headset's card gives no route of the shape
    #: find_route reads.
    no_route: str
    #: The notes for the default output: switch_off's when the equalizer was it, and
    #: switch_on's when the equalizer was it before it went off with the headset's own output
    #: gone or the headset's own output is it.
    moved: str
    not_moved: str
    moved_nowhere: str
    reselected: str
    not_reselected: str
    selected: str
    #: switch_on's note when the headset's own output is the default and no pick of the
    #: equalizer showed: the headset is then not held, so what plays there is never louder.
    not_selected: str
    #: switch_on's note when Gearbench Spatial plays to the headset and did not move to the
    #: equalizer within pipewire_client.SETTLE: the headset is then not held, so spatial
    #: sound, still played straight to it, is never louder.
    spatial_not_moved: str
    #: switch_on's note when Gearbench Spatial's unit or PipeWire could still not be read
    #: after pipewire_client.SETTLE, with the cause: the headset is then not held either,
    #: since its chain may still play straight to it.
    spatial_unread: str
    #: switch_on's note when, after pipewire_client.SETTLE, a node other than the chain's
    #: still plays straight to the headset's own output, as a stream with an output of its
    #: own does: the headset is then not held, so what plays there is never louder.
    played_straight: str
    #: switch_on's note when the unit or PipeWire could still not be read for that look, with
    #: the cause: the headset is then not held either.
    straight_unread: str
    #: switch_on's note when the watch for the headset's return did not start, with the cause.
    no_watch: str
    #: The watch's journal lines, and its unit's description.
    watch_held: str
    watch_unread: str
    watch_ended: str
    watch_back: str
    watch_no_chain: str
    watching: str
    #: update's failure after its save, with the cause: said as saved, so a retry does not
    #: make the change again, as a second --save-as would save a second preset.
    not_set_live: str
    #: render's refusal of the headset's output's node name.
    not_named: str
    #: summary's words while the unit runs: the headset unplugged or with no output, its
    #: output not linked, and linked; each unit's one word for each state, as Gearbench
    #: Spatial's and the noise suppression's.
    waiting: str
    unlinked: str
    ready: str


def words(model: HeadsetModel) -> Words:
    """`model`'s equalizer's names and sentences."""
    key, name, short = model.key, model.name, model.short
    eq, unit = f"{name} Equalizer", f"gearbench-{key}-eq.service"
    watch = WATCH_NAME_OF.format(key)
    again = f"switch {eq} off and on again to try once more."
    put_back = (f"The {short}'s own volume may still be at 0 dB. Putting it back failed ({{}}); "
                "set it to {}%")
    return Words(
        unit=unit, watch_name=watch, watch_unit=f"{watch}.service",
        sink=f"gearbench-{key}-eq", output=f"gearbench-{key}-eq.output", description=eq,
        conf_name=f"gearbench-{key}-eq.conf", record_name=f"gearbench-{key}-eq.json",
        no_headset=(f"the {short}'s output is not in PipeWire: plug in the headset's "
                    f"{model.plugged_by}, then switch {eq} on again"),
        no_output=(f"the {short}'s Configuration in GNOME's Sound settings has no output: "
                   f"pick one that has, then switch {eq} on again"),
        no_runtime=runtime_folder.not_set(f"{eq}'s files have"),
        not_carried=(f"The {short}'s own volume stayed where it was. Setting {eq}'s volume to "
                     f"match it failed ({{}}), so both volumes apply; {again}"),
        no_sink=f"{eq} did not show in PipeWire within {pipewire_client.SETTLE:g} s",
        stopped=pipewire_unit.STOPPED.format(eq, unit),
        not_held=(f"The {short}'s own volume stayed where it was. Setting it to 0 dB failed "
                  f"({{}}), so {eq}'s volume and the {short}'s both apply."),
        not_put_back=f"{put_back} in GNOME's Sound settings.",
        not_put_back_muted=f"{put_back} and mute it in GNOME's Sound settings.",
        no_route=f"PipeWire gives no volume for the {short}'s output",
        moved=f"The {short}'s own output is the default output now.",
        not_moved=(f"The default output did not move to the {short}'s own output. Picking it "
                   "failed ({}); pick an output in GNOME's Sound settings."),
        moved_nowhere=(f"{eq} was the default output. With the {short}'s own output gone, "
                       "PipeWire picks another."),
        reselected=f"{eq} is the default output again.",
        not_reselected=(f"{eq} did not become the default output again. Picking it failed "
                        "({}); pick it in GNOME's Sound settings."),
        selected=f"{eq} is the default output now.",
        not_selected=(f"{eq} did not become the default output. Picking it failed ({{}}), so "
                      f"the {short} keeps its own volume; {again}"),
        spatial_not_moved=(f"{spatial.DESCRIPTION} did not move to {eq} within "
                           f"{pipewire_client.SETTLE:g} s. The {short}'s own volume stayed "
                           f"where it was; {again}"),
        spatial_unread=(f"The {short}'s own volume stayed where it was. {spatial.DESCRIPTION} "
                        f"could not be read ({{}}); {again}"),
        played_straight=(f"Sound still plays straight to the {short}'s own output. The "
                         f"{short}'s own volume stayed where it was; pick {eq} as the output "
                         f"of the app that plays there, then switch {eq} off and on again."),
        straight_unread=(f"The {short}'s own volume stayed where it was. What plays to its own "
                         f"output could not be read ({{}}); {again}"),
        no_watch=(f"Gearbench is not watching for the {short}'s return. Starting the watch "
                  f"failed ({{}}): if the {short} is unplugged and plugged back in, switch "
                  f"{eq} off and on again to hold it at 0 dB."),
        watch_held=f"{watch}: the {short} is back; its own output is at 0 dB again",
        watch_unread=pipewire_client.UNREAD.format(watch),
        watch_ended=(f"{watch}: pw-dump --monitor ended ({{}}), so a {short} plugged back in "
                     "stays at its own volume"),
        watch_back=f"{watch}: the {short} is back. {{}}",
        watch_no_chain=(f"{watch}: {eq}'s unit was not read ({{}}), so it watches for the "
                        f"{short} {spatial.DESCRIPTION} plays to"),
        watching=f"{eq}, watching for the {short}",
        not_set_live=(f"the change is saved, but was not set on {eq} ({{}}); it applies at its "
                      "next start"),
        not_named=f"the {short}'s output '{{}}' cannot be named in the chain's conf",
        waiting=f"on, waiting for the {short}",
        unlinked=f"on, but not linked to the {short}",
        ready=f"on, linked to the {short}")


#: Each model's of EQ_MODELS, by its key, made once: a sentence keeps the waits it names as
#: they were at the import.
WORDS = {model.key: words(model) for model in EQ_MODELS}
