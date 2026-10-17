# User guide

This guide covers every control in the window and every `gearbench-ctl`
command. The README has the install, the first-time setup and a short tour.
`man gearbench-ctl` has every option; from a checkout,
`man ./man/gearbench-ctl.1`.

The daemon runs as your user service,
`gearbench.service`, and owns the devices. The window and the command line,
`gearbench-ctl`, send it requests over its control socket. This guide calls
the service the daemon, as `gearbench-ctl` and the window's messages do.

## The window

Open the window with `gearbench-ctl gui`, from Gearbench in the app grid, or
with Open Gearbench in the tray icon's menu. It has four tabs, Keyboard,
Mouse, Headset and Macros, and always opens on the Keyboard tab.

The window shows the daemon's live state, so a change made with an M key or
with `gearbench-ctl` shows in it too. If an M key, a program, the tray
icon's menu or `gearbench-ctl` switches the profile before the window shows
the switch, the daemon refuses a change made in the window in that gap,
since it would land on a profile the window is not showing. A toast, or a
dialog when the message is too long for one, says where the keyboard is now,
and the window moves to that profile. While a game profile stays loaded,
changes go to it, so after a switch of M profile the daemon refuses only a
rename and Set as M default, the two changes that go to the M profile.

While the window is on screen, focused or not, it reads the daemon's state
every 3 s, and the mouse's and the headset's every 5 s; not while it is
minimized, covered, on another workspace or behind the lock screen. It asks
the mouse itself only while the Mouse tab shows in the active window, and
the base station only while the Headset tab does; otherwise it shows what
the daemon last read. The daemon also reads the base station every 5 s on
its own (see "Status and battery").

The main menu, at the top right, has four entries:

- Game profiles… opens the Game profiles dialog (see "Game profiles"),
  whose Import pack… opens the profiles and macros pack.
- Import from G HUB… brings over G HUB's profiles, bindings and macros (see
  "Moving from G HUB").
- Tray icon turns the tray icon on and off (see "The tray icon"). It shows
  once the daemon has answered, since the switch is the daemon's setting.
- About Gearbench has a Troubleshooting page whose Debugging Information row
  opens what a bug report needs, with Copy Text and Save As: the version,
  the daemon's `status` reply and its problems, whether you may open each
  device's hidraw node and `/dev/uinput`, what systemd and PipeWire say of
  spatial sound, and the daemon's last 30 journal lines.

### The device tabs

A device tab shows only the controls the attached device has: a G305 gets
no lighting rows, no button rows and no Shift stage row.

Until the window has seen a device, its tab shows Looking for the keyboard…
(or the mouse, or the headset), then Keyboard not available (or Mouse, or
Headset) with the reason: no device found, a device or receiver that does
not answer, or no permission to open its node, which means its udev rule is
missing or not applied, or another account or a text console has the seat.
While the daemon does not answer, the tab says
Waiting for the daemon. With the window on screen, a mouse can take up to
about 40 s to appear after you switch it on. Once the window has seen a
device, the tab keeps its rows after the device goes, until the window
closes.

Once a device is seen, the tab's first group is named after it. On the
Keyboard and Mouse tabs, its Status row has a green check mark while the
device is there and a red error sign once it is gone or while the daemon
does not answer. Once a device the daemon has reached stops answering, the
row has an amber warning sign and says what to do: press a key on the
keyboard, move the mouse, or switch it on. A device the daemon has not
reached, such as one switched off when its receiver was plugged in or the
daemon started, shows the daemon's reason instead: amber for the keyboard,
and a red error sign for the mouse, even with its receiver plugged in. The
row says On its cable or Through its LIGHTSPEED receiver, with the
`/dev/hidraw` node in its tooltip.

The Battery row gives a percentage and a state, amber at 30 % and red at
15 % while not charging. The G305 reports only a coarse level, shown as
"about 90%". The keyboard reports only its voltage, and its percentage comes
from Logitech's own curves for the G915, one on battery and one charging.
The charging curve ends at 91 %, so above 4.10 V while charging it reads
"charging, nearly full", with no number, and `gearbench-ctl status` prints
the voltage beside it. The G502 X PLUS's battery is the last level it
reported by itself, or the level read before its first report; the G305's is
the level last read from it. A mouse that does not answer shows its last
reading with the time it was taken, and the date too when that was not
today. The rows that write to it are grayed out until it
answers. A mouse that has gone, or one
the daemon cannot be asked about, shows no reading.

While the keyboard is away or not answering, a banner says Changes are saved
and will show when the G915 is back.

The Headset tab's first group belongs to the base station: a green check
mark with Headset on, an amber warning sign with Headset off or out of range
or with Not answering, and a red error sign with the reason once the base
station is gone or while the daemon does not answer. The rows that change the
base station's settings are grayed out while it does not answer or while it
saves. See "Status and battery" under "The headset".

### Banners and messages

A banner under the header stays up while one of these lasts:

- The daemon is not running. The banner says what G1 to G5 type while it is
  down, or that they may type nothing if it was killed, and has Start it,
  which runs `systemctl --user start gearbench`;
  after a failed start it gives what systemctl said, or says that systemctl
  did not finish in 30 s, or that the daemon exited at once or did not listen
  within 5 s.
- The daemon runs and did not answer in time, busy with a mouse profile
  write, for example, or sent a reply that could not be read. There is no
  button, since starting it again would do nothing.
- The control socket cannot be used, as when another user owns it. The
  banner gives the error.
- No virtual keyboard or mouse: the daemon could not open `/dev/uinput`.
  Its udev rule is missing (README, "Install"), or the rule is installed and
  has not reached the node yet (README, "First-time setup").
- Another program that drives these devices is running: the banner names
  each one found, with its process id and what to do. Once it is gone,
  `systemctl --user restart gearbench` sets each device up again.
- `config.json` or the loaded game profile's file could not be read, or one
  of them or a macro's file was edited on disk. The banner has Reload….
- A macro is running or counting down to its start. The banner has Stop.
- Game profiles follow focus without Gearbench's GNOME Shell extension. The
  banner says what they follow without it, only X11 windows or, with no X
  server, the running programs, and why it is not running: off, with Turn
  on, which asks GNOME Shell to enable it; not loaded until you log out and
  back in; failed, with GNOME Shell's error; not made for this GNOME; or
  GNOME's extensions switched off, which the Extensions app turns on. A Turn
  on that GNOME Shell refuses stays on the banner with its reason.

The Mouse tab has a banner of its own, a line for each problem the daemon
lists for a mouse: an onboard profile other than 1 running, host mode that
did not take or left G4 to G9 dead, G4 to G9 unreadable from the mouse's
event node, keys arriving on the G502 X PLUS's event node in host mode
(see "The mouse"), and, while the tab shows a mouse, another mouse whose
node Gearbench has no permission to open.

A refused change comes back as a toast when it fits on one line, and as a
dialog when it does not. A change refused because `config.json`, the loaded
game profile's file or a macro's file was edited on disk, or one of the
first two could not be read, goes on the banner with Reload… instead; while
the Game profiles dialog is open, which covers that banner, it also comes
back over the dialog as a toast or a dialog. A device failure is said in
plain words, with the raw error behind Details on a toast and shown under
the words in a dialog. A note too long for a toast, such as the one a mouse
write gives in host mode, shows its first sentence or a short summary, with
the whole note behind Details. A dropdown entry that needs explaining has a
tooltip.

Closing the window while a change is still on its way, such as a headset
setting, a mouse write, a color, a macro save or a Spatial sound action,
waits for it; closing again closes at once. A change refused then keeps the
window open, with the reason shown and the row put back, and the next close
asks again about unsaved macro changes, even those you chose to discard. A
change the daemon never got, because it is not running or its socket could
not be used, keeps the window open too, under the banner that says so. A
Spatial sound action that fails then does not keep the window open. Macro
changes made while it waits are asked about before the window closes,
unless a macro save or delete is still on its way.

### The color picker

The Keyboard and Mouse tabs use the same color picker, in the Color row
(Brush in Per-key mode). Click the row to open it: a square for saturation
and value over a strip for hue, then the Hex row, and on the Keyboard tab
the Saved colors row. As you drag, the color shows live on the mouse, and on
the keyboard in Static mode. A keyboard color is saved when you let go, the
Brush only with the keys it paints.

From the keyboard, once the row is open, Tab to the square or the strip. In
the square, Left and Right move the saturation and Up and Down the value; in
the strip, the arrow keys move the hue. Each press moves 1 %, or 10 % with
Alt. The strip also takes Page Up and Page Down for 10 %, and Home and End
for its two ends. A run of presses is one change, made when you pause, Tab
away or close the window. A screen reader reads the square and the strip as
sliders, with their values in words.

The Hex row takes six hex digits, `#0080ff` or `0080ff`; three digits,
`#fff`, are refused.

On the Keyboard tab, + in the Saved colors row keeps the current color, and
a click on a saved color applies it. The row holds 24 colors and drops the
oldest; a `config.json` edited by hand to list more does not load. To remove
one, right-click it, or focus it and press Delete, Backspace, the Menu key or
Shift+F10; the window asks first.

## The keyboard

![The Keyboard tab](images/keyboard.png)

The Keyboard tab, from the top: the G915 group with Status and Battery; the
M profile group, with Active, Name and Reload from disk (see "Files"); the
Game profile group (see "Game profiles"); the Lighting group's Mode row,
with Speed under an animation and Direction under Wave; the drawn keyboard,
with Map G keys; then the color picker with its Saved colors row, Groups,
Background, Brightness, and the switches and times of "Idle, lock and
suspend".

The keyboard on its receiver is set up again at once when it is switched on,
and moves to its cable when that is plugged in.

### M profiles

M1, M2 and M3 on the keyboard switch between three M profiles, and the M
LEDs show which one is active. Each M profile has its own lighting, G-key
bindings, MR layer and mouse button bindings. On the Keyboard tab, the
Active row switches the M profile and the Name row renames the one shown;
`gearbench-ctl profile 2` does what M2 does. The daemon saves the active M
profile, and a game profile loaded by hand, as they change, so logging in
restores both. A game profile loads on top of the M profile; see "Game
profiles". An M profile's name cannot hold a control character (Unicode
category Cc, such as ESC or BEL) or run past 1,000 characters: the rename is
refused with the reason, and a `config.json` holding such a name does not
load.

### G keys and the MR layer

G1 to G5 ship unbound. To bind one, turn on Map G keys over the drawn
keyboard. Each G key then shows its binding under its name, `Macro: name`
for a macro, when the drawn keyboard is big enough to fit it; in a narrow
window the key shows its name alone. Its tooltip always has the whole
binding, and hovering M1 to M3, MR, Game, BT or Host says what that key
does. Click a G key for the Press a key dialog:

- Press what the key should send. Nothing is bound until you choose Set, so
  after a slip you can press again. Escape closes the dialog. A key still
  held as the dialog closes does nothing in the window until you let go of
  it.
- The dialog records the physical key, whatever your layout, Space and Enter
  included.
- Modifiers held while a key goes down are part of its binding, `ctrl+f15`,
  spelled the same for the left or the right modifier. Other keys held down
  together bind together, in the order pressed: hold A, hold B, then press
  C, and the binding is `a+b+c`. A key let go of before the last one goes
  down is left out.
- A modifier tapped alone is the binding by itself and keeps its side:
  `shift`, `rctrl`. Two or more modifiers pressed together and released are
  the binding, each keeping its side, in the order Ctrl, Shift, Alt, Super:
  `ctrl+shift`.
- GNOME Shell keeps the release of a Super tapped alone for its Activities
  overview, so the tap binds nothing. Pick Super alone from the list.
- Choose from a list… lists F13 to F24, the media keys, the modifiers alone,
  then the main keys, the navigation keys and the keypad. GNOME takes F13
  (Settings), F20 (mic mute) and F21 to F23 (touchpad keys), and the list
  says so; F14 to F19 and F24 are free. Each key shows its label over the
  name a binding uses, Print Screen over `sysrq`, and the search finds
  either. The Ctrl, Shift, Alt and Super boxes go with the key you pick,
  except a box for that key itself: Shift with Left Shift gives `leftshift`.
  The list is how the window binds Escape and Super alone; `gearbench-ctl
  bind` binds the kernel's other key names.
- Use a macro… binds the key to a macro. Back returns to Press a key.
- Unbind clears the key's binding.

From the command line: `gearbench-ctl bind G1 volumeup`.

MR is a shift layer while you hold it, with its LED lit: a G key sends its
MR binding, or its base binding when it has no MR binding. The window has no
editor for the MR layer; bind it with `gearbench-ctl bind G1 f15 --shift`.

### Binding syntax

A binding is a key name with optional modifiers: `f15`, `volumeup`,
`ctrl+shift+f5`, `rsuper+f15`. A modifier alone is a binding too: `shift`,
`ctrl`, `alt`, `super`, or the right-hand `rshift`, `rctrl`, `ralt` and
`rsuper`. So are several keys, `a+b+c` or `ctrl+a+b`: the keys go down in
the order the binding names them and come up in reverse. Once Print Screen
goes down with Alt held, a key other than Alt, Shift or Print Screen is left
up until Alt comes up, in a binding or a macro, such as the `b` of
`alt+sysrq+b`: Linux would take it as a magic SysRq command, and on Ubuntu
`b` restarts the computer at once, without saving your work. The journal
names the key left up. Alt with Print Screen alone still goes through. A G
key or mouse button bound to `alt+sysrq+b`, or a macro's `key_down` step of
it, still holds Alt and Print Screen, but a macro's `key` step of it presses
none of its keys. A binding
names each key once, so `a+a` is refused, and a `+` needs a key on each side,
so `ctrl++` is refused too: the plus key is `shift+equal` on a US layout, or
`kpplus` on the keypad. `macro:NAME` runs a macro, and is refused when the
name holds a control character or runs past 1,000 characters. `none`,
`clear` or `off`, in any case,
clears a G key with `gearbench-ctl bind` and a mouse button with
`gearbench-ctl mouse-bind`.

No name binds the Clear key, `key_clear` included, since it is too easily
confused with `clear`. A `config.json` or game profile file that binds it
does not load, and neither does one holding any other binding the command
line refuses. A binding in either file goes in quotes: `"G1": "5"` binds the
5 key, and a file with `"G1": 5` does not load. In a file, a G key with no
binding is `null`; `none`, `clear` and `off` unbind on the command line
only. A file that gives one of them to a G key does not load, and neither
does one that gives a mouse button `clear` or `off`: `none` is a mouse
button's action, G9's by default.

Key names are the kernel's, from a copy of its key table taken from Linux
7.0. Where `/usr/include/linux/input-event-codes.h` is installed (package
`linux-libc-dev`), it adds the names a newer kernel defines; bindings and
macros do not need it. `gearbench-ctl keys` lists them, `gearbench-ctl keys
volume` the ones that match, and `gearbench-ctl keys --modifiers` the
modifiers. The list spells a name as the kernel does, `KEY_VOLUMEUP`; a
binding takes it in any case, with or without the `KEY_`, so `volumeup` is
the same key. The same bindings and macros work on the mouse's G4 to G9.

### How the keys behave

- A G key bound to a key holds it for as long as you hold the G key, so it
  can walk, crouch or push to talk. One bound to a macro follows the macro's
  Repeat mode (see "Repeat modes and limits"): a Once macro plays to its end
  and a Toggle macro ignores the release. A G key still held when you press
  MR or an M key still releases what it pressed.
- A key held by two things at once stays down until the last of them
  releases it: G1 bound to `shift` while a macro types a capital, for
  example, or two G keys.
