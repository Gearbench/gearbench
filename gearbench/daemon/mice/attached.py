"""The attached-mouse record (`AttachedMouse`), what one torn down leaves for the next
(`KeptMouse`), a color in flight (`SentColor`), the waits for a mouse asleep or missing,
and the sentences and helpers the mice's controller and its parts use."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import cast

from ...evdev import EventReader
from ...hidpp import Hidpp
from ...lighting import Cluster, effect_order
from ...mouse import (
    CABLED_IDS,
    GEARBENCH_LIGHTING,
    MODELS,
    OWN_LIGHTING,
    WAITS_TO_CLAIM,
    MouseError,
    key_name,
    model_ids,
    model_key,
)
from ...mouse_device import Mouse
from ...mouse_lighting import MouseLights
from ...profiles import MouseTuning
from ...report import untested, untested_note
from ...spy import ButtonSpy
from ...wording import written
from ..shared import (
    MOUSE_SILENT_RECHECK,
    NODE_DENIED,
    attached_list,
    mouse_model_named,
    whole_number,
)

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

#: Why a game profile's own stages and rate leave a mouse outside host mode alone, for the
#: refusal and the note that say so.
TUNING_HOST_ONLY = "a game profile's own stages and rate are for a mouse in host mode"


def enable_refusal(name: str) -> str:
    """The refusal of the enable write on a model whose event node Gearbench does not read
    (ButtonTable.event_node): its buttons, sent as mouse buttons 11 to 16, would reach nothing
    outside host mode (the owner's decision of 2026-10-21, "All but Enable…")."""
    return (f"nothing was sent: Gearbench does not read the {name}'s event node, so its "
            f"buttons, enabled in its onboard profile, would do nothing outside host mode; in "
            f"host mode, the default, they reach Gearbench without this write "
            f"(`gearbench-ctl mouse-host on`)")


def denied_problem(path: Path, product: int) -> str:
    """NODE_DENIED for a mouse node this user may not open, as Unattached.denied_mice gives it."""
    return NODE_DENIED.format(path, MODELS[product])


def no_mouse(found: Sequence[str]) -> str:
    """The error for a command that needs a mouse while none is attached, given why each
    mouse found is not attached (Unattached.found): one there and not granted, or refused at
    its setup, is not a mouse to switch on."""
    return found[0] if found else NO_MOUSE


def effect_names(clusters: Iterable[Cluster]) -> list[str]:
    """The effects `mouse_effect` takes on these clusters, in every Effect row's order
    (effect_order), the mouse's own sorted, by the names the clusters resolve: an unnamed id
    shows as effect-0x0013 and the like, which has_effect cannot."""
    return effect_order(sorted({e.name for c in clusters for e in c.effects
                                if c.has_effect(e.name)}))


def dpi_keys(product: int) -> list[str]:
    """The `mouse_dpi` and `mouse_tuning` keys of this mouse, its own product id first, then
    its model's other ids (mouse.model_ids): the G502 X PLUS is 4099 on its receiver and c095
    on its cable, and a DPI chosen on one is the mouse's on the other."""
    own = f"{product:04x}"
    return [own, *(key for key in model_ids(own) if key != own)]


def other_route(product: int) -> bool:
    """Whether the model of `product` has a route of the other kind: a cable for an id through
    its receiver, a receiver for an id on its cable (mouse.CABLED_IDS)."""
    cabled = product in CABLED_IDS
    return any((int(key, 16) in CABLED_IDS) != cabled for key in dpi_keys(product))


def own_stages(tuning: MouseTuning | None) -> tuple[int, ...] | None:
    """A game profile's own stage list, or None where it leaves the stages to profile 1."""
    return tuning.dpi_stages if tuning is not None else None


def same_place(dpi: int | None, was: Sequence[int], now: Sequence[int]) -> int | None:
    """Where a mouse on `dpi` goes when the stages go from `was` to `now`: `dpi` itself while
    `now` still holds it, though a stage written below it (place_stages sorts them) has moved
    its place. Else the stage it was on was edited, and it goes to the stage of `now` at the
    place `dpi` held in `was` (the owner's decision of 2026-10-25). None where `was` does not
    hold `dpi`, or `now` ends before that place."""
    if dpi not in was:
        return None
    if dpi in now:
        return dpi
    at = was.index(dpi)
    return now[at] if at < len(now) else None


def follow_shift(attached: AttachedMouse, was: Sequence[int], now: Sequence[int]) -> int | None:
    """After the stages in force went from `was` to `now` under a held DPI shift: the DPI under
    the shift follows the edit (same_place), for the release to put back. The DPI under the
    shift, or None with none held."""
    moved = same_place(attached.dpi_before_shift, was, now)
    if moved is not None:
        attached.dpi_before_shift = moved
    return attached.dpi_before_shift


def stage_request(request: dict) -> tuple[list[int], int | None, int | None]:
    """mouse_dpi_stages' stages, default and shift, the last two None when not sent."""
    value, default, shift = request["value"], request.get("default"), request.get("shift")
    command = "mouse_dpi_stages"
    if not isinstance(value, list):
        # Text and an object iterate too: "800" would be the stages 8, 0 and 0.
        raise TypeError(f"{command} is a list of DPI values, not {written(value)}")
    stages = [whole_number(stage, f"{command}[{at}]") for at, stage in enumerate(value)]
    return (stages, None if default is None else whole_number(default, f"{command}.default"),
            None if shift is None else whole_number(shift, f"{command}.shift"))


def named_in(mice: Sequence[AttachedMouse], text: str) -> AttachedMouse | None:
    """The mouse among `mice` that `text` names: the one with that product id, else the first
    of the model it names by any of its ids or its name (mouse_model_named), so a G502 X PLUS
    named 4099 is found on its cable too; None where none of them is."""
    wanted = text.strip().lower()
    key = mouse_model_named(wanted)
    return next((m for m in mice if f"{m.product:04x}" == wanted),
                next((m for m in mice if model_key(m.product) == key), None))


def asked(mice: Sequence[AttachedMouse], text: str,
          unattached: Callable[[], Sequence[tuple[int, str]]], *, write: bool = False
          ) -> AttachedMouse:
    """The mouse among `mice` that `text` names (named_in), as `--mouse`, `mouse_select` and a
    request's `device` give it. Where none of them is, a MouseError naming the mouse asked
    for, by its model where `text` names one, and saying for a `write` that nothing was
    written, then why where that model was found and not attached (`unattached`, by product
    id: Unattached.found), then the mice attached, or with none why (no_mouse)."""
    found = named_in(mice, text)
    if found is not None:
        return found
    wanted = text.strip().lower()
    key = mouse_model_named(wanted)
    said = (f"no mouse {wanted!r} is attached" if key is None
            else f"the {key_name(key)} ({wanted if wanted in model_ids(key) else key}) "
                 f"is not attached")
    nodes = unattached()
    # The node of the model asked for: another model's is not why this one is missing.
    own = [why for product, why in nodes if model_key(product) == key]
    if mice:
        listed = attached_list(m.label for m in mice)
        if own:
            listed = f"{own[0]}; {listed}"
    else:
        listed = no_mouse(own or [why for _product, why in nodes])
    raise MouseError(f"{said}{'; nothing was sent' if write else ''}; {listed}")


def aimed(target: AttachedMouse, reply: dict) -> dict:
    """A write's `reply`, which names the mouse it went to first (`mouse`, its label) where the
    write was made: with two attached, the one a command without `device` meant is the
    selection's, which the window and another command can move."""
    return {"ok": True, "mouse": target.label, **reply} if reply.get("ok") else reply


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

    A field without a default is given at the attach, but for own_lighting and release_owed,
    which the record sets from its product.
    """

    dev: Hidpp
    mouse: Mouse
    lighting: MouseLights | None
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
    #: Its button spy armed by an arm that ran to its end (Mice.arm_host); False from its
    #: attach, from the start of each arm and of each hand-back. A mouse counted in HOST mode
    #: without it, and not answering, is not driven yet (mouse.host_state).
    armed: bool = False
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
    #: It shows its own lighting, no claim having landed: a mouse that waits to claim
    #: (mouse.WAITS_TO_CLAIM), from its attach until a claim lands (MouseLighting.claim, before its
    #: first lighting write), and again once a release lands (MouseLighting.release). A claim
    #: that failed may have landed all the same (claim_unsure). False on any other mouse, which
    #: is claimed at its setup.
    own_lighting: bool = field(init=False)
    #: A claim failed, and may have landed all the same: its answer lost, or a two-write
    #: claim's second write refused after its first. Until a claim or a release lands, the
    #: mouse counts as holding it: held_lighting answers its lighting, and let_go releases it
    #: (the owner's decision of 2026-10-21).
    claim_unsure: bool = False
    #: A mouse that waits to claim is owed one release from its attach, for a claim an
    #: earlier run left, killed before its stop: sent by its first lighting setup with nothing
    #: to write (MouseLighting.let_go), and due no more once that release has gone out or any
    #: release has landed (MouseLighting.release), as `mouse_effect`'s `release` sends (the
    #: owner's decision of 2026-10-18, "Release at startup", made for the G502 HERO; that it
    #: goes once whether it lands or not, and to the G502 LIGHTSPEED and the PRO WIRELESS, is
    #: Claude Code's call under it, pending the owner's). False on any other mouse.
    release_owed: bool = field(init=False)
    #: When its lighting is written again after a DPI flash (after_dpi_write), or None.
    repaint_at: float | None = None
    #: The journal said, since this attach, that color sync leaves it as it is: it has no
    #: lighting the daemon drives (sync_mouse_color).
    sync_skip_said: bool = False
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
    #: The last reading `mouse` took, MOUSE_READINGS, the onboard mode and the `host` it was
    #: taken under, or None, and when: monotonic for its age, wall-clock seconds for the reply.
    reading: dict | None = None
    read_at: float = 0.0
    read_wall: int | None = None
    #: The DPI the daemon last put it on or read from it (on_dpi), or None: known_dpi's answer
    #: with no reading, since a game profile's own stages put it on a DPI config.json lacks.
    dpi_on: int | None = None
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
    #: mode that lands. Its buttons reach the daemon as spy masks only while this holds.
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
    #: same one edited or read again (retune). Both None once handed back (Dpi.out_of_force),
    #: and the load None while a setup's move onto a load's own stages is not taken
    #: (restore_dpi).
    tuning: MouseTuning | None = None
    tuning_load: int | None = None
    #: The DPI before a game profile's own stages came into force, put back when they go out
    #: of force (desktop_dpi). Kept with no own stages in `tuning` while a move onto them is
    #: owed, or after that move's retry was refused, or while a setup's move off them is not
    #: taken (restore_dpi), until a DPI is chosen outside them (remember_dpi). Followed through
    #: a write that edits profile 1's list (Dpi.follow_kept).
    dpi_before_tuning: int | None = None
    #: The DPI before the load in force, kept when own stages are dropped under it and the
    #: mouse stays on its stage (retune): put back at that load's end, by an unload or a load
    #: with none of its own, and the DPI before the next load's own stages (desktop_dpi).
    #: Cleared when a tuning is taken under another load (take_tuning), a DPI is chosen outside
    #: own stages (remember_dpi), or the DPI outside them is put back (restore_dpi), which
    #: keeps it while the mouse has not taken it. Followed through a write that edits profile
    #: 1's list (Dpi.follow_kept).
    dpi_before_load: int | None = None
    #: The DPI a stage edit under the same load was mapped from, kept while its move is owed:
    #: that move may have landed with its answer lost, so the retry maps from this, not from a
    #: read (retune), and a DPI shift pressed meanwhile holds this as the DPI under it. The
    #: shift's press, release or put-back meanwhile makes it the DPI that write sent
    #: (Dpi.edit_moved). Cleared once stages are taken (take_tuning) or out of force
    #: (Dpi.out_of_force), or a DPI is chosen (remember_dpi).
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
    #: kept across a replug: through a receiver the kernel writes the mode again at that
    #: connect, and on a cable hid-generic writes none.
    wheel_mode: int | None = None
    #: When the loop next reads its battery (MouseBatteries.poll), or None before the loop's
    #: first pass that polls it, which sets it a full interval on, so the passes of its attach
    #: send nothing more. A recheck keeps it, so a mouse whose link drops more often than that
    #: is still read.
    battery_poll_at: float | None = None

    def __post_init__(self) -> None:
        self.own_lighting = self.release_owed = self.product in WAITS_TO_CLAIM

    @property
    def name(self) -> str:
        return self.mouse.name

    @property
    def label(self) -> str:
        """How a reply and a refusal name it among other mice: its name and product id, "G502
        X PLUS (4099)", the id telling its routes, and two of its model, apart."""
        return f"{self.name} ({self.product:04x})"

    @property
    def capabilities(self) -> list[str]:
        """What this build drives on it, for the `mouse` reply and the journal's connect line:
        what the mouse probed (Mouse.capabilities), then `lighting` for clusters that
        enumerate (find_lighting answers None for none)."""
        return self.mouse.capabilities + (["lighting"] if self.lighting is not None else [])

    def connected_words(self, path: Path) -> str:
        """The journal's line for it attached on `path`: its model, its id, what this build
        drives on it (capabilities) and whether it says when it is switched on, and for a
        model not yet run on (report.untested) the note that asks for a device report from
        it."""
        said = (f"{self.name} connected on {path} ({self.product:04x}): "
                f"{', '.join(self.capabilities) or 'nothing this build drives'}; "
                + ("says when it is switched on" if self.wireless is not None
                   else "cannot say when it is switched on (no 0x1D4B)"))
        return f"{said}; {untested_note(self.name)}" if untested(self.product) else said

    @property
    def held_lighting(self) -> MouseLights | None:
        """`lighting` while its claim is held or may be (claim_unsure), the one a stop
        releases; None while it shows its own (own_lighting), or for a mouse without
        lighting."""
        return None if self.own_lighting and not self.claim_unsure else self.lighting

    @property
    def lighting_shown(self) -> str | None:
        """Whose lighting a mouse that waits to claim (mouse.WAITS_TO_CLAIM) shows, OWN_LIGHTING or
        GEARBENCH_LIGHTING; None for a mouse without lighting, and for any other, which shows
        Gearbench's from its setup."""
        if self.lighting is None or self.product not in WAITS_TO_CLAIM:
            return None
        return OWN_LIGHTING if self.own_lighting else GEARBENCH_LIGHTING

    @property
    def sets_rate_live(self) -> bool:
        """Whether `mouse_rate`, with no game profile's own rate to edit, sets this mouse's
        rate live alone (Dpi.live_rate): in HOST mode, on a model whose onboard
        profile is not written (Mouse.flash_refusal)."""
        return self.host and self.mouse.flash_refusal is not None

    @property
    def enable_refused(self) -> str | None:
        """Why the enable write is refused on this mouse, before anything is sent
        (enable_refusal), or None: on a model whose event node is read, one without a button
        table, and one whose writes are not tested, whose own refusal (Mouse.flash_refusal)
        comes first."""
        table = self.mouse.button_table
        if table is None or table.event_node or self.mouse.flash_refusal is not None:
            return None
        return enable_refusal(self.name)

    @property
    def its_spy(self) -> ButtonSpy:
        """`spy` on a mouse that has one. `host` is set only on such a mouse, and Mice's
        setup_host, respy and stop_spy are called only for one: connect_mouse, wants_host,
        host_mode_changed and _setup_onboard check `spy` first."""
        return cast(ButtonSpy, self.spy)

    @property
    def its_lighting(self) -> MouseLights:
        """`lighting` on a mouse that has it: every path to it checks `lighting` first."""
        return cast(MouseLights, self.lighting)

    def may_be_asleep(self) -> bool:
        """Not heard from within MOUSE_AWAKE, or marked not answering: a write waited for
        could hold the loop for up to 1.63 s, or a timeout's three tries, so the lighting is
        sent without waiting, but for a mouse whose lighting claims each effect
        (MouseLighting.send_or_write)."""
        return self.silent or time.monotonic() - self.heard_at >= MOUSE_AWAKE

    def on_dpi(self, dpi: int) -> None:
        """The mouse is on `dpi` as far as the daemon knows: `mouse` answers with it until the
        mouse is next read, which a `mouse` with `ask` false never does, and known_dpi when
        there is no reading."""
        self.dpi_on = dpi
        if self.reading is not None:
            self.reading["dpi"] = dpi

    def on_rate(self, rate: int) -> None:
        """The mouse runs at `rate` Hz as far as the daemon knows: `mouse` answers with it
        until the mouse is next read, which a `mouse` with `ask` false never does."""
        if self.reading is not None:
            self.reading["report_rate"] = rate

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
