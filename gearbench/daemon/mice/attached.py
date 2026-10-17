"""The attached-mouse record, `AttachedMouse`, what one torn down leaves for the next
(`KeptMouse`), the waits for a mouse asleep or missing, and what the mice's controller and
its parts share: a color in flight (`SentColor`), the keys a model's DPI and tuning are filed
under (`dpi_keys`), a game profile's own stages (`own_stages`), a stage's place in an edited
list (`same_place`), what a `mouse_dpi_stages` request asks for (`stage_request`), the
effects `mouse_effect` takes (`effect_names`) and the error with no mouse attached
(`no_mouse`)."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import cast

from ...config import written
from ...evdev import EventReader
from ...hidpp import NO_RULE, Hidpp
from ...lighting import NAME_TO_EFFECT, Cluster, Lighting
from ...mouse import BACKUP_NAMES, Mouse, backup_name
from ...profiles import MouseTuning
from ...spy import ButtonSpy
from ..shared import MOUSE_SILENT_RECHECK, whole_number

# A mouse heard from within this long counts as awake (AttachedMouse.may_be_asleep).
MOUSE_AWAKE = 1.0

# How often a missing mouse is looked for, doubling on each failure up to MOUSE_RETRY_MAX: a
# mouse that is off keeps its receiver's node and never answers the probe.
MOUSE_RETRY = 10.0

MOUSE_RETRY_MAX = 120.0

# The longest the retry waits while a client asks about a missing mouse: a probe of a mouse
# that is off costs the loop 1.5 s, so not one per poll.
MOUSE_POLL_RETRY = 30.0

#: An error for a command that needs a mouse while none is attached.
NO_MOUSE = "no mouse found; switch it on, or plug in its receiver or cable"


def no_mouse(denied: Sequence[tuple[Path, int]]) -> str:
    """The error for a command that needs a mouse while none is attached, given the mouse
    nodes this user may not open (Mice.denied_mice): one there and not granted is not a
    mouse to switch on."""
    return (f"no permission to open the mouse's {denied[0][0]}; {NO_RULE}" if denied
            else NO_MOUSE)


def effect_names(clusters: Iterable[Cluster]) -> list[str]:
    """The effects `mouse_effect` takes on these clusters, sorted: an unnamed id shows as
    effect-0x0013 and the like, which has_effect cannot resolve."""
    return sorted({e.name for c in clusters for e in c.effects if e.name in NAME_TO_EFFECT})


def dpi_keys(product: int) -> list[str]:
    """The `mouse_dpi` and `mouse_tuning` keys of this mouse, its own product id first. One model
    has an id per route (the G502 X PLUS is 4099 on its receiver, c095 on its cable), filed
    together in BACKUP_NAMES, and a DPI chosen on one is the mouse's on the other."""
    name = backup_name(product)
    siblings = sorted(p for p, n in BACKUP_NAMES.items() if n == name and p != product)
    return [f"{product:04x}"] + [f"{p:04x}" for p in siblings]


def own_stages(tuning: MouseTuning | None) -> tuple[int, ...] | None:
    """A game profile's own stage list, or None where it leaves the stages to profile 1."""
    return tuning.dpi_stages if tuning is not None else None


def same_place(dpi: int | None, was: Sequence[int], now: Sequence[int]) -> int | None:
    """The stage of `now` at the place `dpi` held in `was`, or None where `was` does not hold
    it or `now` ends before that place."""
    if dpi not in was:
        return None
    at = was.index(dpi)
    return now[at] if at < len(now) else None


def stage_request(request: dict) -> tuple[list[int], int | None, int | None]:
    """mouse_dpi_stages' stages, default and shift, the last two None when not sent."""
    value, default, shift = request["value"], request.get("default"), request.get("shift")
    command = "mouse_dpi_stages"
    if not isinstance(value, list):
        # Text and an object iterate too: "800" would be the stages 8, 0 and 0.
        raise TypeError(f"{command} wants a list of DPI values, not {written(value)}")
    stages = [whole_number(stage, command) for stage in value]
    return (stages, None if default is None else whole_number(default, command),
            None if shift is None else whole_number(shift, command))