- A screen lock, GNOME's screen blank, a suspend, or another account or a
  text console taking the screen releases whatever a G key holds, and the MR
  layer and its LED. A G key or MR still held through it does nothing until
  you release it and press it again.
- The Game key is the keyboard's own Game Mode, which Gearbench does not
  touch. Game Mode turns off both Super keys and the Menu key: if Super does
  nothing, the Activities overview included, press the Game key again.
- With the daemon stopped, the keyboard runs its onboard profiles, and G1 to
  G5 type F1 to F5 on M1, 1 to 5 on M2 and 6 to 0 on M3. If the daemon was
  killed, they can be left typing nothing.

### Lighting

The Mode row picks what the keyboard shows: Static, one color for the whole
keyboard; Per-key, keys painted one by one; or one of the keyboard's own
animations, Breathe, Cycle, Wave and Ripple. The lighting holds only while
the daemon runs. When it stops, the keyboard goes back to its own lighting,
except at a shutdown or reboot: then the daemon turns its lights off (see
"Idle, lock and suspend").

`gearbench-ctl effect` takes `static`, `breathe`, `cycle`, `wave`, `ripple`
or `disabled`. `gearbench-ctl effect static` brings back whichever of Static
and Per-key the profile was on. `gearbench-ctl effect disabled` turns the
keyboard black, with a Per-key layout's painted keys still showing, and the
window draws the profile's Static color and background as black. Choosing
Breathe or Ripple then runs the animation in the profile's stored Static
color and moves the picker to it; Static then comes back in that color.

An animation shows a Speed slider, slower to the left. Its subtitle is the
length of one cycle in milliseconds, as `gearbench-ctl effect --speed` takes
it: 1000 to 20000 for breathe, cycle and wave, and 100 to 1000 for ripple.
The defaults are 3000 for breathe, 2100 for cycle and wave, and 1000 for
ripple. An effect set without `--speed` runs at the speed saved for it, or
the default.

Wave also has a Direction row: Left to right, Right to left, Top to bottom,
or From the center out (`--direction horizontal`, `reverse-horizontal`,
`vertical` or `center-out`), horizontal until you choose one. Breathe and
ripple run in the Static color, which the picker sets. Cycle and wave have
no color, and the picker is grayed out. The logo waves with the keys under
wave, and stays steady in the Static color under ripple. Each profile saves
its mode, each animation's speed and wave's direction.

### Painting keys

In Per-key mode the drawn keyboard is editable, and the picker is titled
Brush:

- Left-click a key to paint it, and right-click to clear it back to the
  background. Drag to paint every key you cross, and right-drag to clear
  them. Each key lights on the keyboard as the pointer reaches it, and the
  drag is saved once, when you let go.
- Groups paints a set of keys in one click: WASD, Interface (Tab, M, Esc and
  Enter), Combat (E, F, R, V, Q and G), Numbers, F-keys, G-keys, Modifiers,
  Arrows, Keypad, Media, Logo, and All keys, which is every key with a light
  but the logo.
- Sample gives the brush the color of the next key you click: the key's own
  color, or the background for an unpainted key. That click paints nothing,
  and Sample turns itself off after it. Escape or a second click on Sample
  cancels it. While it waits, no click, drag or key clears a key.
- Sample and Groups show in Per-key mode only, and not while Map G keys is
  on.
- Background sets what an unpainted key shows, apart from the brush. Use
  brush color gives it the brush's color, and Black leaves unpainted keys
  unlit.
- The row over the drawn keyboard says which profile the edits go to.

From the keyboard, the drawn keyboard is one Tab stop. The arrow keys move
between keys, and Space or Enter does what a click does. Shift with the
arrows paints the key you start on and each key you move to, as a drag does,
saved when you release Shift or move the focus off the drawn keyboard. With
Sticky Keys on, Shift and then one arrow paints two keys, saved as you
release the arrow. Delete, Backspace, the Menu key and Shift+F10 clear the
focused key. A screen reader reads the drawn keyboard as a group named
Keyboard, and each key gives its name and its color, or with Map G keys on,
what its tooltip says.

Static and the animations hide the painted keys and the background, and keep
both: switch back to Per-key and the layout comes back, after a restart too.
Static comes back in its own Static color. `gearbench-ctl color` sets the
background in Per-key mode and the Static color otherwise. Under cycle, wave
or `disabled` the keyboard does not show it. `gearbench-ctl config` shows
each M profile's mode, how many painted keys it holds, and its background
when that differs from its Static color.

### Brightness

