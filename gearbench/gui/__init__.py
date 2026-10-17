"""GTK4 / libadwaita window for Gearbench.

`main` is what `gearbench-ctl gui` runs. The parts:

- `worker`: the thread that talks to the daemon, the loading guard and the
  settle timer.
- `color`: the color helpers and the picker widget.
- `style`: the shared stylesheet, and helpers the parts share.
- `tips`: per-entry tooltips in a dropdown's popover.
- `board`: the drawn keyboard.
- `dialogs`: capture a key or choose a macro, record a macro.
- `profile_manager`: the Game profiles dialog and its program picker.
- `import_dialog`: Import from G HUB, and the half of it the pack shares.
- `pack_dialog`: the profiles and macros pack, from the Game profiles dialog.
- `holds`: what holds a Headset tab row against a poll: a send still settling, a
  slider held down, a write not answered yet.
- `keyboard_page`, `mouse_page`, `headset_page`, `macro_page`: the four tabs.
- `game_card`, `lighting_card`: the Keyboard tab's Game profile group and its lighting
  rows, each a card its page builds.
- `stages_card`, `onboard_card`: the Mouse tab's DPI stages with its report rate, and its
  Onboard profile 1 group, cards likewise.
- `spatial_group`: the Headset tab's Spatial sound group, which runs
  systemctl, pw-dump and pw-cli itself, never through the daemon.
- `about`: the About dialog and its debug information.
- `macro_banner`: the banner that names the running macro, a part the window builds.
- `extension_banner`: the banner while the Shell extension is not running, a part likewise.
- `polls`: the status, mouse and headset poll timers, a part of the window likewise.
- `main_menu`: the header's primary menu, its Tray icon check and About, a part likewise.
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
