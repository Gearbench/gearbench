"""A switch row on a systemd user unit the window runs itself: the unit's state on the row
(`show_switch`, DOTS, UNIT_DOTS), and an action of the unit's that raised, in words for a toast
(`failure_words`, `switch_failure`)."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from .. import pipewire_client, pipewire_unit
from ..pipewire_unit import RUNS_OR_STARTS
from ..settings_files import shown_path
from ..wording import capitalized, reason
from .style import ASLEEP, SAVE_FAILED, SAVE_FAILED_LINE, STARTING, put_dot
from .worker import LoadingGuard

#: A switch's toast line when its failure is too long for a toast, by what it switches.
NOT_STARTED_OF, NOT_STOPPED_OF = "{} did not start", "{} did not stop"
#: The dot for each summary kind of a unit's: a spinner while it starts; a kind missing here
#: is red.
DOTS: dict[str, bool | str | None] = {
    "ready": True, "off": None, "starting": STARTING,
    **dict.fromkeys(("waiting", "unlinked"), ASLEEP)}
#: The dots of the kinds systemd gives a unit that starts and stops, for a group's own DOTS:
#: a spinner while it starts or stops.
UNIT_DOTS: dict[str, bool | str | None] = {
    "off": None, "starting": STARTING, "stopping": STARTING, "stopped": ASLEEP}


def show_switch(loading: LoadingGuard, row: Adw.SwitchRow, dot: Gtk.Image,
                state: pipewire_client.UnitState, summed: tuple[str, str]) -> str:
    """A switch row on its unit's `state` and its summary, (kind, words): the words its
    subtitle, the switch on while the unit runs or starts, but where systemd could not be
    asked, and its dot (DOTS). The kind."""
    kind, words = summed
    words = capitalized(words)
    row.set_subtitle(words)
    if state.systemd is None:
        loading.put_active(row, state.active in RUNS_OR_STARTS)
    put_dot(row, dot, DOTS.get(kind, False), words)
    return kind


def failure_words(error: Exception, feature: str) -> str:
    """A unit's action that raised `error`, in words for a toast: a program's StepError, or a
    ValueError, is a fragment where it is made, capitalized here, as the library card shows
    it; any other error is named by `feature`. The callers word an OSError, a save's, on their
    own."""
    if isinstance(error, (pipewire_client.StepError, ValueError)):
        return capitalized(str(error))
    return f"{feature}: {error}"


def switch_failure(error: Exception, failed: str, feature: str) -> tuple[str, str]:
    """A unit's switch, or a change of the G432 page's presets, that raised `error`, as its
    toast: (the words, the line for words too long for a toast). An OSError comes from the
    runtime folder, where a switch saves (pipewire_unit.switch_folder), and its line names
    that folder; any other error is in failure_words' words, with `failed` as its line."""
    if isinstance(error, OSError):
        return (SAVE_FAILED.format(f"in {shown_path(pipewire_unit.switch_folder())}",
                                   reason(error)),
                SAVE_FAILED_LINE.format("in the runtime folder"))
    return failure_words(error, feature), failed
