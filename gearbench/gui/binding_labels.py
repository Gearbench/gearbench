"""A binding in the window's words (shown_binding), and the words of the built-in actions
more than one part lists: G-Shift (hold) and the balance's two (SHARED_ACTIONS,
SHARED_TIPS), Nothing, the G-Shift layer's Same as Base and its tooltip, and a balance
word's line while Outputs is off; and the actions the G-Shift layer leaves out
(shift_layer_actions).
"""

from __future__ import annotations

from collections.abc import Iterable

from ..config import BALANCE_ACTIONS, G_SHIFT, NOTHING, macro_name
from ..uinput import shown_spec

#: The actions a key of every kind and a mouse button take, (binding, label), in the order a
#: row and the capture dialog's Actions group list them: G-Shift held while the key is
#: (config.G_SHIFT), and the balance moved a stop toward the game or voice chat
#: (config.BALANCE_ACTIONS).
SHARED_ACTIONS = ((G_SHIFT, "G-Shift (hold)"),
                  *zip(BALANCE_ACTIONS, ("Balance toward game", "Balance toward chat")))
#: The tooltip line of each of SHARED_ACTIONS, by its binding, which a binding row's table and
#: the capture dialog's Actions group for a keyboard's G key look up: a tip found by binding
#: stays on its action whatever order a table lists them in.
SHARED_TIPS = {G_SHIFT: "Hold for the G-Shift layer's bindings.",
               **dict(zip(BALANCE_ACTIONS, ("Moves the balance one stop toward the game.",
                                            "Moves the balance one stop toward voice chat."),
                          strict=True))}
#: The line of a binding row bound to a balance word, and of the word's entry in the capture
#: dialog's Actions group, and a part of a G key's tooltip bound to one (board.key_tooltip),
#: while the Outputs process, which takes the word, does not run
#: (OutputsGroup.balance_unheard).
NEEDS_OUTPUTS = "Needs Outputs"


def outputs_line(bound: str, outputs_off: bool) -> str | None:
    """NEEDS_OUTPUTS for `bound` a balance word while `outputs_off`, else None."""
    return NEEDS_OUTPUTS if outputs_off and bound in BALANCE_ACTIONS else None


def shown_binding(bound: str) -> str:
    """A binding in the window's words: NOTHING_ACTION or one of SHARED_ACTIONS by its label,
    `Macro: name` for a macro, the key spec on one line otherwise."""
    label = dict((NOTHING_ACTION, *SHARED_ACTIONS)).get(bound)
    if label is not None:
        return label
    macro = macro_name(bound)
    return f"Macro: {macro}" if macro is not None else shown_spec(bound)


#: What a key or a row on the G-Shift layer with no binding there says it does with G-Shift
#: held, which the layer does not show: what it does on the base layer, bound or not (the
#: owner's decision of 2026-10-28, each layer showing its own bindings alone).
FALLS_THROUGH = "Does the same as on Base while G-Shift is held"
#: FALLS_THROUGH as SAME_AS_BASE's tip in a binding row's list (binding_rows.put_layer),
#: ending with a period like each other entry's tip there.
FALLS_THROUGH_TIP = f"{FALLS_THROUGH}."
#: The G-Shift layer's choice for a key or button with no binding there, which clears one
#: (FALLS_THROUGH): a binding row's first entry on that layer (binding_rows.layer_actions),
#: and the capture dialog's button in place of Unbind for a keyboard's G key there, beside
#: NOTHING_ACTION, which binds nothing there (the owner's decisions of 2026-10-28).
SAME_AS_BASE = "Same as Base"
#: A binding to nothing, config.NOTHING, in the window's words: the one pair that words it.
#: NOTHING_TIP is its tip on the G-Shift layer: in a binding row's list there
#: (binding_rows.put_layer) and in the capture dialog's Actions group, where a keyboard's
#: G key takes it on that layer alone.
NOTHING_ACTION = (NOTHING, "Nothing")
NOTHING_TIP = "Does nothing while G-Shift is held."


def shift_layer_actions(actions: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """`actions`, (binding, label), as the G-Shift layer offers them: without G_SHIFT, which
    the daemon refuses there (config.g_shift_refusal)."""
    return [pair for pair in actions if pair[0] != G_SHIFT]