@dataclass
class SentColor:
    """A color or an effect written to an idle mouse without waiting for its replies."""

    color: int
    #: The keys of its replies (Hidpp.answer): each effect write's, then for a color the range
    #: write's and the frame end's.
    replies: tuple[tuple[int, int, int], ...]
    #: Monotonic deadline for them (MOUSE_WRITE_DEADLINE).
    deadline: float
    #: Counted as shown once answered: False for an effect or a drag's preview, or once what
    #: the mouse shows may have changed since the send (AttachedMouse.forget_showing).
    repeat: bool


@dataclass(kw_only=True)
class AttachedMouse:
    """One mouse the daemon drives, and its state. Every attached mouse is driven at once, so
    its buttons work while another's page is on screen; the window shows `Mice.selected`.

    A field without a default is given at the attach.
    """

    dev: Hidpp
    mouse: Mouse
    lighting: Lighting | None
    product: int
    input: list[EventReader] = field(default_factory=list)
    #: Its event node the gearbench group may read and why the last try was refused
    #: (evdev.group_denied), for `problems` (mouse_buttons_no_permission); or None.
    input_denied: str | None = None
    #: Its event node has carried a keystroke in HOST mode since this attach (saw_keystroke),
    #: for `problems` (mouse_keystrokes).
    keystrokes: bool = False
    #: The lighting it was given with sync off, which `reapply_mouse` puts back: a static color,
    #: or an effect with its color. None until one is chosen.
    color: int | None = None
    effect: str | None = None
    #: The feature index of 0x1D4B, where the mouse says it was switched on, or None.
    wireless: int | None = None
    #: The feature index of 0x8100, where it reports its DPI button's stage (STAGE_EVENT), or None.
    onboard: int | None = None
    #: When it was last set up, at attach, by `reapply_mouse` or by host_mode_changed turning
    #: host mode on, monotonic; 0.0 after a link down or a resume, so the next setup is not held
    #: back. The power-on report comes twice in the same instant, and right after a link-up
    #: attach, and must not repeat a setup just done.
    reapplied_at: float
    #: When it last reported being switched on: the report comes in a pair, and one within
    #: REAPPLY_INTERVAL of the one before is not logged.
    switched_on_at: float = float("-inf")
    #: The DPI to go back to when this mouse's held DPI-shift button is released. Per mouse, so
    #: another mouse going away does not lose it.
    dpi_before_shift: int | None = None
    #: It stopped answering: nothing is written to it unasked until it answers, and the loop asks
    #: it at `recheck_at` (MOUSE_SILENT_RECHECK).
    silent: bool = False
    recheck_at: float = 0.0
    recheck_wait: float = MOUSE_SILENT_RECHECK
    #: Its receiver said its link is down (hidpp.LINK_NOTICE): silent, and not asked at a recheck
    #: either, until the link is back, it says anything or a resume asks every mouse again
    #: (recheck_mice).
    link_down: bool = False
    #: Lighting to write again once it answers and no color is in flight (tend_mice): skipped
    #: while it was silent, since no power-on report follows a mouse marked silent while it was
    #: on; asked for while a color was in flight; a send whose answer was lost; a DPI flash's
    #: repaint.
    owed_lighting: bool = False
    #: When its lighting is written again after a DPI flash (after_dpi_write), or None.
    repaint_at: float | None = None
    #: The feature index of 0x0020 ConfigChange (MOUSE_COOKIE); None without it or outside
    #: COOKIE_MODELS.
    config_change: int | None = None
    #: Gearbench's cookie was set after its last setup that took, and nothing has undone that
    #: since: a setup started, a resume, a host mode switch that changes its mode, or a HOST-mode
    #: profile 1 write after which its DPI was not put back or its rate not taken
    #: (put_back_after_write, rate_owed). Only then does a recheck read it back: a cookie left
    #: by an earlier run, or set ahead of a setup that was cut short, would vouch for a setup
    #: that did not take.
    cookie_set: bool = False
    #: Its last setup is unfinished: stopped answering part way, missed at a host mode switch or
    #: a resume, or a put-back after a profile 1 write not done. Once it answers, tend_mice sets
    #: it up again, since no recheck will: one heard from before its recheck gets none, and one
    #: that never stopped answering has none.
    cut_short: bool = False
    #: The static color it is known to show, or None; a drag's preview is not counted. A synced
    #: color it shows is not written again: each write to an idle mouse waits for it to wake
    #: (sync_mouse_color).
    showing: int | None = None
    #: A color sent without waiting whose answer is not in yet, or None. One at a time: a color
    #: asked for meanwhile is owed (check_mouse_write, tend_mice).
    sent: SentColor | None = None
    #: The last reading `mouse` took, MOUSE_READINGS and the onboard mode, or None, and when:
    #: monotonic for its age, wall-clock seconds for the reply.
    reading: dict | None = None
    read_at: float = 0.0
    read_wall: int | None = None
    #: The last battery any reading or report carried, and when, in wall-clock seconds.
    battery: dict | None = None
    battery_at: int | None = None
    #: The feature index of 0x1004 UnifiedBattery, or None. `battery_reports` is set by the
    #: mouse's first battery report; from then on `mouse` never asks for the battery.
    battery_index: int | None = None
    battery_reports: bool = False
    #: When it was attached or last marked answering (mark_answering), monotonic. Later than
    #: `read_at` means the reading may be out of date.
    heard_at: float
    #: Why the last switch to onboard profile 1 did not take, for `problems`, or None.
    profile_error: str | None = None
    #: Its 0x8110 button spy, looked up for a model in spy.SPY_MODELS, or None; HOST mode needs it.
    spy: ButtonSpy | None = None
    #: Counted in HOST mode, only on a mouse with a spy: set before arm_host's HOST write, by
    #: the arm's first mode read finding HOST (_mode_before_arm), and where the mouse may have
    #: been left in it; cleared by that read finding another mode or by a switch to ONBOARD
    #: mode that lands. G4 to G9 reach the daemon as spy masks only while this holds.
    host: bool = False
    #: Why the last arm did not take, for `problems` (mouse_not_host), or None.
    host_error: str | None = None
    #: Its attach's switch to ONBOARD mode went unanswered on a mouse without a spy, which
    #: leave_host cannot hand back: its next setup sends the switch (switch_to_onboard).
    owed_onboard: bool = False
    #: The spy's last mask, and the buttons down in the one gate for both sources (button_edge).
    spy_mask: int = 0
    buttons_down: set[str] = field(default_factory=set)
    #: The wheel tilt held in the spy's mask, -1 left, +1 right, 0 none, and when its next notch
    #: is due, None while none is held: Buttons.tend_tilts and Buttons.deadlines test only
    #: `tilt_at`.
    tilt: int = 0
    tilt_at: float | None = None
    #: The loaded game profile's own stages and rate this mouse was last put on in HOST mode,
    #: None for profile 1's, and Games.loads then: a profile loaded since is told from the
    #: same one edited or read again (retune).
    tuning: MouseTuning | None = None
    tuning_load: int | None = None
    #: The DPI before a game profile's own stages came into force, put back when they go out
    #: of force (desktop_dpi). Set with `tuning` still None while a move onto them is owed, or
    #: after that move's retry was refused, until a DPI is chosen outside them (remember_dpi).
    dpi_before_tuning: int | None = None
    #: The DPI before the load in force, kept when own stages are dropped under it and the
    #: mouse stays on its stage (retune): put back at that load's end, by an unload or a load
    #: with none of its own, and the DPI before the next load's own stages (desktop_dpi).
    #: Cleared when a tuning is taken under another load (take_tuning), a DPI is chosen outside
    #: own stages (remember_dpi), or the DPI outside them is put back (restore_dpi).
    dpi_before_load: int | None = None
    #: The DPI a stage edit under the same load was mapped from, kept while its move is owed:
    #: that move may have landed with its answer lost, so the retry maps from this, not from a
    #: read (retune). Cleared once stages are taken (take_tuning) or out of force (hand_back),
    #: or a DPI is chosen (remember_dpi).
    dpi_before_edit: int | None = None
    #: The stages and rate in force changed while it was not answering: given once it answers
    #: (tend_mice), or by its next setup.
    owed_tuning: bool = False
    #: Its last retune ended at a failed request, a write or its read of the DPI, and no tuning
    #: has been taken since (take_tuning). In HOST mode, on a mouse still attached and not owed
    #: the tuning, tuning_state answers "refused".
    tuning_failed: bool = False
    #: True after a HOST-mode profile 1 write whose DPI could not be read before or put back
    #: after (put_back_after_write), so what the mouse reads may be profile 1's default stage,
    #: no choice. Cleared at the next flash_write.
    dpi_not_put_back: bool = False
    #: The report rate last sent live and answered, or None while unknown. retune compares with
    #: this: one cut short after its rate write leaves `tuning` behind the rate in force.
    live_rate: int | None = None
    #: Its wheel's 0x2121 mode as the last read with high resolution on found it, which a setup
    #: finding high resolution off puts back (keep_wheel), or None before such a read. Not
    #: kept across a replug: the kernel writes the mode again at that connect.
    wheel_mode: int | None = None

    @property
    def name(self) -> str:
        return self.mouse.name

    @property
    def its_spy(self) -> ButtonSpy:
        """`spy` on a mouse that has one. `host` is set only on such a mouse, and Mice's
        setup_host, respy and stop_spy are called only for one: connect_mouse, wants_host,
        host_mode_changed and _setup_onboard check `spy` first."""
        return cast(ButtonSpy, self.spy)

    @property
    def its_lighting(self) -> Lighting:
        """`lighting` on a mouse that has it. Mice's color and effect writes are reached only
        for one: no_mouse_lighting, give_back_lighting and sync_mouse_color check `lighting`
        first."""
        return cast(Lighting, self.lighting)

    def may_be_asleep(self) -> bool:
        """Not heard from within MOUSE_AWAKE, or marked not answering: a write waited for
        could hold the loop for up to 1.63 s, or a timeout's three tries, so the lighting is
        sent without waiting."""
        return self.silent or time.monotonic() - self.heard_at >= MOUSE_AWAKE

    def forget_showing(self) -> None:
        """What it shows may have changed: not known, and a color in flight is not taken as
        shown when its answer comes, since that answer is to a write from before the change."""
        self.showing = None
        if self.sent is not None:
            self.sent.repeat = False

    def drop_sent(self) -> None:
        """Stop waiting for a color in flight, and owe the lighting instead."""
        if self.sent is not None:
            self.dev.forget(*self.sent.replies)
            self.sent = None
            self.owed_lighting = True


@dataclass(frozen=True)
class KeptMouse:
    """What a mouse torn down leaves for the next AttachedMouse of its model, on either route
    (Mice._mouse_kept): its own color and effect, the game profile's own stages and rate it
    was last put on with the load then, and the DPIs from before them and from before the
    load that desktop_dpi puts back. Each is the AttachedMouse field of its name."""

    color: int | None = None
    effect: str | None = None
    tuning: MouseTuning | None = None
    tuning_load: int | None = None
    dpi_before_tuning: int | None = None
    dpi_before_load: int | None = None

    @classmethod
    def of(cls, attached: AttachedMouse) -> KeptMouse:
        """What `attached` leaves, read from its fields of the same names."""
        return cls(**{each.name: getattr(attached, each.name) for each in fields(cls)})