The Brightness row, or `gearbench-ctl brightness`, sets the backlight,
rounded down to a level the firmware keeps: 0, 12, 25, 50 or 100 %. The
keyboard's own brightness key works too, and the daemon saves the level it
sets. A mouse synced to the keyboard follows the brightness (see "Lighting
and color sync").

## Game profiles

A game profile is one JSON file in `~/.config/gearbench/profiles/`, loaded on
top of the active M profile. Until it is unloaded, it replaces the lighting,
the G-key bindings with their MR layer, and the mouse button bindings. On a
G502 X PLUS in host mode, DPI stages or a report rate of its own replace
onboard profile 1's too (see "Stages and rate per game profile"). The M keys
keep switching M profiles as before.

A file that cannot be read costs only that profile: the window lists it in a
dimmed row, with the reason. Two files can hold one name, in any case and
with punctuation and symbols counted as spaces (C and C++ are one name), as
when a file manager makes a copy; in a name with no letter or digit they
count as themselves (!! and ?? are two names).
The file named for the profile is read
(`doom.json` for Doom), or else the first by file name, and the other is
listed dimmed, naming the file read instead. Give the profile in the other
file a name of its own and it loads as a separate profile, or remove that
file. A profile loaded from a copy while `doom.json` was missing moves to
`doom.json` within 5 seconds of it coming back, and an edit in those seconds
is refused.

While a game profile is loaded, the window and the editing commands edit it
and save its file. The editing commands are `color`, `effect`, `bind`,
`mouse-bind` and `mouse-defaults`, and for a G502 X PLUS with host mode
switched on, whether or not it took, `mouse-dpi-stages` and `mouse-rate`
(see "Stages and rate per game profile"). The Keyboard and Mouse tabs say
which layer they edit: `Editing Doom, loaded for DOOMEternalx64vk.exe`,
`Editing Doom` for one loaded by hand, or `Editing M1 · Profile 1`.

The Keyboard tab's Game profile group, from the top:

- Loaded chooses the profile in force (`gearbench-ctl load`, `gearbench-ctl
  unload`). Its list opens with a search box that matches any part of a
  name, in any case: `raiders` finds Arc Raiders. While the list is open it
  does not change; a profile added or loaded meanwhile shows once it closes.
  It also lists a loaded game profile whose file could not be read or is
  gone, so it can be unloaded here. While the loaded profile's file will not
  load, the Keyboard tab keeps the lighting it showed, and the window sends
  no binding, keyboard color or keyboard effect change, Save as… or Set as
  M default, and says why; Map G keys shows no bindings. Loaded and Reload
  from disk still work.
- Keep loaded shows while a game profile is loaded; see "Hold-offs and Keep
  loaded".
- While focus is followed and a game profile names a program, a row says
  which profile is active and why (see "Following the game in front").
- Auto-load lists the loaded profile's programs, with Edit….
- During a hold-off, a row says so, with Load it now (see "Hold-offs and
  Keep loaded").
- Save as…, on the Save row (`gearbench-ctl save-profile`), saves what shows
  as a game profile, and the window then loads it. A name another game
  profile has asks before writing over it, and names the programs that load
  it. A profile saved from an animation runs the same animation at the same
  speed; one saved from `disabled` has a black Static color and background.
- Set as M default, on the Save row (`gearbench-ctl promote`), writes the
  loaded game profile's lighting, in its mode and with its painted keys and
  both colors, and its G-key, MR and mouse bindings into the active M
  profile, then unloads the game profile. The game profile's own DPI stages
  and rate stay with it, since an M profile has none.
  With no game profile loaded, it saves the active M profile as it shows.

Manage…, beside the group's heading, opens the Game profiles dialog.

### Following the game in front

A profile can name programs. It is active while a window of one of them has
focus, so with two games running, the one in front shows its profile. With
focus anywhere else, on the desktop or on a program no profile names, the
keyboard shows what it showed before: the M profile, or a profile loaded by
hand. Focus counts once it has stayed on a window for 0.3 s, or 1 s after
the first of a run of changes, so an Alt-Tab through several windows within
a second repaints the keyboard once.

Focus comes from Gearbench's own GNOME Shell extension, which sees every
window, native Wayland ones included. It reports the focused window's
process id only to the daemon `gearbench.service` runs, so a daemon you run
by hand in a terminal follows focus on Xwayland. `install.sh` installs and
enables the extension, and GNOME loads it at your next login. If GNOME's
Extensions switch is off, or its settings allow no extensions from your home
folder, `install.sh` says so: the extension does not run until that changes,
and turning the switch on starts your other extensions too. If the settings
lock which extensions run, `install.sh` says so and cannot enable the
extension; once they no longer lock it, run `install.sh` again or the
`gnome-extensions enable` command below. With the Debian package, log out
and back in once: the window's banner then has Turn on, which enables it, as
this command does:

```
gnome-extensions enable gearbench-focus@wmuschetto.github.io
```

While the extension is off, the daemon reads focus from Xwayland, which X11
programs draw into, and Proton games too unless they run Wine's Wayland
driver (GE-Proton's `PROTON_ENABLE_WAYLAND=1`); a native Wayland window then
counts as no program.
`gnome-extensions info gearbench-focus@wmuschetto.github.io` says why the
extension is off. `Enabled: No` needs the command above. A `State:` of
`ERROR` means GNOME could not load it, and `journalctl --user -b -t
gnome-shell | grep -i gearbench` shows the error. `OUT OF DATE` means your
GNOME is not version 50, the only one the extension is marked for.
`doesn't exist` means GNOME has not found it: either it was installed after
your login, so log out and back in, or `install.sh` put it in your home
folder and GNOME's settings allow no extensions from there.
With neither the extension nor Xwayland, profiles
follow running programs: a profile loads when its program starts and hands
back when it exits.

`gearbench-ctl status` names the focused program on its `focus` line, with
the profile that names it, if any. When no program has focus, the line reads
`no program` under the extension and `no X11 program` under Xwayland. Where
focus is not followed, it reads `not followed` and says game profiles load
for running programs. `gearbench-ctl status --json` always gives the source
as `focus.source`: `gnome-shell`, `xwayland` or `processes`.

A program's name matches whole, in any case, with `.exe` optional:
`r5apex_dx12` and `r5apex_dx12.exe` both match `r5apex_dx12.exe`, and
`chrome` matches Chrome but not Discord's `chrome_crashpad_handler`. A
Proton or Wine game matches by its Windows `.exe`. A profile loads for at
most 64 programs, and a program's name is at most 255 bytes, the longest a
file name can be: the dialog and `profile-programs` refuse more, and a
profile file past either does not load. The journal names the program at
each change.

### The Game profiles dialog

![The Game profiles dialog](images/game-profiles.png)

Game profiles… in the main menu, or Manage… beside the Game profile heading,
lists every game profile. The filter at the top shows the profiles whose
name or programs hold what you type, in any case: `doom` finds Doom, and
`r5apex` finds the profile that loads for `r5apex_dx12.exe`. Open one to see
the programs that load it, remove a program (the dialog asks first), type
one into Add by name, or pick one from Add a running program.

Adding or removing a program loads and unloads nothing by itself: within
5 s the daemon checks again, loads the profile if one of its programs has
focus, or is running where focus is not followed, and unloads a profile a
program loaded if none of its programs does. When another program on the
list still matches the one you remove, such as `DOOM` beside `doom.exe`, or
a name Linux cut to its first 15 bytes beside the full one, the dialog names
it, since the profile still loads for that program.
`gearbench-ctl profile-programs NAME [PROGRAM...]` sets the list, and
`gearbench-ctl apps` lists what you are running.

A game started from Steam, Lutris, Heroic, Bottles or itch is listed under
its own name while it runs, with `Running in Steam` (or the launcher's name)
under it; a Proton or Wine game is listed by its `.exe`. Adding a launcher
itself, picked or typed, asks first, since the profile would then load for
the launcher and not the game. `profile-programs` and `save-profile --process`
warn and set it anyway.

The Name row renames a profile, as `gearbench-ctl rename-profile OLD NEW`
does. Everything that named the profile follows, and nothing is loaded or
unloaded. A name another game profile has, in any case and with punctuation
and symbols counted as spaces (`Doom: Eternal` is `Doom Eternal`; a name
with no letter or digit keeps its symbols), is refused. So is renaming the
loaded profile after its file was edited by hand, until Reload from disk
takes the edit or `gearbench-ctl save` writes over it (see "Files"). A
profile that is not loaded is read from its file at the rename, so a hand
edit goes with it to the new file. A profile's name and its programs cannot
hold a control character (Unicode category Cc, such as ESC or BEL) or a lone
surrogate; emoji and combining marks are fine. A file with one does not
load, and neither does a file whose profile's name runs past 1,000
characters, a name a rename refuses too. The white space at a name's two
ends, a tab or a newline included, is cut off before these checks. Delete
profile removes the file, as
`gearbench-ctl delete-profile NAME` does; deleting the loaded profile
unloads it, and the confirmation says so.

### The profiles and macros pack

Gearbench comes with a pack of game lighting profiles and macros, and
imports nothing from it until you ask. It holds per-key lighting for 67
games, among them Baldur's Gate 3, Counter-Strike 2, Cyberpunk 2077, Elden
Ring, Helldivers 2 and Stardew Valley, and 10 macros; `gearbench-ctl
import-pack --list` shows what yours holds.

The pack's profiles light the keys each game binds by default, in one
scheme: movement red, abilities and actions purple, weapon and item slots
orange, menus, map and inventory blue, chat and pings cyan, modifiers
green, function keys and G1 to G5 yellow, anything else pink, on a dark
gray background. A few keep colors of their own, such as No Man's Sky's
white L, Y and Z. A modifier with a job of its own takes that job's color,
except movement: a Shift, Ctrl or Alt that sprints or crouches stays green.
Rebind a key in the game and its color stays where the default was; paint
it on the Keyboard tab after loading the profile.

Import pack…, at the top left of the Game profiles dialog, lists the pack's
game profiles and macros, each one ticked. A game profile's line says what
its lighting is, and opening it shows Load for and the programs that load
it. A macro opens to show every step, one a line, named as the macro editor
names them. The filter narrows both lists by name or program, and Select
all and Select none act on what it shows. A file in the pack that Gearbench
cannot read, that holds more than lighting, or that repeats another file's
name is listed under Broken, never imported.

Next shows what will be written, as the G HUB import does. A pack profile is
lighting only: it brings no G-key bindings, MR layer, mouse buttons, DPI
stages or report rate. Where you have a game profile or macro of the same
name, in any case and with punctuation and symbols counted as spaces (a name
with no letter or digit keeps its symbols), the review page asks about each
one. Keep mine, the default, leaves yours as it is. Use the pack's writes
the pack's lighting over your game profile, and the pack's programs while
Load for is ticked, and leaves your bindings, MR layer, mouse buttons, DPI
stages and report rate; for a macro, it writes the pack's macro over yours.
A game profile of yours with the same lighting, or a macro with the same
steps and repeat mode, is already there, and nothing is asked or written for
it. The daemon writes the files, so it has to be running.

A pack profile loads for the programs the pack names for its game, such as
`r5apex_dx12.exe` and `r5apex.exe` for Apex Legends. Untick Load for to load
it by hand; then one that replaces your profile keeps your programs. A
program one of your game profiles already loads stays with yours, and the
review page says which.

The pack's 10 macros:

| Macro | Runs | What it does |
| --- | --- | --- |
| Rapid fire | while held | clicks the left button 10 times a second |
| Auto-click | until pressed again | clicks the left button about every 0.65 s |
| Say gg in chat | once | presses Enter, types gg, presses Enter |
| Autorun | until pressed again | holds W |
| Skill chain 1-2-3 | once | presses 1, 2 and 3, 1.5 s apart |
| Skill 1 turbo | while held | taps 1 ten times a second |
| Bunny hop | while held | taps Space 20 times a second |
| Anti-AFK | until pressed again | taps W, then S, once a minute |
| Hold right click | until pressed again | holds the right button |
| Helldivers 2: Reinforce | once | holds Ctrl and enters W, S, D, A, W |

A game's own rules decide whether a macro is allowed there, so above the
macros the window and `gearbench-ctl import-pack --list` say "Use at your
own risk: some games forbid macros." Games that forbid every macro include
Arc Raiders ("Using macros", Nexon's Terms of Service, effective
2025-09-21), Marvel Rivals ("Hardware macros", its Prohibited Software &
Hardware List, 2025-02-26) and Escape from Tarkov (Battlestate Games on X,
2018-05-02). Rules found for other games:

| Game | Rule | Source |
| --- | --- | --- |
| World of Warcraft | one action per key press; no repeat while held | Blizzard forum posts, 2008-05-29 and 2022-11-25 |
| Final Fantasy XIV | no automated or absentee play; turbo keys risk suspension | Square Enix Support Center; forum reply, February 2012 |
| Path of Exile | no macro that does more than one action or runs on a timer | Chris Wilson, Grinding Gear Games, 2013-07-23 |
| PUBG | mouse scripts are detected | Dev Letter, 2026-04-17 |
| Destiny 2 | advanced macros and idle automation can be banned | Bungie, 2023-04-13 and 2024-08-19 |
| Guild Wars 2 | one action per press; no farming while away | ArenaNet's policy, as quoted on the Guild Wars 2 wiki |
| RuneScape | no software that generates clicks or key presses | Jagex, "Macro and client features not permitted" |
| New World | idle automation is kicked, then penalized | Amazon, reported by MMOs.com, 2021-10-05 |
| Black Desert | hardware macros are detected and restricted | Pearl Abyss notice, 2020-10-21 |
| Counter-Strike 2 | several actions from one input may be kicked on official servers | Valve, "Side-stepping Skill", 2024-08-19 |
| Hypixel (Minecraft) | nothing that automates a gameplay action; no auto or burst clicking or macros | its rules, as quoted on its forum, 2020-01-24 |

No rule on macros was found for Helldivers 2 itself.

```
gearbench-ctl import-pack --list                      what the pack holds
gearbench-ctl import-pack --dry-run                   what importing all of it would write
gearbench-ctl import-pack "Apex Legends" "Dota 2"     only these
gearbench-ctl import-pack --replace "Apex Legends"    all of it, the pack's lighting and programs over your Apex Legends
gearbench-ctl import-pack --without-programs          each loads by hand
```

With no names, `import-pack` imports all of the pack. Wherever a name is
taken it keeps yours and imports the rest, and says so when the daemon
wrote everything it sent; `--replace NAME` takes the pack's for that name,
and can be given more than once. With names given, NAME must be one of them.

### Hold-offs and Keep loaded

Choosing by hand, in the Loaded row or the tray menu's Game profile, with
Save as… or Set as M default, or with `load`, `unload` or `promote`, holds
off the profile showing for a program that has focus, or runs where focus is
not followed, however that profile was loaded. The hold-off lasts
until focus moves away from the profile's programs, or, where focus is not
followed, until none of them runs, so the next check does not put the
profile straight back; it follows focus, or the running program, to another
of the profile's programs. Loading the profile already loaded holds nothing
off, and a profile a program loaded stays that program's: Save as… onto it
keeps it loaded for the program, and it goes when a hold-off would end.
Where focus is not followed, several profiles can be held off at once, each
until none of its own programs runs.

The Game profile group names the latest hold-off, with Load it now
(`gearbench-ctl let-load`), which ends every hold-off and checks again at
once: the profile of the program with focus loads again, or, where focus is
not followed, the profile of a running program. With Keep loaded on, a
profile you loaded by hand stays instead. `gearbench-ctl status` names the
latest hold-off on its `game profile` line, and the journal notes each one.

Another profile that names a held-off profile's own program does not load
in its place. Where focus is not followed, another game's program that
starts during a hold-off still loads its own profile, and the hold-off
stays. A hand edit that leaves a profile's file unreadable ends no hold-off,
and does not unload a profile its program loaded while that program runs or
has focus: the profile keeps what it held, and `gearbench-ctl status` and the
window's banner give the file's error until a reload reads it or
`gearbench-ctl save` writes over it.

Keep loaded (`gearbench-ctl keep-loaded on|off`) keeps a profile you loaded
by hand when a program that would load another profile gets focus, or
starts where focus is not followed. The journal notes each time it does.
G HUB calls this a persistent profile. It is off whenever the daemon starts,
and it keeps only a game profile: over the M profile, a program's profile
loads as it does with Keep loaded off, even when the M profile was chosen by
hand.

## Macros

![The Macros tab](images/macros.png)

A G key or a mouse button bound to `macro:NAME` runs a macro: a list of
steps, kept as one JSON file per macro in `~/.config/gearbench/macros/`. A
file that cannot be read costs only that macro, and the Macros tab lists it
in a dimmed row, with the reason. Two files can hold one name, compared as
game profile names are (see "Game profiles"), as when a file manager makes
a copy. The file named for the macro is read (`spam.json` for Spam), or
else the first by file name, and the other is listed dimmed, naming the
file read instead.

A macro file edited by hand runs as edited at the next press. Until a
reload (see "Files"), the daemon refuses to save over it, rename it or
delete it; a file deleted by hand holds no edit to keep, so saving the macro
writes it again. Saving a new macro under the name of a file added by hand
since the Macros tab read its list is refused; the tab reads the list again
and asks whether to replace that macro, as it does for a name already
listed. Once a reload, from the window or `gearbench-ctl reload`, has read
an edit, the editor shows the open macro as its file holds it. If the editor
has unsaved changes, it keeps them, says the file changed, and Save writes
them over it.

The Macros tab has the list of macros on the left, with New and Delete, and
one macro's editor on the right: its Name, its Repeat mode, and its Steps.
Pick a step type from the dropdown and press Add, or press Record…. A Key,
Key down or Key up step has Change… to pick another key; the other steps are
edited on their rows. Each step moves up or down with its arrows, and its
trash button removes it. Each macro in the list says which keys and buttons
run it, and in which profile, and deleting a macro names them. Play and Save
stay in a bar at the bottom.

### Steps

A macro file looks like this:

```
{"version": 1, "name": "Reload and heal", "repeat": "once",
 "steps": [{"type": "key",      "value": "ctrl+shift+f5"},
           {"type": "text",     "value": "gg"},
           {"type": "key_down", "value": "g"},
           {"type": "delay",    "ms": 120},
           {"type": "key_up",   "value": "g"},
           {"type": "click",    "button": "left"}]}
```

| Type | Field | What it does |
| --- | --- | --- |
| `key` | `value` | presses the keys of a binding, `ctrl+c` or `a+b`, and releases them in reverse |
| `key_down` | `value` | presses them in the binding's order and holds them |
| `key_up` | `value` | releases them, in reverse |
| `text` | `value` | types a string, one tap per character |
| `delay` | `ms` | waits |
| `click` | `button` | presses and releases `left`, `right`, `middle`, `side` or `extra` on the virtual mouse |
| `click_down` | `button` | presses that button and holds it |
| `click_up` | `button` | releases it |

The `_down` and `_up` pairs hold something across a delay: press `g`, wait,
release `g`. A press of something the macro already holds does nothing, so
a `key_down` in a repeating macro presses once, and a release of something
it does not hold does nothing either. A `key_down` of a key that a G key or
a mouse button holds keeps that key down after the G key or button comes
up, until the macro lets go of it or stops. A `key` or `text` step leaves
alone a key that is already held, by the macro's own `key_down`, a G key or
a mouse button. A `key_up`, or the end of the macro, releases only what the
macro itself holds.

Nothing stays held when a macro stops. The daemon releases what the macro
pressed, in reverse order, when:

- the macro ends, a While held one at the end of the pass in progress when
  its key comes up, or it is toggled off;
- another macro takes over;
- the keyboard or mouse that started it is unplugged or moves between its
  receiver and its cable, or the daemon stops;
- the mouse that started it is switched off or goes out of range, or the
  keyboard that started a While held macro does;
- the Host mode switch is turned on or off, for a macro a G502 X PLUS button
  started;
- the macro is deleted or saved over while it runs, or its key no longer
  runs it after a rebind, an M profile switch, or a game profile loading or
  unloading;
- the screen locks or blanks, the machine is about to suspend, or another
  account or a text console takes the screen;
- Stop or `gearbench-ctl macro-stop` stops it.

A Toggle or Once macro a G key started keeps running while the keyboard is
switched off or out of range.

A macro that presses without releasing is allowed. The editor warns about
it under the steps, and about a release of a key no earlier step left down,
and saves the macro anyway. Each key of a combination counts on its own:
Key down `ctrl+c` and then Key up `c` leave ctrl down until the macro ends,
and the editor says so. A macro that repeats keeps its keys down from one
pass into the next, so a Key up `a` first and a Key down `a` later lift `a`
once a pass, and the editor warns about neither.

The editor puts a 50 ms delay in front of a step added after another,
unless either one is a delay or only one step is left before the 64-step
limit. A recording puts at least that much between two actions, except the
releases Stop adds at the end, which follow at once. The added delay is an
ordinary step you can change or delete, and nothing is added at playback. A
delay added by hand starts at 50 ms too.

Text steps send key codes, so what they type depends on your keyboard
layout; they assume US ANSI. A text step takes only what that layout types:
letters, digits, its punctuation, space, tab and newline. Any other
character is refused when the macro is saved; in the window, Save and Play
say which step by the number on its row. The pack and G HUB imports, in the
window and on the command line, show a newline in a text step as ⏎ and a
tab as ⇥.

### Repeat modes and limits

The Repeat row, `repeat` in the file, is Once (`once`), While held
(`while_held`) or Toggle (`toggle`). A While held macro starts again at the
end of each pass until its key comes up, then stops at the end of the pass
in progress. A Toggle macro starts at one press and runs until the same key
is pressed again, even if its file was deleted, broken or renamed by hand
after it started. A pass starts at least 50 ms after the previous one
started, so a macro with no delay in it repeats 20 times a second. End a
repeating macro with a delay longer than 0 ms, or a game may read a release
and the next press as no release at all; the editor warns when it does not.

One macro runs at a time: a new press cancels the one running. A toggled
macro has no key held down, so `gearbench-ctl status` names the macro
running, `gearbench-ctl macros` stars it, and the window shows a banner with
Stop.

The limits are 1,000 characters in a name, 64 steps, 256 characters in a
text step, 60 s in a delay, and 50 ms between the starts of two passes.

No macro starts while the screen is locked or blank, or while the machine is
about to suspend. The journal says why a macro stopped or did not start, and
Play and `macro-run` are refused with the reason. A G key bound to a key
still works while the screen is locked.

While your session is not the one on the screen, after Switch User or
Ctrl+Alt+F3, no macro starts, no G key or mouse button binding types,
`back` and `forward` included, and in host mode the G502 X PLUS's wheel
tilt scrolls nothing: they would reach the other session. Switching away
stops a running macro and lets go of held keys, and the journal notes when
the session goes inactive and comes back and each binding not typed.
Following a lock, a blank, a suspend or a switch to another session needs
`python3-gi`; without it, none of the four stops or refuses anything.

### Editing and saving

New opens a macro named "New macro", set to Once and with no steps, and Save
refuses until it has a step. Choosing another macro, pressing New or closing
the window with unsaved changes asks first. Save writes the open macro
without asking, and asks only when its name belongs to another macro. Names
match the way the files are named, so "my macro" belongs to "My Macro".
Changing the name and saving renames the macro, and every binding that names
it follows, except in a file changed on disk, one that would not load or one
whose save failed: that file keeps the old name, with a warning in the
journal. A name cannot hold a control character, such as ESC, and a file
whose macro's name holds one, or runs past 1,000 characters, does not load;
the white space at a name's two ends is cut off first. A save the daemon
refuses stays in the editor, unsaved, with the reason. While a save or a
delete is on its way, the list, New, Delete and Save are grayed out. One the
daemon does not answer keeps them grayed out until the daemon answers a
request sent after it, or that one goes unanswered too. The macro files are
then read, and a toast says whether the save or the delete went through, or
that the files could not be read.

### Playing a macro

Play runs the macro in the editor, unsaved changes included, 3 s after the
click. A macro types wherever the focus is, and the 3 s are the time to click
into the window it is meant for. Nothing is saved. A banner counts down, then
says the macro is running, with Stop. The same banner is up whenever a macro
runs, whatever started it, and its Stop stops any of them.

A played macro has no key held down, so Once and While held run one pass,
and Toggle runs until it is stopped: by Stop, `gearbench-ctl macro-stop`,
another macro, a save or delete of it, a lock, a blank, a suspend, a switch
to another session, or the daemon stopping. Rebinding a key or switching
profile leaves it running.

`gearbench-ctl macro-run NAME` plays a saved macro the same way, and `--delay
SECONDS` sets the wait, 0 to 60, or 0 for none. The daemon counts down, so
the prompt comes back at once, and `gearbench-ctl status` shows the
countdown on its `macro` line. `macro-stop` cancels the countdown too. With
no delay, or with the terminal still in front, the macro types into the
terminal.

A desktop shortcut for `gearbench-ctl macro-stop` stops a runaway macro
whatever has the focus. In GNOME, add it under Settings, Keyboard, View and
Customize Shortcuts, Custom Shortcuts, or from a terminal:

```
P=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/gearbench-macro-stop/
S=org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$P
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$P']"
gsettings set "$S" name 'Gearbench: stop the running macro'
gsettings set "$S" command "$(command -v gearbench-ctl) macro-stop"
gsettings set "$S" binding '<Shift>Pause'
```

The first `gsettings set` replaces the list of custom shortcuts; if you have
others, add the path to the list instead. Give the shortcut Shift and no
other modifier: the modifier you hold applies to the keys the macro is
typing, and with Ctrl, Alt or Super held they act as shortcuts (Super with a
typed `l` locks the screen).

### Recording a macro

Record… on the Macros tab opens a dialog that records the keys and clicks
you make in it, with the time between them as delay steps. Nothing outside
the dialog is recorded.

- A key is read as a hardware code, so a recording follows the physical keys
  whatever the layout. Modifiers held with a key become one step,
  `ctrl+shift+f5`, spelled the same for the left or the right modifier, and
  AltGr is Alt: AltGr+Q is `alt+q`, as the Press a key dialog binds it. A
  modifier pressed alone is recorded like any other key: tapped, it is
  `key shift`; held, it is `key_down`, the time, `key_up`. A Super tapped
  alone goes to GNOME's Activities overview and is not recorded.
- A modifier held over a click is recorded where it went down and where it
  came up, and the macro plays it that way. Held over keys typed one after
  another, it goes into each key's step instead: Shift held over A and then
  B records `key shift+a`, a delay and `key shift+b`. When a modifier has
  been recorded on its own and is still held, a key pressed under it leaves
  that modifier out of its step: Shift held, a click, then A held under it
  records `key_down shift`, the click, `key_down a`, `key_up a` and, where
  Shift came up, `key_up shift`, with the delays between them. With Shift
  held over W, and D going down before W comes up, W's release records
  `key_up w`, leaving Shift down for D, and D's records `key_up shift+d`.
  With the right Shift and W held, the left Shift held and let go of
  records only its `key_down shift`, and Shift stays down until W's
  `key_up shift+w`. A modifier that comes up while its key is still down
  gets a `key_up` of its own when something else is recorded before that key
  comes up: "Th" typed fast, with H down before T is up, records
  `key_down shift+t`, `key_up shift`, `key_down h`, `key_up t` and
  `key_up h`. One still held after its key comes up goes in again before a
  click: Shift, A, then a click with Shift still down records `key shift+a`,
  `key_down shift`, the click and `key_up shift`.
- A key or button released in under 35 ms, with nothing recorded in between,
  is one `key` or `click` step. Held longer, it becomes a `_down` step, a
  delay and an `_up` step. Auto-repeat is dropped.
- Gaps are rounded to 10 ms. A gap under 50 ms is recorded as 50 ms, and one
  over a minute as 60 s, the most a delay step holds.
- A mouse button with no click step, a sixth button or a wheel tilt, is
  ignored.
- A finger on a touchscreen is the left button, so a tap records as a left
  click. A second finger is ignored.
- A key held when the keyboard focus leaves the dialog, for another window
  or the overview, is recorded as released at that moment, since its real
  release goes where the focus went.
- Whatever is still held at Stop gets its release at the end. The recording
  keeps room for those releases, and the dialog says when it stops at the
  64-step limit. A modifier pressed on its own, with nothing recorded since,
  is recorded as held when you press Stop, unless fewer than three steps are
  left before the limit: then it is left out, and nothing says so. A
  modifier still held after the key it went with, as Shift after a capital,
  is left out at Stop.

Stop hands the steps to the editor, replacing what is there, and asks first
if the editor holds any steps. A recording with no steps replaces nothing: a
toast says Nothing was recorded and the editor keeps its steps. Nothing is
written until Save, so you can correct the delays or delete a stray click
first. Cancel and Escape throw the recording away; Escape itself is never
recorded. A key still held as the dialog closes does nothing in the window
until you let go of it.

## The mouse

![The Mouse tab](images/mouse.png)

The Mouse tab covers every mouse attached. From the top: the mouse's group,
with Device, Status, Battery and Host mode; Tuning, with DPI, DPI notices and
Report rate; DPI stages; Lighting; Buttons; and Onboard profile 1.

With more than one mouse attached, the Device dropdown, or `gearbench-ctl
mouse-select`, chooses which one the tab shows and the mouse commands act on.
`gearbench-ctl mouse` lists them on its `mice:` line, with the selected one
starred. Every attached mouse keeps working whichever one is shown, and a DPI
button acts on the mouse it is on. If the chosen mouse goes away, the tab
shows it again when it is back.

Pair nothing else to either mouse's receiver. Going by the kernel's
`hid-logitech-dj.c`, a keyboard paired there could have its keystrokes
arrive on the mouse's nodes, which Gearbench's udev rules let you and your
programs open: the hidraw and event nodes of the G502 X PLUS, the hidraw
node of the G305. This has not been checked on hardware. In host mode no
onboard profile sends a key, so a key on the G502 X PLUS's event node comes
from a keyboard. Gearbench then warns in the journal once each time the
mouse attaches, and `gearbench-ctl status`, the tray (at the top of its menu
and in its tooltip) and the Mouse tab's banner show the warning until the
mouse detaches.

### The buttons, G4 to G9

The G502 X PLUS has six buttons Logitech names G4 to G9. Left, right, the
wheel click and the wheel tilts cannot be remapped.

| Button | Where | Factory | Default binding |
| --- | --- | --- | --- |
| G4 | side, rear | back | `back` |
| G5 | side, front | forward | `forward` |
| G6 | thumb paddle | DPI shift | `dpi-shift` |
| G7 | DPI down | DPI down | `dpi-down` |
| G8 | DPI up | DPI up | `dpi-up` |
| G9 | behind the wheel | profile cycle | `none` |

With host mode on, the default (see "Host mode"), G4 to G9 reach the daemon
while it runs, on the receiver or the cable, with nothing written to the
mouse's onboard memory. With `gearbench-ctl mouse-host off`, or where host
mode did not take, the factory profile makes G4 and G5 back and forward and
G6 to G9 send nothing. Run `gearbench-ctl mouse-enable-buttons` once, or
Enable… under the Mouse tab's Onboard profile 1, and they send mouse buttons
11 to 16 at once, which the daemon reads. After
G HUB on Windows the mouse can be on onboard profile 2, where the buttons
are the factory ones again; see "The onboard profile and its backups". If
`gearbench-ctl mouse` says the buttons are partly enabled, run
`gearbench-ctl mouse-enable-buttons` again.

Onboard profile 1 decides what G4 to G9 do whenever the mouse runs it in
onboard mode: at the login screen, with the daemon stopped, on another
computer without G HUB, and while the daemon runs without host mode. Two
writes set it, and each replaces the other:

- The fallback, `gearbench-ctl mouse-fallback-buttons` or Write fallback… on
  the Mouse tab, is for a mouse in host mode. It gives G4 to G8 their
  factory functions back, which are also their default bindings, and makes
  G9 do nothing: from the factory G9 cycles the onboard profiles, and on
  profile 2 nothing Gearbench writes is in effect. In host mode, all six
  buttons still reach the daemon whatever profile 1 holds, so their bindings
  still apply. Without the daemon, G4 and G5 go back and forward, and G6 to
  G8 shift and step the DPI.
- The enable write, `gearbench-ctl mouse-enable-buttons` or Enable… outside
  host mode, is for a mouse that runs in onboard mode while the daemon runs,
  with `mouse-host off` or where host mode did not take. The bindings of G4
  to G9 apply while the daemon runs, and without it the six do nothing.

A mouse with the fallback that runs in onboard mode does what the fallback
says, and its bindings do not apply. Whether Gearbench then follows G7 and
G8 stepping the stages, as it follows the G305's DPI button (see "DPI and
stages"), has not been checked on the G502 X PLUS.

Each button has a dropdown on the Mouse tab: Nothing, Back, Forward, DPI
shift, DPI up, DPI down, then Keystroke…, which opens the Press a key dialog
(see "G keys and the MR layer"), and Macro…, which opens that dialog on its
macro list. A row bound to a key or a macro has Change… to pick another.
From the command line: `gearbench-ctl mouse-bind G9 ctrl+f15`. The bindings
belong to the M profile or the loaded game profile, beside the G keys, and
the Editing row at the top of the Buttons group says which.

- `back` and `forward` go out as the mouse's back and forward from a virtual
  mouse, pressed and released with the button. The actions match in any
  case, so `Back` is the mouse's back; `key_back` binds the Back key.
- `dpi-shift` drops to the shift stage while the button is held and puts
  the DPI back at the release. A DPI chosen while it is held, with DPI up,
  DPI down, the DPI row, the tray or `gearbench-ctl mouse-dpi`, stays, and
  the release puts nothing back, as when a game profile moves the DPI under
  the held button. If the mouse's link drops or the daemon stops while the
  button is held, or the mouse does not answer the release, the DPI from
  before the shift is put back as soon as it can be: when the mouse links
  again or answers, at the next release, or at the stop.
- `dpi-up` and `dpi-down` step through the stage list from the nearest stage,
  with a desktop notice (see "Notices").
- A key binding or `macro:NAME` works as on a G key: the key binding is held
  as long as the button is, and the macro runs by its repeat mode (see
  "Repeat modes and limits"). A lock, a blank, a suspend or a switch to
  another session releases a held key, `back` or `forward`, and while
  another session has the screen none of them types.

Restore defaults, after a confirmation, or `gearbench-ctl mouse-defaults`,
puts all six back to the default bindings in the table above.

### DPI and stages

Type the DPI into the DPI row and confirm it with the apply button, or run
`gearbench-ctl mouse-dpi 1600`. The DPI snaps to the sensor's step. The G502
X PLUS takes 100 to 25600, and the G305 200 to 12000, both in steps of 50.
The DPI row refuses a value outside that range; `mouse-dpi` sets the nearest
end of it instead, and its `dpi:` line says what was set. The G305's DPI
button steps through its stages by itself and tells Gearbench the stage it
moved to. That stage's DPI then shows as the mouse's, is remembered, and
gets the notice a DPI button's step gets.

The daemon remembers the last DPI you chose for each mouse model, from the
window, the tray, `mouse-dpi` or a DPI button, whichever way the mouse is
connected, and puts it back when the mouse is switched on or attaches. Two
mice of one model share it. Without this, a mouse powers on at the stage it
was last on, or in host mode at the default stage. The paddle's shift is not
remembered. Under a loaded game profile's own stages, the DPI you choose is
remembered for that profile instead (see "Stages and rate per game profile").

The stage list is what the DPI buttons step through: one to five distinct
values, in onboard profile 1, with a default stage and a shift stage, the
one the paddle drops to. In host mode a loaded game profile can have its
own.

On the Mouse tab each stage has a row, titled with its number and whether it
is the default or the shift stage, `Stage 3 · default`, with a dot in the
color the mouse flashes on that stage. The dot's tooltip names the color, and
a screen reader reads the same words. Type a stage into its row and confirm
it with the apply button. The rows are one list, sent whole. A stage outside
the sensor's range is refused with the range named, and nothing is written;
a value inside it snaps to the step of 50. A row's trash button removes its
stage once you confirm; a stage's row emptied and applied is refused. Add a
stage shows an empty row for a new one, sent when you apply it; that row's
trash button, before you apply it, takes it away without asking.

The Default stage and Shift stage rows pick which stage is which. The mouse
starts on the default stage when it switches to profile 1, and a new default
leaves it on the stage it is on. The rows and `gearbench-ctl mouse-dpi-stages
800,1600,3200 --default 1600 --shift 800` write the list at once. A G305's
tab has no Shift stage row, since none of its buttons can be bound to DPI
shift; `--shift` still sets its shift stage. If you change the value of the
default or shift stage, it stays the default or shift stage at its new
value, and a toast names that value. On the command line, `--default` and
`--shift` take DPI values, snapped as the stages are and refused outside the
range, and a value the list does not hold is refused. One under 100, the
lowest any supported sensor takes, is refused as a stage's number before
anything is sent.

After a stage edit, a remembered DPI becomes what the mouse reads then: edit
the stage it is on, and it is at the new value at once and after the next
power-on. With the paddle held, it becomes the DPI from before the paddle,
at its place in the new list, which is where letting go of the paddle puts
the mouse. With no DPI remembered, nothing is stored and the mouse stays on
its own stage.

Use factory stages…, after a confirmation, or `gearbench-ctl mouse-dpi-stages
--factory` writes the factory stages, default and shift, read from the
mouse's ROM copy of profile 1, in place of the list. On the G502 X PLUS they
are 800, 1200, 1600, 2400 and 3200, default 1600, shift 800.

### Host mode

Host mode is on by default. Turn it on or off with `gearbench-ctl mouse-host
on|off`, the Host mode switch on the Mouse tab, or `mouse_host_mode` in
`config.json`. The switch shows for a mouse it concerns, and says whether
host mode took and why not; for a mouse asleep or switched off that has not
taken it yet, it says host mode is tried once the mouse answers. With the
switch off, it also says when the mouse has not yet gone back to onboard
profile 1. Outside host mode it says when the mouse runs a profile other than
profile 1. It concerns the G502 X PLUS, on its receiver or its cable; moving
the mouse from one to the other while the daemon runs puts it in host mode
again on the new one. On the G305 the switch changes nothing. With it off, or
where it did not take, the daemon reads G4 to G9 from the mouse's event node,
which only the `gearbench` group may read (README, "Install"); until the
daemon has that group, `gearbench-ctl status` and the Mouse tab's banner say
what is missing, and so does the Programmable buttons row once the buttons are
enabled.

While the daemon runs with host mode on, a G502 X PLUS is in host mode. No
onboard profile runs, and G4 to G9 reach the daemon as the mouse's button
reports, with no write to its onboard memory. The DPI stages and the report
rate are onboard profile 1's, or a loaded game profile's own, sent live.
`gearbench-ctl mouse` starts its `mode:` line with `host`. The wheel tilts
scroll sideways as before, one notch at the tilt and one every 100 ms while
it is held; the daemon makes these, and a tilt cannot be bound. A DPI set to
one of the stages, by a DPI button, the paddle or `mouse-dpi`, flashes the
stage's color and leaves the mouse dark, as in onboard mode, and the
lighting comes back 5 s later.

A mouse that refuses host mode runs profile 1 as with the switch off (or,
if the switch to profile 1 does not take, the profile it was on), and
`gearbench-ctl status` and a banner on the Mouse tab say host mode did not
take, with the reason. If the mouse then also fails to switch back to
onboard mode, it is left in host mode with G4 to G9 and its DPI buttons
dead; `gearbench-ctl status` and the banner say so, and switching the mouse
off and on, or `gearbench-ctl mouse-host on`, puts it right. In both cases
Gearbench tries again when the mouse next attaches or is switched on.

When the daemon stops, the mouse goes back to onboard mode on profile 1, at
the DPI it had outside a game profile's own stages, and G4 to G9 do what
profile 1 says (see "The buttons, G4 to G9"). A mouse asleep at the stop is
asked a second time, once the first request has woken it.

A mouse switched off or out of range when the daemon stops, or a daemon
that is killed, leaves the mouse in host mode until its next power cycle or
the next start: clicks and motion work, and G4 to G9 and the wheel tilts are
dead. A mouse switched off or out of range at `gearbench-ctl mouse-host off`
stays in host mode until it is back, and goes to onboard mode then, unless
host mode is on again by then.

### Report rate

The Report rate row, or `gearbench-ctl mouse-rate`, sets 1000, 500, 250 or
125 Hz. The rate is stored in onboard profile 1 and in effect at once, unless
the mouse runs another onboard profile; in host mode it is also sent live.
Against a mouse left in host mode by something else, it falls back to a live
write, which takes no backup, and the reply says which happened. With a game
profile loaded, in host mode, it is that profile's own rate instead.

### Stages and rate per game profile

In host mode the daemon runs the DPI stages and the report rate, and a game
profile can have its own for each mouse model. While the profile is loaded,
they are in force in place of profile 1's, sent live, and nothing is written
to the mouse's onboard memory.

- With a game profile loaded, `gearbench-ctl mouse-dpi-stages` and
  `gearbench-ctl mouse-rate`, and the stage and rate rows on the Mouse tab,
  save to the game profile and leave profile 1 as it is, and the reply says
  so. A profile with no stages of its own starts from profile 1's, and
  `--default` and `--shift`, when left out, stay on profile 1's default and
  shift DPIs, or on their places in the list when those DPIs are gone.
- The rows show the profile's own while it is loaded, and the Game profile
  row at the top of the DPI stages group says whose they are.
- `mouse-dpi-stages --reset` and `mouse-rate --reset` drop the profile's own,
  and profile 1's are used again. Use profile 1's, on the Game profile row,
  drops both, once you confirm; Cancel is the default.
- When a profile with its own stages loads, by hand or for its program, the
  DPI goes back to the one last chosen under it since the daemon started, so
  switching away from a game and back keeps the DPI you picked in it. At its
  first load after a start, or once that DPI is not one of its stages, it
  goes to its default stage. When it unloads, or one without its own stages
  takes its place, the DPI goes back to where it was before it loaded. None
  of these flashes the stage's color.
- The DPI buttons, the paddle and the tray menu use the profile's own
  stages. A DPI chosen under them, with a button, the tray or `mouse-dpi`, is
  put back after a power cycle while the profile stays loaded, and the DPI
  remembered for the desktop is left as it was.
- An edit to the loaded profile's stages, or `gearbench-ctl reload` after a
  hand edit to its file, keeps the mouse on the stage it is on, at its new
  value. If the mouse refused the profile's stages when it loaded and has
  not taken them since, the edit moves it as a load does. A stage a hand
  edit puts between the sensor's steps, or outside its range, is taken at
  the nearest DPI the mouse takes, as `mouse-dpi` does, unless two stages
  would land on the same one.
- On the G305, or with `mouse-host off`, the mouse runs profile 1, and a
  profile's own stages and rate do not apply: the two commands and the rows
  write profile 1, and their reply says so; `--reset` is refused. For a
  profile with stages or a rate of its own, the `game stages:` line of
  `gearbench-ctl mouse` and the Game profile row say they are not in force.
- A mouse that is switched off when the profile loads gets them when it is
  back, and the reply to a change made while it is off says the change
  takes effect once the mouse answers. A change the mouse refuses is still
  saved: the reply says it was refused, and the mouse gets it at its next
  setup or the profile's next load.

### Lighting and color sync

`gearbench-ctl mouse-color` and `mouse-effect`, and the picker and Effect
row in the Mouse tab's Lighting group, set the mouse's own color and effect,
live as you drag. The Effect row shows the effect last set, which the
daemon does not read from the mouse: Static for a color of its own, or
when none has been set since the daemon started. The mouse's color is not
stored: `gearbench-ctl mouse` reports what was last written since the
daemon started.

`gearbench-ctl mouse-sync on`, or Sync with keyboard, makes every mouse with
lights follow the keyboard: the background in Per-key mode, the Static color
in Static mode and, steady, under the animations, and black under
`disabled`. Live previews go to the mouse too. The mouse has no brightness
control, so the daemon scales its color by the keyboard's brightness: at
50 %, the default, it shows half of each color, and at 100 % the color
itself. It goes off with the screen lock, and it dims with the keyboard's
idle dimming once nothing on the desktop, the mouse included, has been
touched for the keyboard's Dim after time (see "Idle, lock and suspend").
While sync is on, `mouse-color` and `mouse-effect` are refused and the
tab's picker and Effect row are grayed out. The Background row on the
Keyboard tab says when the background is black, which leaves a synced mouse
dark. `mouse-sync off` gives each mouse back its own color or effect. A
mouse with none keeps the synced color at the keyboard's brightness as its
own, and goes back to that color at every later switch off while the daemon
runs. With sync off, a mouse with no color or effect of its own would stay
dark after a DPI flash, so once the flash is over it takes the keyboard's
color at the keyboard's brightness.

The setting is `mouse_sync_color` in `config.json`, one for all mice and all
profiles. A mouse that is off or asleep gets the color when it is back, and
a failed write to the mouse never affects the keyboard.

### The onboard profile and its backups

A mouse runs an onboard profile from its own memory: one of five on the
G502 X PLUS, and the only one on the G305. Gearbench writes profile 1 only.
Profile 1 holds the report rate, the DPI stages with the default and shift
stage, and the button entries.

`mouse-enable-buttons`, `mouse-fallback-buttons`, `mouse-rate`,
`mouse-dpi-stages` and `mouse-restore` write it, and in the window Enable…
or Write fallback…, the rate and stage rows, Use factory stages… and Restore
profile backup…. With a game profile loaded and `mouse-host on`,
the rate and stage commands and rows, and Use factory stages…, write that
profile instead on a G502 X PLUS, whether or not host mode took. The
Onboard profile 1 group on the Mouse tab shows what profile 1 holds: its
stages and rate in one line, whatever a game profile's rows above show, the
button write on the G502 X PLUS, and the backup. When a write failed and
profile 1 could not be read back, or profile 1 fails its CRC, the Stages and
rate row says so before the stages. Until that is settled, the Programmable
buttons row says the buttons are not known and suggests neither write.

Every write:

- first backs up the directory sector and every profile sector to
  `~/.config/gearbench/backup/<model>-sector-<XXXX>-<UTC timestamp>.bin`,
  with `g502x` or `g305` as the model, and never overwrites a backup. An
  image read with a bad CRC is saved with `.bad-read` in place of `.bin`. A
  restore never picks one, nor a backup set aside as `.unchanged` (below).
- refuses an edit when the sector as read now has a bad CRC. A restore
  writes over such a sector.
- reads the sector back after writing and compares it byte for byte.
- takes effect at once, with no power cycle, and the reply says so. On a
  mouse that runs another onboard profile (below), outside host mode, it is
  not in effect until the mouse runs profile 1, and the reply says that
  instead. In host mode, where no profile runs, the daemon takes the stages
  and rate from profile 1 at once, except any a loaded game profile has of
  its own, and the mouse runs profile 1 when the daemon stops. The reply
  says what holds while the daemon runs and what changes once it stops.
  After a restore or a report rate change in host mode it also says if the
  mouse did not take the report rate, and after the other writes in host
  mode if the mouse stopped answering after the write. If the mouse did not
  answer, the daemon sends the rate once it does; otherwise the rate waits
  for the mouse's next setup, as after it is switched off and on.

The window locks those rows while a write is on its way, with a spinner on
the G502 X PLUS's Programmable buttons row, and a toast says when it is in
effect. A write takes about a second when the mouse answers.
`gearbench-ctl mouse-restore`, or Restore profile backup…, writes this
model's newest backup of profile 1 back, backing up the current state first,
so two restores in a row toggle between the two; over a profile 1 with a bad
CRC, the second writes the same backup again, as the window's dialog says
before the first. The newest is the model's, whichever mouse of that model
wrote it: with two G502 X PLUS, it can be the other one's. A restore that
fails and leaves a profile 1 with a good CRC as it was renames the backup it
just made, adding `.unchanged` to its name, so trying again writes the
backup you meant; nothing is deleted. RECOVERY in `man gearbench-ctl` has
what to do after a write or a restore that failed, how to restore an older
backup, and what is left with no good backup.

The daemon keeps the mouse on profile 1. G HUB can leave the G502 X PLUS on
profile 2, where G4 and G5 are back and forward again and nothing written to
profile 1 shows. At attach and after each power-on the daemon switches it
back, which writes nothing to the mouse's memory, and puts the remembered
DPI back; the journal notes the switch, and warns with the reason if it
does not take. Outside host mode, where that profile then runs,
`gearbench-ctl status` says the mouse runs another profile and the Mouse
tab shows a banner. The Profile backup row and the Enable… and Restore
profile backup… dialogs say before a write that it is not in effect while
that profile runs, and `gearbench-ctl mouse` names the profile on its
`running:` line. In host mode no profile runs, and the daemon tries the
switch again when host mode ends.

The enable write and the fallback live in the mouse's own memory, and no
Gearbench setting records them. The mouse keeps either on another computer
and through a reinstall of Gearbench, and a different G502 X PLUS is on its
factory profile until it gets its own write. `mouse-rate` and
`mouse-dpi-stages` change their own bytes and carry the button entries
across.

`gearbench-ctl mouse` reports the button state on its `buttons:` line:
enabled, fallback, partly enabled or not enabled, and the write to run. The
Mouse tab reports it on the Programmable buttons row under Onboard
profile 1. Both show profile 1 as the daemon last read it, at attach or at
the last write. Once the buttons are enabled, the row's button reads
Re-apply…, which writes the same entries again. In host mode,
`gearbench-ctl mouse` starts the line by saying G4 to G9 come in the mouse's
button reports, and the row says first that G4 to G9 reach Gearbench
directly and that the mouse runs profile 1 without Gearbench. The row then
has Write fallback… in place of Enable…, in the accent color until profile 1
holds the fallback. Its dialog says what the write does and that every
profile sector is backed up first. A mouse whose switch back to onboard mode
was refused stays in host mode with G4 to G9 and its DPI buttons doing
nothing until it is switched off and on, or `gearbench-ctl mouse-host on`
arms it again. `gearbench-ctl mouse` then says `host, stuck there` on its
`mode:` line, and its `buttons:` line and the Mouse tab's Programmable
buttons row read as they do outside host mode. A mouse asleep or switched
off at the time is not stuck: its `mode:` line says
`host until the mouse answers`, and once it does, Gearbench makes the
switch. With host mode on, it tries host mode again first and makes the
switch only if that fails too.

### Asleep or switched off

A mouse switched off behind its receiver stays attached: `gearbench-ctl
mouse` says `not answering`, and the Mouse tab's Status row has an amber
warning sign, with the rows that write to it grayed out. Until it moves,
clicks or reports, the daemon sends it nothing on its own but a probe, 30 s
after it went quiet and then at doubling intervals up to two minutes, and
none while its receiver says the link is down. A command that writes to it,
such as `gearbench-ctl mouse-dpi`, still tries it. `gearbench-ctl mouse` and
the Mouse tab send it nothing: they show what was last read from it.
`gearbench-ctl mouse`, and the window's 5 s read on any tab while the window
is on screen, bring its next probe within 30 s unless its receiver says the
link is down. The G502 X PLUS's receiver says when the mouse is switched on,
and the daemon sets the mouse up again at once, lighting and DPI included.

Each setup that does not come from turning host mode on or off also puts
back the wheel's high resolution, as an earlier setup found it, in case
another program turned it off. In low resolution the G502 X PLUS's wheel
scrolls about an eighth as far per notch, and the journal says when a setup
puts high resolution back. If the wheel stays slow, switch the mouse off and
on behind its receiver: the kernel turns high resolution on as the mouse
connects, and from then on the daemon puts it back whenever such a setup
finds it off.

A mouse that is off when the daemon starts is set up as soon as its receiver
says it is switched on, or it moves, clicks or reports. Failing that, it is
looked for again after 10 s, then less often, up to every two minutes. With
no other mouse attached, `gearbench-ctl mouse`, or the window's 5 s read on
any tab while the window is on screen, brings the next try within 30 s.

## The headset

![The Headset tab](images/headset.png)

The Headset tab and the `gearbench-ctl headset` commands cover the Astro A50
Gen 4 through its base station, on USB with its switch on PC (`9886:002c`).
Gearbench sends the base station a fixed list of commands and refuses any
other before it is sent, a reset, the bootloader and the headset's firmware
updater among them: it never updates the base station's or the headset's
firmware. With its switch on Console the base station has no control
interface, and Gearbench does not look for the Xbox edition (`002d`). Only
Gearbench may talk to the base station: any other program on its node sees
Gearbench's replies and mixes its own in.

A change goes to the base station's memory and is in effect at once; Save
writes it to flash (see "Saving"). Gearbench checks every value before it
sends it, since the base station clamps nothing. The tab shows what the base
station reads back, and leaves out a row whose setting the base station
would not read. The tab has the daemon read every setting and preset again,
as `gearbench-ctl headset --read` does, the first time it shows with the base
station answering, and again after the base station or the daemon was lost.
The daemon also reads them all again when the headset links to its base
station, after a save, and after a resume, since the base station may have
lost what its memory held. While a save runs, the read waits for it to end.

The tab, from the top: the Astro A50 group with its status, Battery and
Balance; Microphone; Equalizer; Stream port; Base station; Revert to saved;
and Spatial sound. The A50's two outputs in GNOME's Sound settings come from
`install.sh` and the package, not from the tab (see "Game and Chat").

### Status and battery

The status group is titled with the model, Astro A50. Its first row says
Headset on, with a green check mark, while the headset is on and linked to
its base station; Headset off or out of range, amber, while it has no link
to its base station; and Not answering, amber, while the base station does
not answer. The row's subtitle gives the firmware versions of the base
station and, once it has been on, the headset; its tooltip gives the node.

The Battery row gives the headset's charge and `on battery`, `charging` or
`fully charged`, amber at 30 % and red at 15 % while not charging. At 100 %
on its base station it says `fully charged`, though the base station no
longer reports it charging. While the headset is on, the daemon reads the
battery once a minute, and again when the headset is put on or taken off its
base station. While the headset is off, the row keeps the last reading
and adds the time it was read, with the date for a reading from an earlier
day. While no base station is attached, the daemon cannot be asked, or the
headset has not been on and linked to its base station since the base
station was plugged in or the daemon started, the row shows no reading.

The Balance row is the live mix that the headset's Game and Voice buttons
set, with a grayed-out slider, Voice at the left and Game at the right, and
ticks at 25, 50 and 75 %. The row is read only, and shows while the headset
is on. Writing Default balance moves the live mix to the same place.

`gearbench-ctl headset` prints the same, a line each: `headset:`, which reads
`on` or `off or out of range`, and `on, docked` or `switched off, docked`
while the headset sits on its base station; `versions:`; `battery:`; and
`balance:`. While the base
station does not answer, it says so and gives the time of the readings below,
dated when they are from an earlier day, and before the first full read it
says the settings have not all been read yet.
`gearbench-ctl status` says whether a base station is attached.

Every 5 s, whether or not the window is open, the daemon asks the base
station whether the headset is on, and while it is, for the live balance and
the active preset, so the tab and `gearbench-ctl headset` follow the
headset's own buttons.

### Microphone

| Row | Command | Values |
| --- | --- | --- |
| Mic level | `gearbench-ctl headset-set mic-level 70` | 0 to 100 % |
| Sidetone | `gearbench-ctl headset-set sidetone 40` | 0 to 100 %: your own voice in the headset |
| Noise gate | `gearbench-ctl headset-set noise-gate home` | Streaming, Night, Home, Tournament |
| Mic EQ | `gearbench-ctl headset-set mic-eq bright` | Bright, Airy, Warm |

Streaming is the lightest gate and Tournament the strongest, which gates the
sidetone too; Home is Astro's default. Each noise gate's entry in the list
has a tooltip that says what it is for. A slider is sent once it has stayed
put for 0.3 s, or when the window closes, so a drag is one write.
`gearbench-ctl headset-set` answers with the value read back, and the saved
value while flash holds another.

### Equalizer

The base station holds three EQ presets, each a name and five bands, and
plays the active one. The Preset row lists them by number and name and
switches the active one, as `gearbench-ctl headset-set preset 2` does. The
rows under it edit the active preset:

- Name, sent with its apply button: 1 to 49 bytes, with no control
  character. `gearbench-ctl headset-eq --name "Night"` does the same.
- Band 1 to Band 5: each row's slider is the band's gain, -7 to +7 dB, and
  its subtitle gives the frequency and the gain. Expanded, a band shows
  Frequency, 80 to 15000 Hz, and bands 2 to 4 also show Width, 0.1 to 3.0:
  the band's width as a multiple of its frequency (1/Q), where 2.0 is wide
  and 0.5 narrow. Bands 1 and 5 are shelves and have no width. A gain is
  sent after 0.3 s at rest, a frequency or a width after 0.6 s.

`gearbench-ctl headset-eq` changes any preset with `--preset N`, and the
active one without it. `--gains 0,2,0,-1,3` sets all five gains, and `--band
3:2750:1.5` sets one band's frequency and width (`--band 1:90` for a shelf),
once for each band. A width is rounded to a tenth, as the Width row shows
it. Gearbench checks everything before the first write, so one bad value
writes nothing. The command answers with the preset as read back.

Import from Astro Command Center… loads an `.astroeq` file, the preset file
Astro's app writes on Windows or the Mac, into the preset that was active
when the dialog opened: its name, its five gains and all five bands.
`gearbench-ctl headset-import "A50 MOD KIT.astroeq" --preset 3` does the
same for any preset. A file that does not parse, or holds a value that is
missing, out of range or not a number (a whole number for a gain or a
frequency), is refused before anything is sent, and the message names it. A
`Name` that holds a control character is refused too. Both refuse anything
but a regular file, such as a named pipe or a device, and a file over
16 KiB.

Which preset is active is not on the unsaved list, since the base station
gives no saved value to compare it with.

### Stream port

Game, Chat, Mic and Aux, 0 to 100 %, set the mix the base station sends to
its stream port: `gearbench-ctl headset-set stream-game 80`, and likewise
`stream-chat`, `stream-mic` and `stream-aux`.

### Base station

| Row | Command | Values |
| --- | --- | --- |
| Default balance | `gearbench-ctl headset-set default-balance 128` | 0, all Voice, to 255, all Game |
| Alert volume | `gearbench-ctl headset-set alert-volume 50` | 0 to 100 % |
| Auto shut-off | `gearbench-ctl headset-set auto-off 30` | 10, 15, 20, 25 or 30 minutes |

The Default balance slider has ticks at 25, 50 and 75 %. Released within 10
steps (about 4 %) of a tick, by the pointer or a finger on a touchscreen, it
lands on the tick, and only the tick is sent. The keyboard and the scroll
wheel move it without snapping.

The base station cannot report Auto shut-off's flash value, so Auto
shut-off is on the unsaved list from the time it is written until a save
works, the base station is unplugged or the daemon restarts, and Revert to
saved cannot put it back. There is no row for the base station's light
brightness: the base station stores it, but setting it did not change its
lights.

### Saving

The base station runs on its memory and starts from its flash after a power
cycle, such as being unplugged. A save to flash takes over 2 s and the
flash's wear limit is unknown, so Gearbench writes flash only when you ask.

While anything differs from flash, a banner at the top of the tab lists it,
with Save. The unsaved list has each setting whose value differs from its
flash value, each preset whose name, gains or a band does, and Auto shut-off
once written. Any other setting put back to its flash value leaves the
list. `gearbench-ctl headset` prints the same list on its `unsaved:` line.
Until the daemon has read everything once, the line ends by saying the rest
is not read yet, after anything already known to differ. It adds that
`gearbench-ctl headset --read` reads it, except while a save runs or the
base station is not answering, when `--read` asks nothing. At other times,
while the list is not empty, `gearbench-ctl headset` adds the command that
saves it, and `headset-set`, `headset-eq` and `headset-import` add it when
the setting or preset they changed is on the list. The banner hides while
no base station is found, since an unplugged base station comes back with
its flash values. The banner shows again if the daemon's read then finds
differences.

Save, or `gearbench-ctl headset-save`, writes every setting and preset to
flash. A slider, frequency or width not sent yet goes first. While the
save runs, the banner says so with no button, the rows are grayed out,
`gearbench-ctl headset` says it is saving, and any other change is refused.
A save started with Save ends with a toast saying it worked or why not; one
started with `gearbench-ctl headset-save` ends with none. Either way
`gearbench-ctl headset` gives why it failed on its `save:` line. A save that
worked is read back at once. After a failed save the daemon reads everything
again: at once for Save, and for `gearbench-ctl headset-save` once the
headset is on, or at `gearbench-ctl headset --read`. If the base station is
unplugged or the daemon stops during the save, no toast says how it ended.
After the daemon stops, the unsaved list from its next read says what the
save left out, apart from Auto shut-off. After an unplug, the list is empty
whether or not the save got through.

Revert to saved, under the Base station group, asks first, with Cancel the
default. It then puts every setting and preset that differs back to what
flash holds, and reads everything again; `gearbench-ctl headset-revert` does
the same. The row is grayed out while nothing is unsaved. Which preset is
active stays as it is. A written Auto shut-off stays too, since it has no
saved value to go back to, and a note says so. A value the base station
refuses, or one Gearbench refuses before sending it, is named in a note if
it still differs from flash once everything is read again, and the rest go
back. The tab shows a note as a toast.
When something still differs from flash afterward, `gearbench-ctl
headset-revert` names it and exits 1.

### Game and Chat

The base station has two audio outputs on USB, Game and Chat, and the
headset's Game and Voice buttons mix the two after USB (the Balance row).
`install.sh` and the package give the base station an ALSA profile set,
`gearbench-astro-a50.conf`, and a WirePlumber rule,
`60-gearbench-astro-a50.conf`, that selects it. With them, GNOME's quick
settings and Sound settings list two outputs, Astro A50 Game and Astro A50
Chat, in place of one `Headphones – Astro A50` entry, and the microphone is
Astro A50 Chat. Games go on Game and voice chat on Chat: set your voice chat
program's output to Astro A50 Chat in its own settings.

With no output chosen before, Game is the default: the rule ranks it above
Chat. The first time, each output starts at 40 % and the microphone at
100 %; WirePlumber keeps each level from then on. The profile set keeps
PipeWire's automatic profiles (`auto-profiles = yes`), so beside the profile
with both outputs the card has Game, Chat or the microphone alone, and Game
or Chat with the microphone. The profile with both outputs ranks highest,
and Sound settings can list the others under a Configuration row for the
A50. The node names stay PipeWire's own,
`alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game` and `.stereo-chat`,
so a default output or a program's saved choice still points at the same
output.

WirePlumber reads the two files only when it starts, and it starts with your
user manager. Until it restarts, GNOME lists one entry and the Spatial sound
group shows its Outputs row: log out of every session and back in (with
lingering on, restart the machine instead), or run `systemctl --user restart
wireplumber`, which stops all sound for a moment. `install.sh` restarts it
when either file is new or changed.

### Spatial sound

![The Spatial sound group](images/headset-spatial.png)

Astro A50 Spatial is a third output, a 7.1 one for games, and it plays
through Astro A50 Game. It renders each of the seven speakers for headphones
with the SADIE II database's measured head responses, so a 5.1 or 7.1
game's rear and side sounds come from behind and beside you. Music and voice
chat stay on the plain outputs.

It runs as a user service of its own, `gearbench-spatial.service`, and only
while the base station is plugged in: unplugged, the service stops, and it
starts again on its own when the base station comes back. When you log in
with the base station already plugged in, a second service,
`gearbench-spatial-login.service`, starts it; without the base station that
one does nothing and reports no error. The Spatial sound group, the last one
on the Headset tab, switches the service with `systemctl --user` and shows
what systemd and PipeWire (`pw-dump`) say of it. The daemon has no part in
it, so the service keeps running with the daemon stopped. The group is on
the tab once the window has seen the base station (see "The device tabs");
until then, or without the daemon, `gearbench-ctl headset-spatial` does the
same from a terminal.

The group's rows:

- Astro A50 Spatial, the switch, is off until you turn it on. On enables the
  service and its login service, and starts it if the base station is
  plugged in; off stops it and disables both. On stays on across logins and
  machine restarts. Its subtitle says the state, in the table below.
- Head picks whose measured head is used: KU100, the default, the Neumann
  KU100 (SADIE II subject D1), which listeners preferred most in SADIE II's
  own test; or KEMAR (subject D2), a dummy head with torso and shoulders.
  Changing it restarts Astro A50 Spatial while it runs: a game playing on it
  goes to the default output for about a second and comes back, still in
  7.1. While it is not running, as with the switch off or the base station
  unplugged, the head applies at its next start.
- Room is Competitive, the default, with no room: direction alone. Immersive
  adds a short, dry room about 11 dB under the sound, which moves it out of
  your head. It changes while the output plays, with no restart; when it
  cannot, a toast says so, and the room applies at the next start. Chosen
  while Astro A50 Spatial is not running, as with the switch off or the base
  station unplugged, or while it waits to start again after a failure, it
  applies at its next start. Chosen while it is starting, it waits up to 2 s
  for the output to come up and changes it then; still starting after 2 s,
  the output keeps the room it started with until its next start. A toast
  says the room did not change, and its Details say why.
- Steam launch option shows `PULSE_SINK=gearbench-a50-spatial %command%`,
  with a copy button.
- Other launchers shows how to set the same variable in the game's
  environment, with a copy button for `PULSE_SINK=gearbench-a50-spatial`.
- Or pick it in GNOME says to pick Astro A50 Spatial before the game starts,
  and Astro A50 Game after it quits.
- Dolby on the star shows while the switch is on (below).
- Outputs shows only while GNOME still lists Game and Chat as one output,
  until WirePlumber restarts.

The switch's subtitle and its status icon:

| Subtitle | Icon |
| --- | --- |
| `Off` | none |
| `On, linked to Astro A50 Game` | green check mark |
| `In use: ARC Raiders, 8 channels, 3 streams`, a game playing on it | green check mark |
| `Starting` | amber warning sign |
| `On; starts when the base station is plugged in` | amber warning sign |
| `On, but stopped: switch it off and on again` | amber warning sign |
| `On, but not linked to Astro A50 Game`, and PipeWire's reason when it gives one | amber warning sign |
| `Off, but an Astro A50 Spatial runs that Gearbench did not start` | amber warning sign |
| `Did not start: journalctl --user _SYSTEMD_USER_UNIT=gearbench-spatial.service says why` | red error sign |
| `On, but PipeWire's pw-dump is not installed` | red error sign |
| `On, but PipeWire could not be read: ...` | red error sign |
| `Not installed: run ./install.sh again, or install the Gearbench package` | red error sign |
| `systemctl --user did not answer: ...` | red error sign |

A game that sends only 2 channels adds a second line saying to set its audio
output to surround or System Default and start it again. The switch, Head
and Room are grayed out while a change runs, and when the service is not
installed or systemctl does not answer. While systemctl does not answer, the
switch stays where it was. The three launch rows are hidden while the
service is not installed. A change that fails shows a toast saying why:
systemctl's or PipeWire's own error when it prints one, and Gearbench's words
otherwise. An error too long for a toast goes behind its Details button, and
the toast says only that Astro A50 Spatial did not start or did not stop,
that the head is saved but Astro A50 Spatial did not restart, or that the
room did not change. When Head or Room cannot be saved to `spatial.json`,
the toast names that file and gives the system's error, as in
`Could not save ~/.config/gearbench/spatial.json: Permission denied`; when
that is too long for a toast, it says only `Could not save spatial.json`,
with the rest behind Details.

To play a game on Astro A50 Spatial:

- Steam: paste the launch option into the game's Properties, General,
  Launch Options.
- Lutris: the game's Configure, System options, Environment variables, with
  `PULSE_SINK` as the key and `gearbench-a50-spatial` as the value.
- Heroic: the game's Settings, Advanced, Environment Variables, with the
  same name and value.
- A terminal: `PULSE_SINK=gearbench-a50-spatial <the game's command>`.
- Or choose Astro A50 Spatial as the output in GNOME's quick settings before
  the game starts. GNOME keeps that choice as the default, after a replug of
  the base station too, so pick Astro A50 Game again when the game is done.

In the game's own audio settings, choose surround, 7.1 or System Default,
not stereo or headphones; a game with a headphone mode of its own is better
left on Astro A50 Game with that mode. A Proton game picks its channel count
once, when it opens its audio, so switch spatial sound on before the game
starts. With it off, a game with the launch option plays on the default
output in stereo; switched on later, it moves to Astro A50 Spatial still in
stereo, and the second line says to start it again. The launch option names
the output, and any program can make an output of that name: while spatial
sound is off, the switch's subtitle then says an Astro A50 Spatial runs that
Gearbench did not start, and the game plays into that program's output.

While a game plays on Astro A50 Spatial, keep the headset's Dolby button on
the star, which is Dolby off, or the base station processes the sound a
second time. No USB command reads the button, so the Dolby on the star row
reminds you: its icon turns to an amber warning sign while something plays,
and a toast says so when the Headset tab finds something playing that
its last look did not. A look that could not read systemd or PipeWire leaves
the icon as it was and shows no toast. The tab looks as it comes up and
every 5 seconds while it shows, so a game started while the window is
closed or on another tab gets the toast when you next open the tab, if it
still plays. A flatter EQ preset suits Astro A50 Spatial too: the head
measurements assume a neutral headphone, and Astro's ASTRO preset adds 7 dB
at 4 and 7 kHz, where the front and back cues are.

The head and the room are kept in `~/.config/gearbench/spatial.json`; on and
off is the service's enable link, kept by systemd. The service writes its
PipeWire configuration to `$XDG_RUNTIME_DIR/gearbench-spatial.conf` at each
start, from the head and room saved then, and logs to `journalctl --user
_SYSTEMD_USER_UNIT=gearbench-spatial.service`, the command its `Did not
start` subtitle names. `journalctl --user -u gearbench-spatial` also shows
what systemd says of the service, such as a start refused after five
failures, and any line another program of yours logs under its name.

### The headset's own buttons

- Dolby switches the base station's Dolby processing, Pro Logic IIx and
  Dolby Headphone, on the Game channel: the headphone icon is on, the star
  off. The button is its only switch. No USB command turns it on or reads
  it, so the tab has no Dolby switch and Gearbench cannot tell which state
  it is in. Keep it on the star while a game plays on Astro A50 Spatial.
- EQ switches the active preset in the base station. The Preset row
  follows within 5 s, or within 10 s while the window is not the active one.
  `gearbench-ctl headset` asks the base station when it runs, so its
  `preset:` line shows the new preset at once.
- Game and Voice move the live balance, which the Balance row shows within
  5 s, or within 10 s while the window is not the active one. Gearbench sets
  only the Default balance.

### Plugged in, unplugged or not answering

The daemon attaches a base station within about 5 s of it being plugged in,
and lets go of one unplugged, after which `gearbench-ctl headset` says no
base station was found. A base station found that does not answer is looked
at again after 5 s, then at doubling waits up to a minute; unplugging it for
more than about a second ends the wait, and once it is plugged back in it is
looked at within about 5 s.
One found that Gearbench cannot set up is looked at again every 5 s, and
`gearbench-ctl headset` and the tab say to unplug it and plug it back in.
One attached that stops
answering is sent nothing but a status request every 30 s until it answers.
Meanwhile the tab says Not answering, `gearbench-ctl headset` says when its
readings were taken, and a banner on the tab says to unplug the base station
and plug it back in. After a resume the daemon asks the base station again at
once.

### Without Gearbench running

The settings belong to the base station, and Gearbench keeps no copy. With
the daemon stopped, the headset keeps what the base station holds in its
memory, and after a power cycle what it holds in flash; stopping the daemon
sends the base station nothing. The low-battery notices and the tray's
headset line need the daemon.

## Idle, lock and suspend

The keyboard's lights go off while the screen is locked or blank, and a
mouse synced to the keyboard goes off with them; a mouse with its own color
keeps it. While the screen is still locked, any input brings them back: a
key, the pointer, or a G, M or MR key. After 30 s without input they dim to
half, and after 60 s they go off again, even while a game keeps the screen
from going idle. Unlocking brings them back. A keyboard its own idle dimming
has dimmed or turned off stays so until the next keypress; a synced mouse
comes back with any input. The brightness you chose is not touched: dim is
half of every color. A lighting change made while the lights are down is
saved and shows when they are back.

This is on by default. `gearbench-ctl lock-lights off`, or the Lights off
when locked switch on the Keyboard tab, turns it off. It follows GNOME:
logind's lock, GNOME's screen blank (which comes without a lock when locking
is off), and Mutter's idle monitor for the input. Under another desktop, the
lock works if the desktop sets logind's lock hint, and before the unlock
only a G, M or MR key brings the lights back. It needs `python3-gi`; without
it the lights stay on, and `gearbench-ctl status` and the switch's row say
why.

With the switch on, the lights go off before the machine suspends. The
daemon holds a logind delay inhibitor for this, which `systemd-inhibit
--list` shows as `Gearbench`. After the resume the daemon sets up the
keyboard and every mouse again, and the lights stay off while the screen is
locked.

The keyboard also dims when left alone: a minute after the last keypress it
dims to half, and five minutes after that its lights go off. The next
keypress brings them back. So does a lighting change from the window or
`gearbench-ctl`, which counts as activity at the keyboard: made while the
keyboard is lit, it starts the count to the dim again, so the keyboard does
not dim while you edit its lighting with the mouse. Reading the settings or
changing the mouse does not count. `gearbench-ctl idle --dim-after SECONDS
--off-after SECONDS` changes the two times, 10 to 3600 s each, and
`gearbench-ctl idle --never-dim on` keeps the lights from dimming or going
off. New times given while the keyboard is dimmed or dark wake it. The
Keyboard tab has the same under the Lights off when locked switch: Never dim,
and Dim after and Lights off after, which are grayed out while Never dim is
on. A time is sent once it has stayed put for 0.6 s, or when the window
closes.

When the idle dimming and the screen lock overlap, the darker of the two
shows. Locking over a dimmed keyboard turns it off. Unlocking without a key,
with the pointer or a fingerprint, leaves it dimmed until a key is pressed,
or off once Lights off after has passed since it dimmed. A keypress while
locked wakes both. A lighting change while locked wakes the keyboard from
its idle dimming but not from the lock: it is saved, and shows at the
unlock.

A mouse synced to the keyboard dims with it only when you have left both
alone. While you use the mouse it stays lit, even with the keyboard dimmed
or dark. Once nothing on the desktop has been touched for the keyboard's Dim
after time, a minute by default, the mouse takes the keyboard's level: half
if the keyboard has dimmed, off if the keyboard is already off, lit if it
has not dimmed yet. From then on it follows the keyboard, so it goes off
when the keyboard does. Moving the mouse lights it again, and the keyboard
stays as it is until a key; a key lights both. A game that keeps the screen
from going idle does not keep the mouse lit. With the keyboard switched off
or out of range, the mouse goes by the keyboard's times without it: half
after Dim after, and off once Lights off after has passed as well.

A new Dim after, Never dim, or Sync with keyboard switched on or off starts
the mouse over, lit. A new Lights off after alone changes only when it goes
off. Going by the keyboard's times, a mouse that is off already comes back
to half for a longer one that has not run out yet, and goes off once it
has; one that has run out already leaves it off. Switching
sync on when it is already on changes nothing. Never dim
keeps the mouse lit too. The mouse also goes off with the screen lock, the
blank and a suspend, and shows the darker of the lock's level and this one.
Gearbench learns that the desktop is idle from Mutter's idle monitor:
without `python3-gi`, or under a desktop that is not GNOME, a synced mouse
stays lit while the keyboard dims.

A lock, a blank or a suspend also stops a running macro, keeps any macro
from starting until the screen is back, and releases what the G keys, MR and
the mouse buttons hold, whatever the switch says (see "Macros"). A DPI shift
paddle held down through any of them still puts the DPI back when you let go
of it.

Switching to another account or a text console does the same, and until
your session is back on the screen no G key or mouse button binding types
either. The lights are left as they are, and the M keys still switch
profiles.

Stopping the daemon hands the keyboard back: it shows its own stored effect,
its own power saving turns it off a few minutes after the last key, and its
G keys type its onboard bindings. When the machine shuts down or reboots,
the daemon turns the keyboard's lights off instead, and a synced mouse's
with them, so the keyboard does not light up again with the PC off. A mouse
that is not synced gets its own lighting back, as at a stop. By 2026-10-03
this had run at reboots, not yet at a power-off. A keyboard the daemon has
found not answering, such as one switched off behind its receiver, is sent
nothing at either stop.

## Notices

Gearbench's desktop notices need `python3-gi`. Switching Gearbench off in
GNOME Settings, Notifications turns all of them off, and the DPI notices also
have a switch of their own. A tray menu choice the daemon refused comes back
as a notice, with the reason.

### Low battery

The daemon reads the G915 on its receiver once a minute, and a notice says
when the battery is below 30 %, and again below 15 %. Two readings in a row
have to be below. The percentage comes from the battery's voltage, which
rises when the lights go off, so a rise does not bring the notice back.
Plugging the cable in takes the notice down and arms both again, and so does
a reading of 50 % or more, for a charge the daemon did not see, such as the
keyboard charged while switched off. To see a notice without waiting, stop
the service (`systemctl --user stop gearbench`) and run
`GEARBENCH_ASSUME_BATTERY=29 gearbench-ctl daemon`: every reading for the
notices is then 29 %, and the notice comes a minute after the start. The
window, the tray and `gearbench-ctl status` still show the keyboard's own
reading. Ctrl+C stops that daemon, and `systemctl --user start gearbench`
starts the service again.

The headset gets the same two notices, from its battery readings (see "Status
and battery"), each saying how much is left and to put the headset on its
base station. Charging, putting the headset on its base station even while it is
switched off, or a reading of 50 % or more takes the notice down and arms
both again. `GEARBENCH_ASSUME_BATTERY` does not reach the headset.

### DPI changes

When a button bound to `dpi-up` or `dpi-down` steps the DPI (G8 and G7 on
the G502 X PLUS unless rebound), or the G305's DPI button moves it to
another stage, a notice gives the mouse and its new DPI. Each step replaces
the notice in place, and Gearbench takes it down 5 s after the last step. A
DPI set from the window, the tray or `gearbench-ctl mouse-dpi` posts
nothing, and neither does the paddle's shift or the DPI put back when a
mouse is switched on. DPI notices are on by default: `gearbench-ctl
dpi-notices off`, or the DPI notices switch under the DPI row, turns them
off.

## The tray icon

Where the desktop has a tray, an icon in the top bar holds the batteries and
a menu. Ubuntu's GNOME has a tray, its AppIndicators extension, on by
default. Stock GNOME has none: there the icon does not show, and nothing
else changes. `gearbench-ctl status` says which on its `tray` line. The icon
and its menu answer only the tray itself, the program that keeps the
desktop's list of icons, and under GNOME only GNOME Shell, so another
program on the session bus cannot ask for the menu or click it. When a line
of the menu changes, such as a battery level, a switch or the DPI stage, its
new text or state is still announced on the session bus, where any program
that is not a Flatpak app can read it, and so can a Flatpak app given the
whole session bus. A tray whose icons are drawn by a different program from
the one keeping that list is refused too, and shows no working icon.

The icon is Gearbench's own, a headset around a small screen. It turns into
a warning sign while anything is wrong, meaning anything `gearbench-ctl
status` lists under problems, and, with nothing wrong, into a low battery
while a keyboard, a mouse or the headset is below 15 % and not charging.
Where the tray shows tooltips, pointing at the icon gives the batteries and
the problems, a line each. Ubuntu's tray shows no tooltips, and the menu has
both.

A click opens the menu:

- Anything wrong, a line each with a warning sign: what `gearbench-ctl
  status` lists under problems. A line longer than 80 characters is shortened;
  `gearbench-ctl status` has the whole of it.
- Each device's battery, the headset's while it is on: `Astro A50: 91%`.
- Open Gearbench, which starts the window or brings it to the front.
- The selected mouse's DPI stages, the current one marked, while a mouse with
  stages is attached and answering. These are the stages in force: a loaded
  game profile's own in host mode, else onboard profile 1's. Choosing one
  sets it as `gearbench-ctl mouse-dpi` does.
- Game profile, once there is one: None unloads, a profile's name loads it,
  the loaded one is marked, and Keep loaded is the switch of that name (see
  "Hold-offs and Keep loaded").
- Lights off when locked and DPI notices, the switches of those names.
- Sync mouse with keyboard, the Mouse tab's Sync with keyboard switch, while
  a mouse with lighting is attached.

Opening the menu reads the selected mouse as the Mouse tab does, so its DPI
and battery are current. The icon and the menu follow changes made anywhere
else within 2 s.

The icon is on by default. `gearbench-ctl tray off`, or unchecking Tray icon
in the window's main menu, takes it away. It needs `python3-gi`.

## Moving from G HUB

G HUB has no per-profile export. It keeps every profile, binding and macro in
one SQLite database on Windows, `%LOCALAPPDATA%\LGHUB\settings.db`, which is
`C:\Users\<you>\AppData\Local\LGHUB\settings.db` on most machines. Beside it
there can be `settings.db-wal`, which holds your newest changes.

1. On Windows, quit G HUB from its tray icon, so nothing is writing the
   files.
2. Copy `settings.db`, and `settings.db-wal` if there is one, into one
   folder that Linux can read.
3. On Linux, choose Import from G HUB… in the window's main menu and pick
   `settings.db`, or run `gearbench-ctl import-ghub settings.db --list`.

Gearbench reads the database from a private copy and never writes it. A JSON
file holding the same document works too. Each file must be a regular file,
or a link to one: `settings.db` and `settings.db-wal` of at most 64 MiB, and
a JSON file of at most 16 MiB. The settings the database holds must fit in
16 MiB too. A file with more than 10,000 profiles or more than 10,000 cards
is refused, and SQLite's read of a database is stopped after 10 s, or once
SQLite holds 256 MiB of memory. Each refusal names the file, and a size or
count refusal names its cap.

The window lists the G HUB profiles found, named by their application, each
with its G keys, mouse buttons, lighting and the macros it uses, and a check
box. Under each macro is every step it holds, one a line, named as the
macro editor names them. The review page, `import-ghub --list` and
`--dry-run` list them too. A filter narrows the list, and Select all and
Select none act on the profiles the filter shows. Nothing is written until
you press Import on the review page that follows, which lists what will be
written. The daemon writes each file, so it has to be running.

What comes over:

- Each G HUB profile becomes a game profile: the G keys of its M1 bank, its
  G-Shift layer as the MR layer, the G502 X PLUS's buttons, and its per-key
  lighting, with the keys G HUB left out dark.
- A binding of one key and its modifiers becomes a plain binding. A sequence
  becomes a Gearbench macro, with G HUB's delays as delay steps; "Repeat
  while holding" becomes `while_held` and "Toggle" becomes `toggle`. Only
  the macros the chosen profiles use come over.
- Where you added the program to G HUB yourself, the profile can load for
  its `.exe`, but only when you ask: check Load for, the first line under
  the profile once you open it in the list, or give `import-ghub`
  `--with-programs`, which takes the programs of every profile it imports
  that G HUB names programs for. Otherwise the profile loads by hand, or,
  replacing a game profile of yours, keeps that profile's programs. Names
  match with `.exe` optional, so a profile for `firefox.exe` also loads for
  a Linux program named `firefox`, and a file from someone else could name a
  program you run every day. The list's Notes line and the review page say
  which way each profile loads. At most 64 programs come over per profile,
  each at most 255 bytes; Not imported says when a profile had more or a
  longer one. A game G HUB recognizes by itself carries no program name, so
  its profile loads by hand until you add one; under Proton the process can
  have another name, which `gearbench-ctl apps` shows while the game runs.
  When the programs come over, one that a game profile of yours already
  loads stays with yours, and the import comes without it. Two names are one
  program when one running game would load both profiles: Linux keeps a
  program's first 15 bytes as its process name, so under Proton
  `Warhammer 40000 Space Marine 2.exe` and
  `Warhammer 40000 Space Marine 2 - Retail.exe` are one. When two chosen
  G HUB profiles would load for one program, as two profiles of one
  application do, it goes to the one G HUB had active for that application,
  else the first in the list, and the other comes without it. In both cases
  the review page and the command line say which.
- The desktop's default profile replaces the G keys and the MR layer of your
  M1, M2 and M3 with its three banks, and gives all three its one set of
  mouse buttons, and its lighting if it has any. It comes over only when you
  check it, or name it with `--profile Desktop`, and nothing undoes it: copy
  `~/.config/gearbench/config.json` first if you may want yours back.

The list and `import-ghub --list` name what does not come over, including a
macro that acts when its key is released, G HUB's actions (OBS, audio
presets and the like), the wheel tilt and the mouse's G-Shift layer, a G key
disabled on the G-Shift layer alone (with MR held it sends its binding), a
game profile's M2 and M3 banks, lighting other than a per-key layout, and other
devices, 20 by name and then how many more. Control characters in G HUB's
names are dropped, a line or paragraph separator (U+2028, U+2029) and a
character that turns the direction of the text after it (U+202A to U+202E,
U+2066 to U+2069) show as U+FFFD, and application, profile and card names
are cut at 256 characters; Not imported lists each cut.

A G HUB game profile is already there when Gearbench has a game profile of
the same name with the same G keys, MR layer, mouse buttons, lighting and
macros: the review page and the command line say so in one line, and nothing
is written for it. Programs are not compared. Names match in any case, with
punctuation and symbols counted as spaces: "Apex: Legends" is "apex
legends", but a name with no letter or digit keeps its symbols. Keys match
in any case. Modifiers side by side before a binding's last key match in any
order: `shift+ctrl+q` is `ctrl+shift+q`, but `shift+ctrl` is not
`ctrl+shift`, whose last keys differ. The keys go down in the order the
binding names them, so `a+ctrl+b` is not `ctrl+a+b`. A macro with the same
steps and repeat mode under another name is the same macro.

Any other name Gearbench already has is asked about. A macro is not asked
about when yours of that name has the same steps and repeat mode: the
imported bindings use yours, and nothing is written for it. Replace writes over
yours, and a game profile keeps its own DPI stages and report rate. With
Load for or `--with-programs` it takes the import's programs, where G HUB
names any; otherwise it keeps yours, and the review page and `--dry-run` say
so, or that it loads by hand when yours has none. The list's Notes line,
written before you answer, still says it loads by hand. Keep both imports it
under a new name like "Apex Legends (2)", and Skip leaves it out. Keep both
is the window's default. A game profile kept beside yours loads by hand, so
it does not take its program over from yours. Skipping a macro keeps yours,
and the imported bindings use it. Two imported macros of one name are asked
about once at most, and are never both written under it: the second, when
it is written, comes in under a new name.

A game profile, or one of your M profiles from the desktop default, that
binds a macro which could not be saved is not written, since its key would
play your own macro of that name, or none. The window and the command line
list it, and the command line exits 1. When the daemon gave no answer to the
macro's save, as when it was busy for more than 10 seconds, the window says
it may not have been saved: the daemon can still save the macro once it gets
to it. The command line waits 3 seconds for each answer. At a request that
gets none it stops, sends nothing after it and exits 2. After any error the
command line prints what it wrote and the errors, and leaves out its lines
on what became of a name or a program.

```
gearbench-ctl import-ghub settings.db --list                  what the file holds
gearbench-ctl import-ghub settings.db --all --dry-run         what importing every game profile would write
gearbench-ctl import-ghub settings.db --profile "Apex Legends" --profile Desktop
gearbench-ctl import-ghub settings.db --all --on-clash keep   keep both wherever a name is taken
gearbench-ctl import-ghub settings.db --all --with-programs   each loads for the programs G HUB names for it
```

`--all` is every game profile, never the desktop default. Without
`--on-clash`, an import that meets a name Gearbench already has writes
nothing and lists the names.

## The command line

`gearbench-ctl` sends a request to the daemon and prints its reply. `--json`,
before or after the subcommand, prints the raw JSON reply instead. The exit
code is 0 when the command did what was asked, and 1 when the daemon refused
or the command refused its arguments or failed, with `error:` and the reason
on stderr, or with `--json` the reply on stdout. It is 2 when the daemon
could not be reached, did not answer in time, closed the connection without
answering or sent a reply that could not be read, and when the command line
did not parse, as with an unknown command or option, or a value of the wrong
type or outside its choices, such as `profile 4`.
`gearbench-ctl --version` prints the version. `probe`, `keys`, `config`,
`gui`, `daemon` and `headset-spatial` need no daemon, and neither do
`import-ghub` and `import-pack` until they import. `man gearbench-ctl` has
every option; from a checkout, `man ./man/gearbench-ctl.1`.

Names in the output come from devices, files, running programs and
PipeWire, and any of them can hold a character a terminal would act on or
not show. The formatted output prints each such character as its escape,
`\x1b` for ESC or `\u202e` for a right-to-left override; `--json` escapes
them itself. The names of running programs and of the streams playing into
Astro A50 Spatial show U+FFFD in place of such a character, in the window
too. A stream's name over 100 characters is cut to its first 99 and an
ellipsis. G HUB's names are changed as they are read (see
"Moving from G HUB").

The daemon and its state:

```
gearbench-ctl status                 what the daemon and the devices are doing, and any problems
gearbench-ctl gui                    open the window
gearbench-ctl daemon --verbose       run the daemon in the foreground with debug logging; a macro's steps show by type only
gearbench-ctl probe                  read-only dump of the keyboard; refuses while the daemon runs, unless --force
gearbench-ctl config                 the configuration on disk
gearbench-ctl save                   write the daemon's copy over a hand edit (see "Files")
gearbench-ctl reload                 read config.json, the loaded game profile and the macros again
gearbench-ctl tray off               no tray icon
```

`gearbench-ctl daemon` refuses to start as root: run it as yourself, not
with `sudo`. It refuses while another daemon runs too, the service's
included, and names the lock it found. Stop the service first
(`systemctl --user stop gearbench`).

The keyboard (see "The keyboard" and "Idle, lock and suspend"):

```
gearbench-ctl profile 2              switch the M profile, as M2 does
gearbench-ctl color '#ff2200'        the Static color, or the per-key background
gearbench-ctl preview '#ff2200'      repaint without saving, as a color drag does
gearbench-ctl effect wave            static, breathe, cycle, wave, ripple or disabled, with --speed and --direction
gearbench-ctl brightness 50          rounded down to 0, 12, 25, 50 or 100
gearbench-ctl bind G1 ctrl+f15       bind a G key; --shift for the MR layer, macro:NAME for a macro
gearbench-ctl keys volume            find key names; --modifiers lists the modifiers
gearbench-ctl lock-lights off        keep the lights on while the screen is locked
gearbench-ctl idle --dim-after 120   the idle dimming: --dim-after, --off-after, --never-dim; alone, shows it
```

Macros (see "Macros"):

```
gearbench-ctl macros                 list the macros, the running one starred
gearbench-ctl macro-run Spam         play a saved macro after 3 s (--delay SECONDS)
gearbench-ctl macro-stop             stop the running macro, however it started
```

Game profiles (see "Game profiles" and "Moving from G HUB"):

```
gearbench-ctl profiles               list the game profiles
gearbench-ctl load Shooter           load a game profile over the M profile
gearbench-ctl unload                 back to the M profile
gearbench-ctl let-load               end every hold-off and check again at once, as Load it now does
gearbench-ctl keep-loaded on         a profile loaded by hand stays when another profile's program gets focus or starts
gearbench-ctl save-profile Shooter --process shooter.exe   save what shows as a game profile
gearbench-ctl profile-programs Shooter shooter.exe         the programs that load a profile
gearbench-ctl rename-profile Shooter "Night shooter"
gearbench-ctl promote                make what shows the active M profile's default and unload the game profile
gearbench-ctl delete-profile Shooter
gearbench-ctl apps                   the programs you are running that a profile can name
gearbench-ctl import-ghub settings.db --list   what G HUB's database holds
gearbench-ctl import-pack --list     the game lighting profiles and macros that come with Gearbench
```

The mouse (see "The mouse"):

```
gearbench-ctl mouse                  the selected mouse: DPI, rate, profile 1, buttons, bindings
gearbench-ctl mouse-select G305      which mouse the mouse commands act on
gearbench-ctl mouse-dpi 1600         set the DPI live
gearbench-ctl mouse-bind G6 dpi-shift   bind G4 to G9: back, forward, dpi-shift, dpi-up, dpi-down, a key, a macro
gearbench-ctl mouse-defaults         put G4 to G9 back to their default bindings
gearbench-ctl mouse-color '#ff00aa'  the mouse's own color
gearbench-ctl mouse-effect breathe   an effect gearbench-ctl mouse lists, with --color
gearbench-ctl mouse-sync on          the mouse takes the keyboard's color
gearbench-ctl mouse-host off         the G502 X PLUS runs onboard profile 1, not host mode
gearbench-ctl dpi-notices off        no desktop notice when a DPI button steps the DPI
```

These write the mouse's onboard profile 1, after backing up every profile
sector. With a game profile loaded and `mouse-host on`, on a G502 X PLUS,
`mouse-rate` and `mouse-dpi-stages` save the game profile's own rate and
stages instead, and `--reset` drops them:

```
gearbench-ctl mouse-enable-buttons     make G4 to G9 send mouse buttons 11 to 16 (G502 X PLUS)
gearbench-ctl mouse-fallback-buttons   G4 to G8 as from the factory, G9 nothing, for use without Gearbench
gearbench-ctl mouse-rate 1000          1000, 500, 250 or 125 Hz
gearbench-ctl mouse-dpi-stages 800,1600,3200 --default 1600 --shift 800
gearbench-ctl mouse-dpi-stages --reset     the loaded game profile goes back to profile 1's stages
gearbench-ctl mouse-dpi-stages --factory   the stages, default and shift of the mouse's factory profile
gearbench-ctl mouse-restore            write this model's newest backup of profile 1 back
```

The headset (see "The headset"):

```
gearbench-ctl headset                the A50: on or off, battery, settings, EQ presets, what is not saved; --read reads them again
gearbench-ctl headset-set sidetone 40   mic-level, sidetone, noise-gate, mic-eq, stream-game, preset and the rest
gearbench-ctl headset-eq --gains 0,2,0,-1,3   an EQ preset's --name, --gains or --band 3:2750:1.5
gearbench-ctl headset-import night.astroeq   an .astroeq preset file into an EQ preset
gearbench-ctl headset-save           write the settings and presets to the base station's flash
gearbench-ctl headset-revert         put back what the base station's flash holds
gearbench-ctl headset-spatial        the spatial output: on or off, head, room, the launch option
gearbench-ctl headset-spatial on --room immersive   on or off, --head ku100 or kemar, --room competitive or immersive
```

When something goes wrong, start with `gearbench-ctl status` and `journalctl
--user -u gearbench`, or the Debugging Information page of About Gearbench,
under Troubleshooting. `-u` also shows any line another program of yours
logs under the service's name. The Debugging
Information page leaves those out: its journal section shows only the
daemon's lines and systemd's about it. RECOVERY in `man gearbench-ctl`
covers a mouse write or restore that failed, and a keyboard or headset left
in a bad state.

## Files

These live under `~/.config/gearbench/`
(`$XDG_CONFIG_HOME/gearbench/` when that is an absolute path):

- `config.json`: the three M profiles, each with its lighting and its G-key,
  MR layer and mouse bindings; which M profile is in use, and the game
  profile loaded by hand, which the next start loads again; the brightness
  and the saved colors; the settings the window and the commands set,
  `mouse_sync_color`, `lights_off_when_locked`, `idle_dim_after`,
  `idle_off_after`, `never_dim`, `dpi_notices`, `mouse_host_mode`, `tray` and
  the remembered `mouse_dpi`; and `keyboard_link_notices`, on by default and
  set only by hand, which has the keyboard's receiver say when the keyboard
  goes off or comes back.
- `profiles/`: the game profiles, one JSON file each, named after the
  profile: its name in lowercase, with its letters and digits kept and each
  run of anything else between them made one hyphen, as in `arc-raiders.json`
  for Arc: Raiders. A name with no letter or digit gets `profile-` and a short
  hash instead, and one that comes out longer than 200 bytes is cut short and
  given a hash at its end. Each file holds the profile's name, the programs
  that load it, its lighting, its G-key, MR layer and mouse bindings, and its
  own DPI stages and report rate per mouse model.
- `macros/`: the macros, one JSON file each.
- `backup/`: the mouse's onboard sectors, saved before each write.
  `mouse-restore` takes the newest backup only if it is a regular file, or a
  link to one, of exactly the mouse's sector size, 255 bytes, whose CRC
  checks.
- `spatial.json`: Astro A50 Spatial's head and room, written by the window
  and `gearbench-ctl headset-spatial`, never by the daemon (see "Spatial
  sound").

Each JSON file is read only if it is a regular file, or a link to one, of at
most 1 MiB. Anything else, a named pipe for example, counts as a file that
cannot be read: a game profile or macro is listed dimmed with the reason,
`config.json` leaves the daemon on the defaults at a start and is refused by
a reload, which keeps what the daemon holds, and `spatial.json` reads as the
defaults. A `config.json` that links to a file that is not there, in a
dotfiles folder not mounted yet say, counts as one too, and nothing is saved
over the link. A game profile's file that links to a file that is not there
is listed dimmed, and a new profile with its name, or a rename to that name,
is refused until the link is fixed or removed. A `profiles` folder that
cannot be listed, or that links to a folder that is not there or sits in a
folder that does, is listed dimmed as one row, and no profile in it counts
as deleted: a loaded one keeps what it held, and the one chosen to load at
the next start stays chosen until the folder can be read. A `macros` folder
like that is listed dimmed as one row in the Macros tab, and a key bound to
one of its macros starts nothing until the folder can be read. A save or
rename that would make a file larger than 1 MiB is refused, and nothing is
written. A save writes through a link to the file it names.

Where the service's sandbox applies, the only folders of yours the daemon
can write are `~/.config/gearbench` and `$XDG_RUNTIME_DIR`, so a save
through a link to a file in any other folder, in your home folder or outside
it, fails. With `XDG_CONFIG_HOME` set, the sandbox goes by the value
systemd's user manager started with. One set only in
`~/.config/environment.d`, or by the desktop session, reaches the daemon and
not the manager: the daemon logs an error at its start, and where the
sandbox applies nothing is saved. Set it for the manager too, with
`ManagerEnvironment=` in `~/.config/systemd/user.conf`, or leave it unset.

The headset's settings are in its base station, and none of them are kept
here. The daemon's control socket is `$XDG_RUNTIME_DIR/gearbench.sock`,
which refuses a request over 1 MiB, and its lock, which keeps a second
daemon from starting, is `$XDG_RUNTIME_DIR/gearbench.lock`. Astro A50
Spatial's PipeWire configuration, written at each start of its service, is
`$XDG_RUNTIME_DIR/gearbench-spatial.conf`.

The A50's audio files: `install.sh` writes
`~/.config/alsa-card-profile/mixer/profile-sets/gearbench-astro-a50.conf`,
`~/.config/wireplumber/wireplumber.conf.d/60-gearbench-astro-a50.conf`,
`~/.config/systemd/user/gearbench-spatial.service` and
`gearbench-spatial-login.service` beside it; the package installs the
same under `/usr/share/alsa-card-profile/`, `/usr/share/wireplumber/` and
`/usr/lib/systemd/user/`, with the spatial data in
`/usr/share/gearbench/spatial`. When `XDG_CONFIG_HOME` is an absolute
path, `install.sh` writes these four files under it in place of
`~/.config`.

The profiles and macros pack is read, never written, from `pack/` in a
checkout and from `/usr/share/gearbench/pack` when installed by the
package: `profiles/` and `macros/` there, in the same formats as yours,
and `notes.json`, the line shown under an item.

Every keyboard change and every mouse binding is saved as it is made, and a
color being dragged is saved at the release. Lighting and bindings go to the
loaded game profile's own file, or to the M profile in `config.json` when
none is loaded. The brightness, the saved colors, and the switches and times
of "Idle, lock and suspend" go to `config.json` whatever is loaded. The
mouse's stages and rate go to the loaded game profile's file on a
G502 X PLUS with `mouse-host on`, and otherwise to its onboard profile 1;
its own color and effect are not stored. A headset change waits for Save
(see "Saving"), except in the Spatial sound group: its switch acts at once,
and Head and Room go to `spatial.json` as they are chosen.

You can edit a file by hand, and the daemon does not write over your edit.
Once a file has changed on disk since the daemon read it, every save to that
file is refused, and the window shows a banner with Reload…. For a macro's
file, that covers a save, a rename or a delete of it, from the Macros tab,
the G HUB import or the pack's; a macro's file deleted by hand is written
again by a save. With the Game profiles dialog open, which covers the
banner, the refusal also shows over the dialog. While the file the lighting
on show is saved to, `config.json` or the loaded game profile's, is refused
this way, a color or paint dragged in the window is refused too and shows
nothing on the keyboard. The M keys still switch profile meanwhile. Which
one is active is saved to `config.json`, so it goes unsaved only while
`config.json` is the refused file.

Reload from disk on the Keyboard tab, or `gearbench-ctl reload`, reads
`config.json`, the loaded game profile's file and the macros again, and
writes the lighting and the M and MR LEDs again. What is loaded follows
`config.json`, as at a start: a profile loaded by hand stays if
`config.json` still names it; if it names another, that one loads, and if
that one's file will not load, nothing loads and `config.json` keeps the
name; a name no profile has, with no profile file named after it, is taken
out of `config.json` when every profile file loads; one `config.json` no
longer names is unloaded. A profile a program
loaded stays, and when it unloads, the one `config.json` names comes back.
If the loaded profile's name was edited in its file, it stays loaded under
the new name, as after a rename, and a hold-off and the choice to load it at
the next start follow the new name. While one of its programs has focus, or
is running where focus is not followed, the daemon follows the new name
within 5 s without a reload; the rest of the edit still waits for one. If
another file holds the new name and is the one read for it, the profile is
unloaded as one whose file has gone.

If `config.json` will not load at a reload, nothing changes, and the reply,
`gearbench-ctl status` and the window's banner give the file's error. Fix
the file and reload, or run `gearbench-ctl save` to keep what the daemon
holds.

If the loaded profile's file will not load, the profile keeps what it held,
and the reply, `gearbench-ctl status` and the window's banner give the
file's error until a reload reads it or `gearbench-ctl save` writes over it.
Until then a change to that profile is refused: fix the file and reload, or
run `gearbench-ctl save` to keep what the daemon holds. If its file has
gone, the profile is unloaded and not loaded again at the next start.

At a start, a profile loaded by hand whose name was edited in its file loads
under the new name when that file is the one named for the profile in
`config.json` (`doom.json` when it names Doom). Following an edited name
writes the new name to `config.json` and leaves the file where it was, so if
the name is edited again and nothing follows it before the next start, the
start cannot find the profile and forgets it. If the start cannot
find it and some game profile file will not load, it stays chosen, since
that file may be its own, and it loads at a reload or a later start once
that file loads.

A `lights_off_when_locked`, `mouse_sync_color`, `mouse_host_mode` or `tray`
changed in the file takes effect at a reload as `lock-lights`, `mouse-sync`,
`mouse-host` or `tray` would, and `idle_dim_after`, `idle_off_after` or
`never_dim` as `idle` would. Otherwise the mouse's onboard profile is left
alone, and in host mode the loaded game profile's own DPI stages and rate
follow its file.

`gearbench-ctl save` writes the daemon's copy over your edit instead. It
does not write over a `config.json` the daemon could not read at its start,
which left it on the defaults, and then saves neither file: fix
`config.json` and reload. It writes no macro, since the daemon keeps no copy
of one: a macro's file is saved over only after a reload.
