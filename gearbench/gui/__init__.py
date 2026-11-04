"""GTK4 / libadwaita window for Gearbench.

`main` is what `gearbench-ctl gui` runs. The parts:

- `worker`: the thread that talks to the daemon, the loading guard and the
  settle timer.
- `color`: the color helpers and the picker widget.
- `style`: the shared stylesheet, and helpers the parts share.
- `tips`: per-entry tooltips in a dropdown's popover.
- `animation`: what each key of the drawn keyboard shows at a moment of an animation.
- `unit_switch`: what a switch row on a systemd user unit the window runs shows, and the
  words for an action of the unit's that raised.
- `device_rows`: a device tab's Status and Battery rows, and what they show once it is gone.
- `board`: the drawn keyboard.
- `binding_labels`: a binding in the window's words, and the words of the built-in actions
  more than one part lists.
- `dialogs`: capture a key or choose a macro, record a macro.
- `profile_manager`: the Game profiles dialog and its program picker.
- `import_dialog`: Import from G HUB, and the half of it the pack shares.
- `pack_dialog`: the profiles and macros pack, from the Game profiles dialog and the main
  menu.
- `holds`: what holds a Headset tab row against a poll: a send still settling, a
  slider held down, a write not answered yet.
- `binding_rows`: a row that binds a key or a button, the Mouse tab's buttons' and a
  headset's G keys', and a group of them under its Layer and Editing rows.
- `keyboard_page`, `mouse_page`, `macro_page`: the Keyboard, Mouse and Macros tabs.
- `headset_page`: the Headset tab's page for the Astro A50 Gen 4.
- `save_watch`: that page's Save, followed until the base station's flash write is over.
- `game_card`, `lighting_card`: the Keyboard tab's Game profile group and its lighting
  rows, each a card its page builds.
- `stages_card`, `onboard_card`: the Mouse tab's DPI stages with its report rate, and its
  Onboard profile 1 group, cards likewise.
- `spatial_group`: the Headset tab's Spatial sound group and its Headset for computer sound
  group, the headset pick, which run their programs themselves, never through the daemon.
- `outputs_group`: the Headset tab's Outputs group, Gearbench Game, Chat and Media, its
  balance and its Apps rows, which runs its programs and sends the balance to the Outputs
  unit's socket itself, never through the daemon.
- `input_meter_row`: Input level, the live meter of a headset's microphone that ends each
  headset page's Microphone group, which runs its programs itself.
- `volume_rows`: Game output and Chat output, the volume of a headset's two outputs under
  Balance on the A50's and the A50 X's pages, and Mic input, its microphone's volume above
  Input level on each headset page, which run their programs themselves.
- `noise_rows`: Noise suppression, the switch above Mic input on each headset page for a
  headset whose noise suppression runs on the computer, and Test the microphone under it,
  which run their programs themselves.
- `computer_rows`: those three parts of a headset page, Noise suppression, Input level and
  the volume rows, built together and told of the page's headset, polls and close at once.
- `library_card`: the rows of a headset page's equalizer for its preset library on this
  computer, a Centurion headset's or that of a model of the G432's family with an
  equalizer of its own, read and saved here.
- `headset_cards`: the cards of the G432's page for what a headset of its family holds
  itself, the equalizer in the headset, with its library, and the lights, set through the
  daemon, and its G keys, bound in the profiles.
- `g432_eq_card`: the equalizer of the G432's page for a model of its family with none of
  its own, run here.
- `g432_page`: the Headset tab's page for the Logitech G432 or another headset of its
  family, its sidetone through the daemon, and the equalizer of a model with none of its
  own (`g432_eq_card`).
- `a50x_page`: the Headset tab's page for the Centurion headsets: the Astro A50 X, the
  A50 Gen 5 and the LIGHTSPEED ones, all through the daemon but their saved presets.
- `headset_stack`: the Headset tab, a stack that shows the A50's page, the G432's or
  another headset's by what is attached, and polls each attached but the A50's.
- `presets_dialog`: the A50's page's Load preset to headset dialog, over the preset
  library Gearbench keeps, read and saved here.
- `about`: the About dialog and its debug information.
- `daemon_banner`: the banner while the daemon cannot be used, and its Start daemon, a part
  the window builds.
- `problem_banners`: the uinput, other-tools and config banners, which say the daemon's
  problems, parts likewise.
- `macro_banner`: the banner that names the running macro, a part likewise.
- `extension_banner`: the banner while the Shell extension is not running, a part likewise.
- `polls`: the status, mouse and headset poll timers, a part of the window likewise.
- `main_menu`: the header's primary menu, its Tray icon check and About, a part likewise.
- `lighting_state`: the window's copy of the lighting the Keyboard tab shows and edits,
  what a read of the files makes it, and the keyboard it shows on.
- `window`: the state the tabs read, the banners, and the status read.
"""

from __future__ import annotations

import gi

# Python runs a package before any module in it, so every module here imports from
# gi.repository with these versions already pinned.
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from .window import main  # noqa: E402 -- the window's imports need the pins above

__all__ = ["main"]
