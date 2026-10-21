# User guide

This guide covers installing Gearbench, every control in the window and
every `gearbench-ctl` command. The README has the devices Gearbench drives,
the two ways to install it and how to ask for a device it does not drive yet.
`man gearbench-ctl` has every option; from a checkout,
`man ./man/gearbench-ctl.1`.

The daemon runs as your user service, `gearbench.service`, and owns the
devices. The window and the command line, `gearbench-ctl`, send it requests
over its control socket. This guide calls the service the daemon, as
`gearbench-ctl` and the window's messages do. A keyboard or mouse with a
cable and a wireless receiver can be on either; Gearbench finds out which,
and the window shows only what the attached device can do.

The sections:

- [First-time setup](#first-time-setup): the steps after an install.
- [Moving from G HUB](#moving-from-g-hub): G HUB's profiles, bindings and
  macros, read from its settings database copied over from Windows.
- [The window](#the-window): the four tabs, the main menu, and the banners
  that say what is wrong and what to do.
- [The keyboard](#the-keyboard): three M profiles, G keys and the MR layer,
  Static, Per-key and the keyboard's animations, keys painted by click or
  drag, and brightness.
- [Game profiles](#game-profiles): lighting and bindings that load while
  their game has focus, a Proton game's by its Windows `.exe`, and a pack
  with per-key lighting for 67 games and 10 macros.
- [Macros](#macros): up to 64 steps of keys, text, clicks and delays, added
  one by one or recorded, played once, while held or as a toggle.
- [The mouse](#the-mouse): the buttons G4 to G9, DPI and its one to five
  stages, host mode, the report rate, color sync with the keyboard, and
  onboard profile 1 with its backups.
- [Mouse models](#mouse-models): the G502 HERO and the seven mice built
  without one on hand, what each does and lacks.
- [The headset](#the-headset): the A50's microphone, EQ presets, base
  station and its Game and Chat outputs.
- [The Logitech G432](#the-logitech-g432): the G432's sidetone, equalizer
  and noise suppression.
- [Spatial sound](#spatial-sound): Gearbench Spatial, a 7.1 output that
  renders a game's surround channels for headphones with a measured head
  from the SADIE II set.
- [Idle, lock and suspend](#idle-lock-and-suspend): the keyboard dims a
  minute after the last keypress and goes dark five minutes after that,
  times you can change, and by default its lights go off while the screen is
  locked or blank.
- [Battery and DPI notices](#battery-and-dpi-notices): a low battery under
  30 % and again under 15 %, and the new DPI when a DPI button steps it.
- [The tray icon](#the-tray-icon): a menu in the top bar with the
  batteries, the DPI stages, the game profiles and the switches.
- [The command line](#the-command-line): every subcommand, by group.
- [Settings files](#settings-files): what Gearbench keeps in
  `~/.config/gearbench/`, and editing it by hand.
- [Install in detail](#install-in-detail), [The udev rules](#the-udev-rules)
  and [Upgrading and removing](#upgrading-and-removing): putting Gearbench on
  a machine, what its rules grant, and a new version or taking it off.
- [The models and their ids](#the-models-and-their-ids): each model's ids
  and sources.
- [Troubleshooting](#troubleshooting) and
  [Why and how we built it](#why-and-how-we-built-it): troubleshooting and
  reporting a problem, and how the project came about.

## First-time setup

The install started Gearbench's service, the daemon, in your desktop
session. The daemon owns the devices; the window and `gearbench-ctl` send it
requests. From then on it starts with each graphical login and stops at
logout, which hands the keyboard back to its onboard profile.

1. Run `gearbench-ctl status`. It says what the daemon found, the keyboard
   with its connection, and lists anything wrong under `problems`, a line
   each. If the service was stopped, `systemctl --user start gearbench`
   starts it. If `problems` lists `no virtual keyboard` with the rules
   installed, the daemon could not open `/dev/uinput`. When
   `getfacl /dev/uinput` lists no `user:` line with your name, the rule has
   not reached the node yet, as happens where uinput is a module not loaded
   yet. It applies at the next boot, or at once after:

   ```
   sudo modprobe uinput
   sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=add
   ```

2. Open the window: Gearbench in the app grid, or `gearbench-ctl gui`.
3. With a keyboard, set the Keyboard tab's Mode to Per-key and click a key
   on the drawn keyboard: it lights at once (see "Painting keys").
4. Coming from Windows, Import from G HUB… in the window's main menu brings
   over G HUB's profiles, bindings and macros (see "Moving from G HUB").
   Import from G HUB before you bind G keys by hand: its desktop default, if
   you check it, replaces the G keys, MR layer and mouse buttons of M1 to M3,
   and a G915's lighting if the default has any, and nothing undoes it.
5. Bind the G keys, which ship unbound. On the Keyboard tab, turn on Map G
   keys and click a G key (see "G keys and the MR layer"), or run
   `gearbench-ctl bind G1 f15`.
6. On a G502 X PLUS with host mode off, G4 to G9 need your account in the
   `gearbench` group. With the package, run
   `sudo usermod -aG gearbench $USER` before you log out; `install.sh` has
   done it. With lingering on, only restarting the machine gives the daemon
   the group.
7. Log out of every session and back in, so GNOME Shell loads Gearbench's
   extension, which tells the daemon which window has focus. With the
   package, the window (step 2) then has a banner with Turn on, which
   enables the extension; `install.sh` has enabled it already. Until the
   extension runs, game profiles follow only X11 windows, a Proton game's
   among them unless it runs Wine's Wayland driver. Once it runs, the
   `focus:` line of `gearbench-ctl status` names the program you are in (see
   "Following the game in front").
8. On a G502 X PLUS, G4 to G9 work at once while Gearbench runs. Write the
   fallback once. Without Gearbench, at the login screen or on another
   computer, it makes G4 to G8 do what they did from the factory: back,
   forward, the DPI shift and the two DPI steps. It makes G9 do nothing,
   though from the factory G9 cycles the onboard profiles. Choose Write
   fallback… under Onboard profile 1 on the Mouse tab, or run
   `gearbench-ctl mouse-fallback-buttons`. It is written into the mouse, so
   each G502 X PLUS needs its own. With host mode turned off, run Enable…
   (or `gearbench-ctl mouse-enable-buttons`) instead, so G4 to G9 reach
   Gearbench. Without Gearbench they then do nothing, G4 and G5 included
   (see "The buttons, G4 to G9"). The G305 needs nothing.
9. For the A50, set the base station's switch to PC. With Gearbench's audio
   profile, GNOME's Sound settings list two outputs, Astro A50 Game and
   Astro A50 Chat, and a microphone, Astro A50 Chat. Games go on Game and
   voice chat on Chat: set your voice chat program's output to Astro A50
   Chat in its own settings. Game becomes the default output unless another
   one was chosen before. If Chat was, pick Astro A50 Game in Sound settings
   (see "Game and Chat").
10. For lighting made for a game, choose Import pack… at the top left of
    the Game profiles dialog: per-key lighting for 67 games and 10 macros,
    imported only as you choose (see "The profiles and macros pack").

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

The window lists the G HUB profiles found, each with its G keys, mouse buttons,
lighting and the macros it uses, and a check box. Each is named by its
application, or, for a profile with a name of its own in an application G HUB
has more than one profile for, by both, as "Apex Legends: Ranked"; that is the
name `--profile` takes. Under each macro is every step it holds, one a line,
named as the macro editor names them. The review page, `import-ghub --list` and
`--dry-run` list them too. A filter narrows the list, and Select all and
Select none act on the profiles the filter shows. Nothing is written until
you press Import on the review page that follows, which lists what will be
written. The daemon writes each file, so it has to be running.

What comes over:

- Each G HUB profile becomes a game profile: the G keys of its M1 bank, its
  G-Shift layer as the MR layer, the buttons of each mouse G HUB has, and its
  per-key lighting, with the keys G HUB left out dark. Where a profile has a
  G915 TKL, its G1 to G12 come over as its F1 to F12, beside the G915's G1
  to G5. A G815's G1 to G5 come over as the G915's would; where both
  keyboards hold different bindings of yours on one key, the G915's comes
  over and Not imported lists the G815's. No G HUB file with a G915 TKL or
  a G815 in it has been tried yet.
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
  M1, M2 and M3 with its three banks, and gives all three its mouse buttons,
  for each mouse G HUB has buttons for, and its lighting on a G915 if it has
  any; your other mice keep their bindings, other keyboards keep their
  lighting, and a G915 TKL keeps its F1 to F12 unless the desktop default has
  that keyboard. It comes over only when you
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

Gearbench reads the database from a private copy and never writes it. A JSON
file holding the same document works too. Each file must be a regular file,
or a link to one: `settings.db` and `settings.db-wal` of at most 64 MiB, and
a JSON file of at most 16 MiB. The settings the database holds must fit in
16 MiB too. A file with more than 10,000 profiles or more than 10,000 cards
(what G HUB puts on a key, a button or a profile, such as a keystroke, a
macro or the profile's lighting) is refused, and SQLite's read of a database
is stopped after 10 s, or once SQLite holds 256 MiB of memory. Each refusal
names the file, and a size or count refusal names its cap.

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
yours, though a game profile keeps its own DPI stages, report rate and
lighting on keyboards other than the G915. With
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
`--on-clash`, an import or a dry run that meets a name Gearbench already has
lists the names and exits 1, writing nothing and showing no plan.

## The window

![The window on its Keyboard tab, with its four tabs and the main menu's button](images/hero.png)

The four tabs at the top, the main menu at the top right.

Open the window with `gearbench-ctl gui`, from Gearbench in the app grid, or
with Open Gearbench in the tray icon's menu. It has four tabs, Keyboard,
Mouse, Headset and Macros, and always opens on the Keyboard tab.

The window shows the daemon's live state, so a change made with an M key or
with `gearbench-ctl` shows in it too. An M key, a program, the tray icon's
menu or `gearbench-ctl` can switch the profile before the window shows the
switch. The daemon refuses a change made in the window in that gap, since it
would land on a profile the window is not showing. A toast, or a dialog when
the message is too long for one, says where the keyboard is now, and the
window moves to that profile. While a game profile stays loaded, changes go
to it. So after a switch of M profile the daemon refuses only a rename and
Set as M default, the two changes that go to the M profile.

While the window is on screen, focused or not, it reads the daemon's state
every 3 s, and the mouse's and the headset's every 5 s. It does not read
them while it is minimized, covered, on another workspace or behind the lock
screen. It asks the mouse itself only while the Mouse tab shows in the
active window, and the base station only while the Headset tab does.
Otherwise it shows what the daemon last read. The daemon also reads the base
station every 5 s on its own (see "Status and battery").

The main menu, at the top right, has four entries:

- Game profiles… opens the Game profiles dialog (see "Game profiles"),
  whose Import pack… opens the profiles and macros pack.
- Import from G HUB… brings over G HUB's profiles, bindings and macros (see
  "Moving from G HUB").
- Tray icon turns the tray icon on and off (see "The tray icon"). The entry
  shows once the daemon has answered, since the switch is the daemon's
  setting.
- About Gearbench has a Troubleshooting page. Its Debugging Information row
  opens what a bug report needs, with Copy Text and Save As: the version,
  the daemon's `status` reply and its problems, whether you may open each
  device's hidraw node and `/dev/uinput`, what systemd and PipeWire say of
  spatial sound, and the daemon's last 30 journal lines.

### The device tabs

A device tab shows only the controls the attached device has: a G305 gets
no lighting rows, no button rows and no Shift stage row.

Until the window has seen a device, its tab shows Looking for the keyboard…
(or the mouse, or the headset), then Keyboard not available (or Mouse, or
Headset) with the reason. The reasons include no device found, a device or
receiver that does not answer, and no permission to open its node. No
permission means its udev rule is missing or not applied, or another account
or a text console has the seat. While the daemon does not answer, the tab
says Waiting for the daemon. With the window on screen, a mouse can take up
to about 40 s to appear after you switch it on. Once the window has seen a
device, the tab keeps its rows after the device goes, until the window
closes.

Once a device is seen, the tab's first group is named after it. On the
Keyboard and Mouse tabs, its Status row has a green check mark while the
device is there. It has a red error sign once the device is gone or while
the daemon does not answer. Once a device the daemon has reached stops
answering, the row has an amber warning sign and says what to do: press a
key on the keyboard, move the mouse, or switch it on. A device the daemon
has not reached, such as one switched off when its receiver was plugged in
or the daemon started, shows the daemon's reason instead. The keyboard's
Status row then has an amber warning sign, and the mouse's a red error sign,
even with its receiver plugged in. The row says On its cable or Through its
LIGHTSPEED receiver, with the `/dev/hidraw` node in its tooltip.

Gearbench was built for some keyboards and mice without one on hand, such as
the G815 or the PRO WIRELESS. For those, the first group also has a Not yet
checked on this model row. A device report from yours shows what works and
what to fix: its Device report button opens the form in your browser, and
`gearbench-ctl report`, run in a terminal, prints what to paste into it (see
"Reporting a device").

The Battery row gives a percentage and a state, amber at 30 % and red at 15
% while not charging. The G305 reports only a coarse level, shown as "about
90%". The G915 and the G915 TKL report only their voltage. The percentage
comes from Logitech's own curves for the G915, one on battery and one
charging. A G515 LIGHTSPEED TKL through its receiver reports its percentage
itself. The charging curve ends at 91 %, so above 4.10 V while charging the
G915 and the G915 TKL read "charging, nearly full", with no number.
`gearbench-ctl status` prints the voltage beside it.

The G502 X PLUS's battery is the last level it reported by itself, or the
level read before its first report. The G305's is the level last read from
it. A mouse that does not answer shows its last reading with the time it was
taken, and the date too when that was not today. The rows that write to it
are grayed out until it answers. A mouse that has gone, or one the daemon
cannot be asked about, shows no reading.

While the keyboard is away or not answering, a banner says Changes are saved
and will show when the G915 is back, with your keyboard's model in place of
G915.

The first group of the Headset tab's A50 page belongs to the base station.
The group shows a green check mark with Headset on, and an amber warning
sign with Headset off or out of range or with Not answering. It shows a red
error sign with the reason once the base station is gone or while the daemon
does not answer. The rows that change the base station's settings are grayed
out while it does not answer or while it saves. See "Status and battery"
under "The headset".

### Banners and messages

A banner under the header stays up while one of these lasts:

- The daemon is not running. The banner says the G keys type their onboard
  bindings while the daemon is down, or may type nothing if it was killed.
  It has Start it, which runs `systemctl --user start gearbench`. After a
  failed start, the banner gives what systemctl said, or says systemctl did
  not finish in 30 s, or the daemon exited at once or did not listen within
  5 s.
- The daemon runs and did not answer in time, busy with a mouse profile
  write, for example, or sent a reply that could not be read. The banner has
  no button, since starting the daemon again would do nothing.
- The control socket cannot be used, as when another user owns it. The
  banner gives the error.
- No virtual keyboard or mouse: the daemon could not open `/dev/uinput`.
  Its udev rule is missing (see "The udev rules"), or the rule is installed
  and has not reached the node yet (see "First-time setup"). A G915 TKL
  then keeps its own profile, and the Keyboard tab says so.
- Another program that drives these devices is running: the banner names
  each one found, with its process id and what to do. Once it is gone,
  `systemctl --user restart gearbench` sets each device up again.
- `config.json` or the loaded game profile's file could not be read, or one
  of them or a macro's file was edited on disk. The banner has Reload….
- A macro is running or counting down to its start. The banner has Stop.
- Gearbench's GNOME Shell extension is installed but not running, so game
  profiles cannot follow every window. The banner says what game profiles
  follow without it: only X11 windows or, with no X server, the running
  programs. It also says why the extension is not running: off, with Turn
  on, which asks GNOME Shell to enable it; not loaded until you log out and
  back in; failed, with GNOME Shell's error; not made for this GNOME; or
  GNOME's extensions switched off, which the Extensions app turns on. A Turn
  on that GNOME Shell refuses stays on the banner with its reason.

The Mouse tab has a banner of its own, with a line for each problem the
daemon lists for a mouse:

- an onboard profile other than 1 running
- host mode that did not take or left the mouse's programmable buttons dead
- G4 to G9 unreadable from the mouse's event node
- keys arriving on the G502 X PLUS's event node in host mode (see "The
  mouse")
- while the tab shows a mouse, another mouse whose node Gearbench has no
  permission to open

A refused change comes back as a toast when it fits on one line, and as a
dialog when it does not. A change refused because `config.json`, the loaded
game profile's file or a macro's file was edited on disk, or one of the
first two could not be read, goes on the banner with Reload… instead. While
the Game profiles dialog is open, which covers that banner, it also comes
back over the dialog as a toast or a dialog. A device failure is said in
plain words, with the raw error behind Details on a toast and shown under
the words in a dialog. A note too long for a toast, such as the one a mouse
write gives in host mode, shows its first sentence or a short summary, with
the whole note behind Details. A dropdown entry that needs explaining has a
tooltip.

Closing the window waits for a change still on its way, such as a headset
setting, a mouse write, a color, a macro save, a Spatial sound action or a
switch of the G432's equalizer. Closing again closes at once. A change
refused then keeps the window open, with the reason shown and the row put
back. The next close asks again about unsaved macro changes, even those you
chose to discard. A change the daemon never got, because it is not running
or its socket could not be used, keeps the window open too, under the banner
that says so. A Spatial sound action, or a change to the G432's equalizer,
its Noise suppression or the A50's saved presets, that fails then does not
keep the window open. Macro changes made while it waits are asked about
before the window closes, unless a macro save or delete is still on its way.

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
oldest. A `config.json` edited by hand to list more does not load. To remove
one, right-click it, or focus it and press Delete, Backspace, the Menu key
or Shift+F10; the window asks first.

## The keyboard

![The Keyboard tab](images/keyboard.png)

The Keyboard tab, from the top: while two keyboards are plugged in, a
switcher that picks the keyboard (see "One keyboard's lighting, another's");
the G915 group with Status and Battery; the
M profile group, with Active, Name and Reload from disk (see "Settings files"); the
Game profile group (see "Game profiles"); the Lighting group's Mode row,
with Speed under an animation and Direction under Wave; the drawn keyboard,
with Map G keys; then the color picker with its Saved colors row, Groups,
Background, Brightness, and the switches and times of "Idle, lock and
suspend".

The keyboard on its receiver is set up again at once when it is switched on,
and moves to its cable when that is plugged in.

A G515 TKL keyboard, the wired one or the G515 LIGHTSPEED TKL on its cable,
works too. The wired one has been tested on the hardware (2026-10-17 and
2026-10-18), and the LIGHTSPEED one has not. Gearbench drives one keyboard
at a time. With two plugged in, it connects the model the mouse follows (see
"One keyboard's lighting, another's"), and until one is chosen or connected,
a G915 or its receiver comes first. A keyboard switched off with its
receiver plugged in counts as unplugged: the other one is connected
meanwhile, when Gearbench starts too. So does a G915 TKL the daemon leaves
on its own profile because it has no virtual keyboard: once it can be set
up, it replaces the other only if it is the one picked. Picking the other at
the top of the Keyboard tab, or with `gearbench-ctl sync-keyboard`, while
both are plugged in, hands the one connected back to its own profile and
connects it at once.
A G915 TKL the daemon would leave on its own profile is the exception: with
no virtual keyboard, or while another account or a text console has the
screen, the one connected stays until that ends. Plugged in later while
another is connected, the one picked replaces it the same way: the daemon
looks for it every 2 seconds. Picked while it is
switched off with its receiver plugged in, it replaces the one connected
once it is switched on, and `gearbench-ctl sync-keyboard` says so: "The G915
is driven once it is switched on." With none picked, one plugged in while
another is connected waits until that one is unplugged.

![The top of the Keyboard tab with two keyboards plugged in: the G915 grayed and switched off, the G515 TKL connected](images/keyboard-switcher.png)

The G515 has no G keys, M keys or MR, so its tab draws its own 87 keys and
Game Mode, with no Map G keys. Groups leaves out G-keys, Keypad, Media and
Logo. Its M profiles are switched from the M profile group and the tray
icon's menu (see "M profiles"). The wired G515 has no battery, so its group
has no Battery row and the tray menu no battery line for it. A profile can
hold a color for a key the G515 lacks, such as the logo, a media key, the
keypad or a G key. That color stays in the profile and is not sent to the
keyboard.

The G515 LIGHTSPEED TKL works through its receiver too, on a node of its own
that its udev rule grants. Its group has a Battery row with the percentage
the keyboard reports, and it gets the G915's low-battery notices (see "Low
battery"). A cable plugged in moves it to its cable. That node also carries
what is typed on the keyboard, so with the rule installed a program you run
can read what you type on the G515. Gearbench drops those reports unread.

A G915 TKL, on its cable or its receiver, works too. It was built from its
firmware, the key layout G HUB draws for it and published listings of its
features. None of it has run on a G915 TKL yet. The G913 TKL has the same
ids and is taken for one. A G915 or a G515 plugged in comes first, unless
the G915 TKL is the model the mouse follows.

The G915 TKL's tab draws its own keys, with its logo, media keys and
brightness button, and Groups leaves out G-keys and Keypad. Its G1 to G12
are its F1 to F12. These type only through the keyboard's own profile, so
while the daemon runs it types them itself, through its virtual keyboard.
Each one binds as the G915's G keys do, with Map G keys, on the MR layer and
in a game profile. It types its own F key until it is bound; unbinding it
brings that back. They bind under their own names, F1 to F12, so a binding
made for the G915's G1 leaves the G915 TKL's F1 typing F1.

While the daemon has no virtual keyboard, because it cannot open
`/dev/uinput`, it leaves the keyboard on its own profile, so F1 to F12 work.
It then drives none of the keyboard's lighting or keys, and
`gearbench-ctl status`, the tray and the Keyboard tab say why. Once the
daemon can open `/dev/uinput`, the keyboard is set up within about 30
seconds. Its M1 to M3, printed under F1 to F3, switch M profiles as on the
G915; how the keyboard sends them has not been checked. When the daemon
stops, at a shutdown or reboot too, it hands the keyboard back to its own
profile, and F1 to F12 type as before. A daemon stopped without that, by a
crash or a kill, leaves F1 to F12 typing nothing until it starts again or
the keyboard is switched off and on. While another account or a text console
has the screen, the daemon hands the keyboard back the same way, so F1 to
F12 type there. It sets the keyboard up again once your session is back.

Nothing is written to the G915 TKL's onboard memory, and over Bluetooth it
is not driven. Linux 7.3 or later, going by the kernel's source, gives the
keyboard a node of its own behind its receiver. That node also carries what
is typed on the keyboard, so with its udev rule installed a program you run
can read what you type on the G915 TKL. Gearbench drops those reports
unread. A color a profile holds for the keypad or a G key stays in the
profile and is not sent to the keyboard.

A G815, on its cable, works too, and the G813, which has the same id, is
taken for one. It was built from published listings and logs of its features
and its quick-start guide. None of it has run on a G815 yet. A G915, a G515
or a G915 TKL plugged in comes first, unless the G815 is the model the mouse
follows. It works as the G915 does: G1 to G5 and their MR layer bound in the
same way, M1 to M3, the same lighting, effects and brightness, and every key
a G915 profile paints. Its tab draws the G915's keys with its own top row,
Game Mode and Brightness where the G915 has LIGHTSPEED and Bluetooth. The
G815 has no battery, so its tab has no Battery row. Its control interface
also carries what is typed on it, so with its udev rule installed a program
you run can read what you type on the G815. Gearbench drops those reports
unread. Nothing is written to its onboard memory. Whether it dims by itself
after its idle time has not been checked.

### M profiles

M1, M2 and M3 on the keyboard switch between three M profiles, and the M
LEDs show which one is active. Each M profile has its own lighting, G-key
bindings, MR layer and mouse button bindings. On the Keyboard tab, the
Active row switches the M profile and the Name row renames the one shown.
`gearbench-ctl profile 2` does what M2 does.

A keyboard with no M keys, the G515, has the same three M profiles. Switch
them from its M profile group or, while the keyboard is attached, from the
tray icon's menu, whose M profile submenu lists them with the active one
marked. The daemon saves the active M profile, and a game profile loaded by
hand, as they change, so logging in restores both. A game profile loads on
top of the M profile; see "Game profiles".

An M profile's name cannot hold a control character (Unicode category Cc,
such as ESC or BEL) or run past 1,000 characters. The rename is refused with
the reason, and a `config.json` holding such a name does not load.

### G keys and the MR layer

G1 to G5 ship unbound. A G915 TKL's G keys are its F1 to F12, each typing
its own F key until it is bound. To bind one, turn on Map G keys over the
drawn keyboard. Each G key then shows its binding under its name,
`Macro: name` for a macro, when the drawn keyboard is big enough to fit it.
In a narrow window the key shows its name alone. Its tooltip always has the
whole binding, and hovering M1 to M3, MR, Game, BT or Host says what that
key does. Click a G key for the Press a key dialog:

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
  either. The Ctrl, Shift, Alt and Super boxes, each the left-hand key, go
  with the key you pick, except a box for that same key. Shift with Left
  Shift gives `leftshift`, and Shift with Right Shift `shift+rightshift`.
  The list is how the window binds Escape and Super alone;
  `gearbench-ctl bind` binds the kernel's other key names.
- Use a macro… binds the key to a macro. Back returns to Press a key.
- Unbind clears the key's binding.

From the command line: `gearbench-ctl bind G1 volumeup`, or on a G915 TKL
`gearbench-ctl bind F5 volumeup`.

MR is a shift layer while you hold it, with its LED lit. A G key then sends
its MR binding, or its base binding when it has no MR binding. The window
has no editor for the MR layer; bind it with
`gearbench-ctl bind G1 f15 --shift`.

### Binding syntax

A binding is a key name with optional modifiers: `f15`, `volumeup`,
`ctrl+shift+f5`, `rsuper+f15`. A modifier alone is a binding too: `shift`,
`ctrl`, `alt`, `super`, or the right-hand `rshift`, `rctrl`, `ralt` and
`rsuper`. So are several keys, `a+b+c` or `ctrl+a+b`: the keys go down in
the order the binding names them and come up in reverse.

In a binding or a macro, once Print Screen goes down with Alt held, a key
other than Alt, Shift or Print Screen is left up until Alt comes up. The `b`
of `alt+sysrq+b` is one such key: Linux would take it as a magic SysRq
command, and on Ubuntu `b` restarts the computer at once, without saving
your work. The journal names the key left up. Alt with Print Screen alone
still goes through. A G key or mouse button bound to `alt+sysrq+b`, or a
macro's `key_down` step of it, still holds Alt and Print Screen. A macro's
`key` step of it presses none of its keys.

A binding names each key once, so `a+a` is refused. A `+` needs a key on
each side, so `ctrl++` is refused too: the plus key is `shift+equal` on a US
layout, or `kpplus` on the keypad. `macro:NAME` runs a macro, and is refused
when the name holds a control character or runs past 1,000 characters.
`none`, `clear` or `off`, in any case, clears a G key with
`gearbench-ctl bind` and a mouse button with `gearbench-ctl mouse-bind`.

No name binds the Clear key, `key_clear` included, since it is too easily
confused with `clear`. A `config.json` or game profile file that binds it
does not load, and neither does one holding any other binding the command
line refuses. A binding in either file goes in quotes: `"G1": "5"` binds the
5 key, and a file with `"G1": 5` does not load. In a file, a G key with no
binding is `null`, and a G915 TKL's F key with no binding is left out.
`none`, `clear` and `off` unbind on the command line only. A file that gives
one of them to a G key does not load, and neither does one that gives a
mouse button `clear` or `off`. `none` is a mouse button's action, and the
default for G9 on the G502 models.

Key names are the kernel's, from a copy of its key table taken from Linux
7.0. Where `/usr/include/linux/input-event-codes.h` is installed (package
`linux-libc-dev`), it adds the names a newer kernel defines; bindings and
macros do not need it. `gearbench-ctl keys` lists them,
`gearbench-ctl keys volume` the ones that match, and
`gearbench-ctl keys --modifiers` the modifiers. The list spells a name as
the kernel does, `KEY_VOLUMEUP`. A binding takes it in any case, with or
without the `KEY_`, so `volumeup` is the same key. The same bindings and
macros work on the mouse's G4 to G9.

### How the keys behave

- A G key bound to a key holds it as long as you hold the G key, so it can
  walk, crouch or push to talk. One bound to a macro follows the macro's
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
  touch. Game Mode turns off the Super and Menu keys: if Super does nothing,
  the Activities overview included, press the Game key again.
- With the daemon stopped, the keyboard runs its onboard profiles. A G915's
  G1 to G5 then type F1 to F5 on M1, 1 to 5 on M2 and 6 to 0 on M3. A G915
  TKL's F1 to F12 type F1 to F12. At a shutdown or reboot the daemon turns
  the lights off and, but for a G915 TKL, leaves the keyboard off its
  onboard profiles, and a killed daemon can leave it off them too. Off
  them, its G keys type nothing.

### Lighting

The Mode row picks what the keyboard shows: Static, one color for the whole
keyboard; Per-key, keys painted one by one; or one of the keyboard's own
animations, Breathe, Cycle, Wave and Ripple. The G915 and the wired G515 TKL
have all four. The G515's Cycle, Wave and Ripple are its own versions of
them, each checked on it on 2026-10-18. The row lists only the animations
the keyboard has. A profile saved with an animation the keyboard lacks shows
its Static or Per-key board there, and the Mode row shows that one.

The lighting holds only while the daemon runs. When it stops, the keyboard
goes back to its own lighting, except at a shutdown or reboot: then the
daemon turns its lights off (see "Idle, lock and suspend").

`gearbench-ctl effect` takes `static`, `breathe`, `cycle`, `wave`, `ripple`
or `disabled`, each one the keyboard has. `gearbench-ctl status` lists them
on its `effects` line, and an effect the keyboard lacks is refused with that
list. `gearbench-ctl effect static` brings back whichever of Static and
Per-key the profile was on. `gearbench-ctl effect disabled` turns the
keyboard black, with a Per-key layout's painted keys still showing, and the
window draws the profile's Static color and background as black. Choosing
Breathe or Ripple then runs the animation in the profile's stored Static
color and moves the picker to it. Static then comes back in that color.

An animation shows a Speed slider, slower to the left. Its subtitle is the
length of one cycle in milliseconds, as `gearbench-ctl effect --speed` takes
it: 1000 to 20000 for breathe, cycle and wave, and 100 to 1000 for ripple.
The defaults are 3000 for breathe, 2100 for cycle and wave, and 1000 for
ripple. An effect set without `--speed` runs at the speed saved for it, or
the default.

Wave also has a Direction row, horizontal until you choose one. Each
keyboard takes six directions: Left to right, Right to left, Top to bottom,
Bottom to top, From the center out and From the edges in
(`--direction horizontal`, `reverse-horizontal`, `vertical`,
`reverse-vertical`, `center-out` and `center-in`). All six have been checked
on the G915 and on a wired G515 TKL.

Breathe and ripple run in the Static color, which the picker sets. Cycle and
wave have no color, and the picker is grayed out. The G915's logo waves with
the keys under wave, and stays steady in the Static color under ripple. Each
profile saves its mode, each animation's speed and wave's direction.

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
Keyboard. Each key gives its name and its color, or with Map G keys on, what
its tooltip says.

Static and the animations hide the painted keys and the background, and keep
both: switch back to Per-key and the layout comes back, after a restart too.
Static comes back in its own Static color. `gearbench-ctl color` sets the
background in Per-key mode with no animation running and the Static color
otherwise. Under cycle, wave or `disabled` the keyboard does not show it.
`gearbench-ctl config` shows each M profile's mode, how many painted keys it
holds, and its background when that differs from its Static color.

### Brightness

The Brightness row, or `gearbench-ctl brightness`, sets the backlight,
rounded down to a level the firmware keeps: on the G915, 0, 12, 25, 50 or
100 %. The keyboard's own brightness key works too, and the daemon saves the level it
sets. A mouse synced to the keyboard follows the brightness (see "Lighting
and color sync"). Each keyboard model keeps its own brightness. One that has
none starts at the level of the first model that has one, in the order its
lighting is copied in (below), or at 50 %.

### One keyboard's lighting, another's

Each M profile and each game profile keeps its lighting for each keyboard
model, the same on its cable and its receiver: the G915, the wired G515 TKL,
the G515 LIGHTSPEED TKL, the G915 TKL and the G815 are five models. A
keyboard with no lighting of its own in a profile shows another model's, cut
to what it can show: an effect it lacks shows as Static or Per-key,
whichever the copied lighting was on before that effect, and only the keys
it has are painted. It copies the model the mice follow: the
one picked with the switcher at the top of the Keyboard tab, or with
`gearbench-ctl sync-keyboard`, or with none picked the keyboard connected
first since the daemon started. The switcher shows while two keyboards are
plugged in, with a button for each; a keyboard that is not plugged in, or
the keyboard connected first, is picked with `gearbench-ctl sync-keyboard`.
A keyboard switched off with its receiver plugged in keeps its button,
grayed out with "Switched off" under its name, and can be picked once it is
switched on. While the window shows, Gearbench asks each keyboard it does
not drive whether it answers, every 2 to 3 seconds.
Where the model followed has no
lighting in the profile either, it copies a model with the same keys, else
the first of the G915, the wired G515 TKL, the G515 LIGHTSPEED TKL, the G915
TKL and the G815 that has lighting in the profile. A profile with no
keyboard's lighting in it shows the default lighting: Static in Gearbench's
default color, `#0080ff`. The daemon drives one keyboard at a time, and the
mouse follows the one connected. With two plugged in, the daemon connects the
model the mouse follows, and a new choice hands the one connected back, as
the daemon's stop does, and connects the one chosen at once if it is switched
on, a G915 TKL once the daemon can type its F1 to F12, as it does when the one
chosen is plugged in beside another; with none connected, the mouse shows that
model's lighting and brightness.

Connecting a keyboard saves no copy. The first change you make on it saves
the copy, with your change, for that keyboard alone; the other models'
lighting stays as it was. A change made while no keyboard is connected goes
to the keyboard connected last since the daemon started, so the mouse shows
it then only where that keyboard is the model it follows. Before any has been
connected, a lighting or brightness change is refused, with a message to
connect a keyboard.

## Game profiles

A game profile is one JSON file in `~/.config/gearbench/profiles/`, loaded
on top of the active M profile. Until it is unloaded, it replaces the
lighting, the G-key bindings with their MR layer, and the mouse button
bindings. On a mouse in host mode, DPI stages or a report rate of its own
replace onboard profile 1's too (see "Stages and rate per game profile").
The M keys keep switching M profiles.

While a game profile is loaded, the window and the editing commands edit it
and save its file. The editing commands are `color`, `effect`, `bind`,
`mouse-bind` and `mouse-defaults`. So are `mouse-dpi-stages` and
`mouse-rate` for a mouse that has host mode, with host mode switched on,
whether or not it took (see "Stages and rate per game profile"). The
Keyboard and Mouse tabs say which layer they edit:
`Editing Doom, loaded for DOOMEternalx64vk.exe`, `Editing Doom` for one
loaded by hand, or `Editing M1 · Profile 1`.

The Keyboard tab's Game profile group, from the top:

- Loaded chooses the profile in force (`gearbench-ctl load`,
  `gearbench-ctl unload`). Its list opens with a search box that matches any
  part of a name, in any case: `raiders` finds Arc Raiders. While the list
  is open it does not change; a profile added or loaded meanwhile shows once
  it closes. It also lists a loaded game profile whose file could not be
  read or is gone, so it can be unloaded here. While the loaded profile's
  file will not load, the Keyboard tab keeps the lighting it showed, and Map
  G keys shows no bindings. The window then sends no binding, keyboard color
  or keyboard effect change, Save as… or Set as M default, and says why.
  Loaded and Reload from disk still work.
- Keep loaded shows while a game profile is loaded; see "Hold-offs and Keep
  loaded".
- While focus is followed and a game profile names a program, a row says
  which profile is active and why (see "Following the game in front").
- Auto-load lists the loaded profile's programs, with Edit….
- During a hold-off, a row says so, with Load it now (see "Hold-offs and
  Keep loaded").
- Save as…, on the Save row (`gearbench-ctl save-profile`), saves what shows
  as a game profile, and the window then loads it. Saving under another game
  profile's name asks before writing over it, and names the programs that
  load it. A profile saved from an animation runs the same animation at the
  same speed. One saved from `disabled` has a black Static color and
  background.
- Set as M default, on the Save row (`gearbench-ctl promote`), writes the
  loaded game profile's lighting and its G-key, MR and mouse bindings into
  the active M profile, then unloads the game profile. The lighting keeps
  its mode, its painted keys and both colors. The game profile's own DPI
  stages and rate stay with it, since an M profile has none. With no game
  profile loaded, it saves the active M profile as it shows.

Manage…, beside the group's heading, opens the Game profiles dialog.

A file that cannot be read costs only that profile: the window lists it in a
dimmed row, with the reason. Two files can hold one name, as when a file
manager makes a copy. Names match in any case, with punctuation and symbols
counted as spaces (C and C++ are one name). In a name with no letter or
digit, punctuation and symbols count as themselves (!! and ?? are two
names). The file named for the profile is read (`doom.json` for Doom), or
else the first by file name. The other is listed dimmed, naming the file
read instead. Give the profile in the other file a name of its own and it
loads as a separate profile, or remove that file. A profile loaded from a
copy while `doom.json` was missing moves to `doom.json` within 5 seconds of
it coming back. An edit in those seconds is refused.

### Following the game in front

A profile can name programs. It is active while a window of one of them has
focus, so with two games running, the one in front shows its profile. With
focus anywhere else, on the desktop or on a program no profile names, the
keyboard shows what it showed before: the M profile, or a profile loaded by
hand. Focus counts once it has stayed on a window for 0.3 s, or 1 s after
the first of a run of changes. So an Alt-Tab through several windows within
a second repaints the keyboard once.

Focus comes from Gearbench's own GNOME Shell extension, which sees every
window, native Wayland ones included. It reports the focused window's
process id only to the daemon `gearbench.service` runs. So a daemon you run
by hand in a terminal follows focus on Xwayland.

`install.sh` installs and enables the extension, and GNOME loads it at your
next login. If GNOME's Extensions switch is off, or its settings allow no
extensions from your home folder, `install.sh` says so. The extension does
not run until that changes, and turning the switch on starts your other
extensions too. If the settings lock which extensions run, `install.sh` says
so and cannot enable the extension. Once they no longer lock it, run
`install.sh` again or the `gnome-extensions enable` command below. With the
Debian package, log out and back in once: the window's banner then has Turn
on, which enables the extension, as this command does:

```
gnome-extensions enable gearbench-focus@gearbench.github.io
```

Gearbench 1.1.0 gave the extension a new name. `install.sh` removes the old
one and turns the new one on, as each run does, whether the old one was on
or off. The new one runs from your next login. With the package, turn it on
again with the window's Turn on after that login.

While the extension is off, the daemon reads focus from Xwayland, which X11
programs draw into, and Proton games too unless they run Wine's Wayland
driver (GE-Proton's `PROTON_ENABLE_WAYLAND=1`). A native Wayland window then
counts as no program.

`gnome-extensions info gearbench-focus@gearbench.github.io` says why the
extension is off. `Enabled: No` needs the command above. A `State:` of
`ERROR` means GNOME could not load it, and
`journalctl --user -b -t gnome-shell | grep -i gearbench` shows the error.
`OUT OF DATE` means your GNOME is not version 50, the only one the extension
is marked for. `doesn't exist` means GNOME has not found it. Either it was
installed after your login, so log out and back in, or `install.sh` put it
in your home folder and GNOME's settings allow no extensions from there.

With neither the extension nor Xwayland, profiles follow running programs: a
profile loads when its program starts and hands back when it exits.

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
file name can be. The dialog and `profile-programs` refuse more, and a
profile file past either does not load. The journal names the program each
time a profile loads or unloads for it.

### The Game profiles dialog

![The Game profiles dialog](images/game-profiles.png)

Game profiles… in the main menu, or Manage… beside the Game profile heading,
lists every game profile. The filter at the top shows the profiles whose
name or programs hold what you type, in any case. `doom` finds Doom, and
`r5apex` finds the profile that loads for `r5apex_dx12.exe`. Open one to see
the programs that load it, remove a program (the dialog asks first), type
one into Add by name, or pick one from Add a running program.

Adding or removing a program loads and unloads nothing by itself. Within 5 s
the daemon checks again: it loads the profile if one of its programs has
focus, or is running where focus is not followed. It unloads a profile a
program loaded if none of its programs does. The dialog names another
program on the list that still matches the one you remove, since the profile
still loads for that program. Such a pair is `DOOM` beside `doom.exe`, or a
name Linux cut to its first 15 bytes beside the full one.
`gearbench-ctl profile-programs NAME [PROGRAM...]` sets the list, and
`gearbench-ctl apps` lists what you are running.

A game started from Steam, Lutris, Heroic, Bottles or itch is listed under
its own name while it runs, with `Running in Steam` (or the launcher's name)
under it. A Proton or Wine game is listed by its `.exe`. Adding a launcher
itself, picked or typed, asks first, since the profile would then load for
the launcher and not the game. `profile-programs` and
`save-profile --process` warn and set it anyway.

The Name row renames a profile, as `gearbench-ctl rename-profile OLD NEW`
does. Everything that named the profile follows, and nothing is loaded or
unloaded. A name another game profile has is refused. Names match in any
case, with punctuation and symbols counted as spaces (`Doom: Eternal` is
`Doom Eternal`; a name with no letter or digit keeps its symbols). Renaming
the loaded profile after its file was edited by hand is refused too, until
Reload from disk takes the edit or `gearbench-ctl save` writes over it (see
"Settings files"). A profile that is not loaded is read from its file at the
rename, so a hand edit goes with it to the new file.

A profile's name and its programs cannot hold a control character (Unicode
category Cc, such as ESC or BEL) or a lone surrogate. Emoji and combining
marks are fine. A file with one does not load, and neither does a file whose
profile's name runs past 1,000 characters, a name a rename refuses too. The
white space at the two ends of a profile's name, a tab or a newline
included, is cut off before these checks. At a program's two ends only
spaces are cut off. `profile-programs`, `save-profile --process` and a file
refuse a tab or a newline there, and Add by name cuts it off first.

Delete profile removes the file, as `gearbench-ctl delete-profile NAME`
does. Deleting the loaded profile unloads it, and the confirmation says so.

### The profiles and macros pack

Gearbench comes with a pack of game lighting profiles and macros, and
imports nothing from it until you ask. It holds per-key lighting for 67
games, among them Baldur's Gate 3, Counter-Strike 2, Cyberpunk 2077, Elden
Ring, Helldivers 2 and Stardew Valley, and 10 macros.
`gearbench-ctl import-pack --list` shows what yours holds.

The pack's profiles light the keys each game binds by default, in one
scheme, on a dark gray background:

- movement red
- abilities and actions purple
- weapon and item slots orange
- menus, map and inventory blue
- chat and pings cyan
- modifiers green
- function keys and G1 to G5 yellow
- anything else pink

A few keys are off the scheme, such as No Man's Sky's white L, Y and Z, and
the green C and X of ARK: Survival Evolved and C of Valheim. A modifier with
a job of its own takes that job's color, except movement: a Shift, Ctrl or
Alt that sprints or crouches stays green. Rebind a key in the game and its
color stays where the default was; paint it on the Keyboard tab after
loading the profile.

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
stages or report rate.

Where you have a game profile or macro of the same name, compared as a
rename compares names, the review page asks about each one. Keep mine, the
default, leaves yours as it is. Use the pack's writes the pack's name, as
the pack spells it, and its lighting over your game profile, and the pack's
programs while Load for is ticked. It leaves your bindings, MR layer, mouse
buttons, DPI stages and report rate. For a macro, it writes the pack's macro
over yours.

The pack's lighting is drawn for the G915. Use the pack's writes the G915's
and leaves your profile's lighting for another keyboard as it is. A keyboard
with none of its own copies the G915's (see "One keyboard's lighting,
another's"). A game profile of yours with the same lighting for the G915, or
a macro with the same steps and repeat mode, is already there, and nothing
is asked or written for it. The daemon writes the files, so it must be
running.

A pack profile loads for the programs the pack names for its game, such as
`r5apex_dx12.exe` and `r5apex.exe` for Apex Legends. Untick Load for to load
it by hand. A pack profile that replaces yours then keeps your programs. A
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

A game's own rules decide whether a macro is allowed there. So above the
macros, the window and `gearbench-ctl import-pack --list` say "Use at your
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
taken it keeps yours and imports the rest, and says so when the daemon wrote
everything it sent. `--replace NAME` takes the pack's for that name, and can
be given more than once. With names given, NAME must be one of them.

### Hold-offs and Keep loaded

Choosing by hand holds off the profile showing for a program that has focus,
or runs where focus is not followed, however that profile was loaded.
Choosing by hand is a choice in the Loaded row or the tray menu's Game
profile, with Save as… or Set as M default, or with `load`, `unload` or
`promote`. The hold-off lasts until focus moves away from the profile's
programs or, where focus is not followed, until none of them runs. So the
next check does not put the profile straight back. The hold-off follows
focus, or the running program, to another of the profile's programs.

Loading the profile already loaded holds nothing off. A profile a program
loaded stays that program's: Save as… onto it keeps it loaded for the
program, and it goes when a hold-off would end. Where focus is not followed,
several profiles can be held off at once, each until none of its own
programs runs.

The Game profile group names the latest hold-off, with Load it now
(`gearbench-ctl let-load`), which ends every hold-off and checks again at
once. The profile of the program with focus then loads again or, where focus
is not followed, the profile of a running program. With Keep loaded on, a
profile you loaded by hand stays instead. `gearbench-ctl status` names the
latest hold-off on its `game profile` line, and the journal notes each one.

Another profile that names a held-off profile's own program does not load in
its place. Where focus is not followed, another game's program that starts
during a hold-off still loads its own profile, and the hold-off stays. A
hand edit that leaves a profile's file unreadable ends no hold-off. Nor does
it unload a profile its program loaded while that program runs or has focus:
the profile keeps what it held. `gearbench-ctl status` and the window's
banner give the file's error until a reload reads it or `gearbench-ctl save`
writes over it.

Keep loaded (`gearbench-ctl keep-loaded on|off`) keeps a profile you loaded
by hand when a program that would load another profile gets focus, or starts
where focus is not followed. The journal notes each time it does. G HUB
calls this a persistent profile. Keep loaded is off whenever the daemon
starts. It keeps only a game profile: over the M profile, a program's
profile loads as it does with Keep loaded off, even when the M profile was
chosen by hand.

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
reload (see "Settings files"), the daemon refuses to save over it, rename it or
delete it; a file deleted by hand holds no edit to keep, so saving the macro
writes it again. Saving a new macro under the name of a file added by hand
since the Macros tab read its list is refused; the tab reads the list again
and asks whether to replace that macro, as it does for a name already
listed. Once a reload, from the window or `gearbench-ctl reload`, has read
an edit, the editor shows the open macro as its file holds it. If the editor
has unsaved changes, it keeps them, says the file changed, and Save writes
them over it.

The Macros tab has the list of macros on the left, with New and Delete, and
one macro's editor on the right: its Name and Repeat rows and its Steps.
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
a `key_down` in a repeating macro presses once. A `key_down` of a key that
a G key or a mouse button holds keeps that key down after the G key or
button comes up, until the macro lets go of it or stops. A `key` or `text`
step leaves alone a key that is already held, by the macro's own
`key_down`, a G key or a mouse button. A `key_up` or `click_up`, or the end
of the macro, releases only what the macro itself holds.

Nothing stays held when a macro stops. The daemon releases what the macro
pressed, in reverse order, when:

- the macro ends, a While held one at the end of the pass in progress when
  its key comes up, or it is toggled off;
- another macro takes over;
- the keyboard or mouse that started it is unplugged or moves between its
  receiver and its cable, or the daemon stops;
- the mouse that started it is switched off or goes out of range, or the
  keyboard that started a While held macro does;
- the Host mode switch is turned on or off, for a macro started by a button
  of a mouse the switch concerns (see "Host mode");
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
`back` and `forward` included, and the wheel tilt of a mouse in host mode
scrolls nothing: they would reach the other session. A G915 TKL's F1
to F12 type their own keys there, from the keyboard's own profile. Switching
away stops a running macro and lets go of held keys. The journal notes when the
session goes inactive and when it comes back. It names the keys pressed in
between only with `--verbose`, since they belong to whoever has the screen.
To follow a lock, a blank, a suspend or a switch to another session, the
daemon needs `python3-gi`; without it, none of the four stops or refuses
anything.

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
SECONDS` sets the wait, from 0 (none) to 60. The daemon counts down, so
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
  modifier pressed alone is recorded like any other key, keeping its side:
  tapped, the left Shift is `key shift` and the right `key rshift`; held, it
  is `key_down`, the time, `key_up`. A Super tapped alone goes to GNOME's
  Activities overview and is not recorded.
- A modifier held over a click is recorded where it went down and where it
  came up, and the macro plays it that way. Held over keys typed one after
  another, it goes into each key's step instead: Shift held over A and then
  B records `key shift+a`, a delay and `key shift+b`. When a modifier has
  been recorded on its own and is still held, a key pressed under it leaves
  that modifier out of its step: Shift held, a click, then A held under it
  records `key_down shift`, the click, `key_down a`, `key_up a` and, where
  Shift came up, `key_up shift`, with the delays between them. When keys
  overlap under a modifier, as in fast typing, a key's `key_up` can leave
  the modifier out, or the modifier can get a `key_down` or `key_up` step of
  its own, as it does when a click follows a capital with Shift still down.
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

The Mouse tab covers every mouse attached. From the top:

- with two mice or more, a switcher with a button for each
- the mouse's group, with Status, Battery and Host mode
- Tuning, with DPI, DPI notices and Report rate
- DPI stages
- Lighting
- Buttons
- Onboard profile 1

With more than one mouse attached, the switcher, as the Headset tab's
between two headsets, or `gearbench-ctl mouse-select`, chooses which one the
tab shows and the mouse commands act on.
`gearbench-ctl mouse` lists them on its `mice:` line, with the selected one
starred. Every attached mouse keeps working whichever one is shown, and a DPI
button acts on the mouse it is on. If the chosen mouse goes away, the tab
shows it again when it is back.

![The top of the Mouse tab with two mice attached: its switcher on the G502 X PLUS, beside the G502 HERO](images/mouse-switcher.png)

Pair nothing else to a mouse's receiver. Going by the kernel's
`hid-logitech-dj.c`, a keyboard paired there could have its keystrokes
arrive on the mouse's nodes, which Gearbench's udev rules let you and your
programs open. These are the G502 X PLUS's hidraw and event nodes, and the
hidraw node of the G305, G502 X LIGHTSPEED, PRO X SUPERLIGHT or G502 X PLUS
MILLENNIUM FALCON. This has not been checked on hardware. In host mode no
onboard profile sends a key, so a key on the G502 X PLUS's event node comes
from a keyboard. Gearbench then warns in the journal once each time the
mouse attaches. `gearbench-ctl status`, the tray (at the top of its menu and
in its tooltip) and the Mouse tab's banner show the warning until the mouse
detaches.

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
mode did not take, the factory profile makes G4 and G5 back and forward. G6
to G9 then do their factory jobs on the mouse and send the daemon no button.
Run `gearbench-ctl mouse-enable-buttons` once, or Enable… under the Mouse
tab's Onboard profile 1, and G4 to G9 send mouse buttons 11 to 16 at once,
which the daemon reads. After G HUB on Windows the mouse can be on onboard
profile 2, where the buttons are the factory ones again; see "The onboard
profile and its backups". If `gearbench-ctl mouse` says the buttons are
partly enabled, run `gearbench-ctl mouse-enable-buttons` again.

Onboard profile 1 decides what G4 to G9 do whenever the mouse runs it in
onboard mode: at the login screen, with the daemon stopped, on another
computer without G HUB, and while the daemon runs without host mode. Two
writes set it, and each replaces the other:

- The fallback, `gearbench-ctl mouse-fallback-buttons` or Write fallback… on
  the Mouse tab, is for a mouse in host mode. It gives G4 to G8 their
  factory jobs back, which are also their default bindings. It makes G9 do
  nothing: from the factory G9 cycles the onboard profiles, and on profile 2
  nothing Gearbench writes is in effect. In host mode, all six buttons still
  reach the daemon whatever profile 1 holds, so their bindings still apply.
  Without the daemon, G4 and G5 go back and forward, and G6 to G8 shift and
  step the DPI.
- The enable write, `gearbench-ctl mouse-enable-buttons` or Enable… outside
  host mode, is for a mouse that runs in onboard mode while the daemon runs,
  with `mouse-host off` or where host mode did not take. The bindings of G4
  to G9 apply while the daemon runs, and without it the six do nothing. On
  a mouse whose buttons Gearbench reads in host mode alone, the enable write
  is refused and not offered, since its buttons would then do nothing
  outside host mode.

A mouse with the fallback that runs in onboard mode does what the fallback
says, and its bindings do not apply. On the G502 X PLUS it has not been
checked whether Gearbench then follows G7 and G8 stepping the stages, as it
follows the G305's DPI button (see "DPI and stages").

Other mice have buttons and names of their own. The Buttons group and
`gearbench-ctl mouse` list the buttons of the mouse shown, under its names.
On the G502 HERO, G6 is the sniper button, and the PRO X SUPERLIGHT has G4
and G5 alone. Each mouse model keeps its own bindings: a key bound to the
G502 X PLUS's G4 stays on the G502 X PLUS, and another mouse's G4 keeps its
own. One name can be a button with another job on another mouse, as the PRO
WIRELESS's G6 is its DPI button. A mouse with no bindings of its own starts
from its own defaults: each button's factory job, or nothing where that job
is one Gearbench does not have, such as switching onboard profiles. Where
Gearbench does not drive a mouse's buttons, each row is grayed out and says
what profile 1 has that button do, and the group says why.
`gearbench-ctl mouse` lists the same under `in profile 1:`.

Each button has a dropdown on the Mouse tab: Nothing, Back, Forward, DPI
shift, DPI up, DPI down, DPI cycle, then Keystroke…, which opens the Press a
key dialog (see "G keys and the MR layer"), and Macro…, which opens that
dialog on its macro list. A row bound to a key or a macro has Change… to
pick another.

From the command line: `gearbench-ctl mouse-bind G9 ctrl+f15`. It binds a
button of the mouse every mouse command acts on: the selected one (see
`gearbench-ctl mouse-select`), or while that one is away the one connected
first. With no mouse connected, that mouse is the one selected last, or else
the first connected since the daemon started. Before any mouse has been
connected, the command is refused, since the daemon cannot tell which model
the binding is for. A button the mouse lacks is refused with its buttons
named. The bindings belong to the M profile or the loaded game profile,
beside the G keys, and the Editing row at the top of the Buttons group says
which.

- `back` and `forward` go out as the mouse's back and forward from a virtual
  mouse, pressed and released with the button. The actions match in any
  case, so `Back` is the mouse's back; `key_back` binds the Back key.
- `dpi-shift` drops to the shift stage while the button is held and puts the
  DPI back at the release. While the button is held, a DPI chosen with DPI
  up, DPI down, DPI cycle, the DPI row, the tray or
  `gearbench-ctl mouse-dpi` stays, and the release puts nothing back. The
  same goes when, in host mode, a game profile with stages of its own for
  the mouse loads or unloads under the held button. If the mouse's link
  drops or the daemon stops while the button is held, or the mouse does not
  answer the release, the DPI from before the shift is put back as soon as
  it can be. That is when the mouse links again or answers, at the next
  release, or at the stop.
- `dpi-up` and `dpi-down` step through the stage list from the nearest
  stage, with a desktop notice (see "Battery and DPI notices"). `dpi-cycle`
  steps up as `dpi-up` does, and from the last stage goes back to the first,
  as the PRO WIRELESS's DPI button does from the factory. Its notice is the
  same.

- A key binding or `macro:NAME` works as on a G key. The key binding is held
  as long as the button is, and the macro runs by its repeat mode (see
  "Repeat modes and limits"). A lock, a blank, a suspend or a switch to
  another session releases a held key, `back` or `forward`. While another
  session has the screen, none of them types.

  Restore defaults, after a confirmation, or `gearbench-ctl mouse-defaults`
  puts the shown or selected mouse's buttons back to its defaults. On the
  G502 X PLUS, those are the default bindings in the table above. The dialog
  names each one. Another mouse model's bindings stay as they are.

### DPI and stages

Type the DPI into the DPI row and confirm it with the apply button, or run
`gearbench-ctl mouse-dpi 1600`. The DPI snaps to the sensor's step. The G502
X PLUS takes 100 to 25600, and the G305 200 to 12000, both in steps of 50.
The DPI row refuses a value outside that range. `mouse-dpi` sets the nearest
end of it instead, and its `dpi:` line says what was set. The G305's DPI
button steps through its stages by itself and tells Gearbench the stage it
moved to. That stage's DPI then shows as the mouse's, is remembered, and
gets the notice a DPI button's step gets.

The daemon remembers the last DPI you chose for each mouse model, from the
window, the tray, `mouse-dpi` or a DPI button, whichever way the mouse is
connected. It puts that DPI back when the mouse is switched on or attaches.
Two mice of one model share it. Without this, a mouse powers on at the stage
it was last on, or in host mode at the default stage. The paddle's shift is
not remembered. Under a loaded game profile's own stages, the DPI you choose
is remembered for that profile instead (see "Stages and rate per game
profile").

The stage list is what the DPI buttons step through: one to five distinct
values, in onboard profile 1. It has a default stage and a shift stage, the
one the paddle drops to. In host mode a loaded game profile can have its
own.

On the Mouse tab each stage has a row, titled with its number and whether it
is the default or the shift stage: `Stage 3 · default`. Where Gearbench
knows the model's colors, the row has a dot in the color the mouse flashes
on that stage. A G502 HERO, G502 LIGHTSPEED, PRO WIRELESS or G903 LIGHTSPEED
HERO gets no dots. The dot's tooltip names the color, and a screen reader
reads the same words.

Type a stage into its row and confirm it with the apply button. The rows are
one list, sent whole. A stage outside the sensor's range is refused with the
range named, and nothing is written. A value inside it snaps to the step of 50.
A row's trash button removes its stage once you confirm; a stage's row
emptied and applied is refused. Add a stage shows an empty row for a new
one, sent when you apply it. Before you apply it, that row's trash button
takes it away without asking.

The Default stage and Shift stage rows pick which stage is which. The mouse
starts on the default stage when it switches to profile 1, and a new default
leaves it on the stage it is on. The rows and
`gearbench-ctl mouse-dpi-stages 800,1600,3200 --default 1600 --shift 800`
write the list at once. A G305's tab has no Shift stage row, since none of
its buttons can be bound to DPI shift. `--shift` still sets its shift stage.
If you change the value of the default or shift stage, it stays the default
or shift stage at its new value, and a toast names that value.

On the command line, `--default` and `--shift` take DPI values, snapped as
the stages are and refused outside the range. A value the list does not hold
is refused. One under 100, the lowest any supported sensor takes, is refused
as a stage's number before anything is sent.

After a stage edit, a remembered DPI becomes what the mouse reads then. Edit
the stage it is on, and it is at the new value at once and after the next
power-on. With the paddle held, the remembered DPI becomes the DPI from
before the paddle, at its place in the new list. Letting go of the paddle
puts the mouse there. With no DPI remembered, nothing is stored and the
mouse stays on its own stage.

Use factory stages…, after a confirmation, or
`gearbench-ctl mouse-dpi-stages --factory` writes the factory stages,
default and shift, in place of the list. They are read from the mouse's ROM
copy of profile 1. On the G502 X PLUS they are 800, 1200, 1600, 2400 and
3200, default 1600, shift 800.

### Host mode

Host mode is on by default. Turn it on or off with
`gearbench-ctl mouse-host on|off`, the Host mode switch on the Mouse tab, or
`mouse_host_mode` in `config.json`. The switch shows for a mouse it
concerns, and says whether host mode took and why not. For a mouse asleep or
switched off that has not taken host mode yet, the switch says host mode is
tried once the mouse answers. With the switch off, it also says when the
mouse has not yet gone back to onboard profile 1. Outside host mode it says
when the mouse runs a profile other than profile 1.

The switch concerns the G502 X PLUS, on its receiver or its cable. Moving
the mouse from one to the other while the daemon runs puts it in host mode
again on the new one. On the G305 the switch changes nothing. With host mode
off, or where it did not take, the daemon reads G4 to G9 from the mouse's
event node, which only the `gearbench` group may read (see "The udev
rules"). Until the daemon has that group, `gearbench-ctl status` and the
Mouse tab's banner say what is missing. So does the Programmable buttons
row, once the buttons are enabled.

The switch concerns the trial mice with buttons in host mode too, none of
them yet checked on the hardware: the G502 HERO, the G502 X, the G502 X
LIGHTSPEED, the G502 X PLUS MILLENNIUM FALCON, the G502 LIGHTSPEED, the PRO
X SUPERLIGHT, the PRO WIRELESS and the G903 LIGHTSPEED HERO. In host mode
each works as below, with these differences. Their event nodes are not read.
So with the switch off, their buttons do what the onboard profile they run
gives them, and the Buttons group shows what profile 1 gives them. On each
but the G502 HERO, `gearbench-ctl mouse-rate` and the Report rate row set
the rate live, with nothing written to the mouse's onboard memory. Gearbench
keeps the rate for the model in `config.json` and sends it again each time
it sets the mouse up in host mode: after a replug and at its next start. The
mouse's onboard profile keeps its own rate. When the daemon stops, each but
the G502 HERO goes back to the onboard profile it runs, since Gearbench does
not switch their profiles yet. The G502 HERO's rate and profile go as the
G502 X PLUS's do (see "The G502 HERO"). Leaving host mode, the G502 HERO's
firmware writes its running profile's two lighting records, unchanged, into
its own settings memory.

While the daemon runs with host mode on, a G502 X PLUS is in host mode. No
onboard profile runs, and G4 to G9 reach the daemon as the mouse's button
reports, with no write to its onboard memory. The DPI stages and the report
rate are onboard profile 1's, or a loaded game profile's own, sent live.
`gearbench-ctl mouse` starts its `mode:` line with `host`. The wheel tilts
scroll sideways as before, one notch at the tilt and one every 100 ms while
it is held. The daemon makes these, and a tilt cannot be bound. A DPI set to
one of the stages, by a DPI button, the paddle or `mouse-dpi`, flashes the
stage's color and leaves the mouse dark, as in onboard mode. The lighting
comes back 5 s later.

A mouse that refuses host mode runs profile 1 as with the switch off (or,
for a trial mouse or if the switch to profile 1 does not take, the profile
it was on). `gearbench-ctl status` and a banner on the Mouse tab say host
mode did not take, with the reason. If the mouse then also fails to switch
back to onboard mode, it is left in host mode with its programmable buttons
dead. `gearbench-ctl status` and the banner say so. Switching the mouse off
and on, or `gearbench-ctl mouse-host on`, puts it right. In both cases
Gearbench tries again when the mouse next attaches or is switched on.

When the daemon stops, the mouse goes back to onboard mode on profile 1, at
the DPI it had outside a game profile's own stages. G4 to G9 then do what
profile 1 says (see "The buttons, G4 to G9"). A mouse asleep at the stop is
asked a second time, once the first request has woken it.

If the mouse is switched off or out of range when the daemon stops, or the
daemon is killed, the mouse stays in host mode until its next power cycle or
the next start. Until then, clicks and motion work, and its programmable
buttons and any wheel tilts are dead. A mouse switched off or out of range
at `gearbench-ctl mouse-host off` stays in host mode until it is back. It
goes to onboard mode then, unless host mode is on again by then.

### Report rate

The Report rate row, or `gearbench-ctl mouse-rate`, sets 1000, 500, 250 or
125 Hz. The rate is stored in onboard profile 1 and in effect at once,
unless the mouse runs another onboard profile. In host mode it is also sent
live. Against a mouse left in host mode by something else, it falls back to
a live write, which takes no backup. The reply says which happened. With a
game profile loaded, in host mode, it is that profile's own rate instead. On
a mouse whose onboard profile Gearbench does not write yet, in host mode,
the rate is sent live alone and kept for the model, and sent again at each
setup in host mode (see "Host mode"). Outside host mode it is refused.

### Stages and rate per game profile

In host mode the daemon runs the DPI stages and the report rate, and a game
profile can have its own for each mouse model. While the profile is loaded,
the profile's own stages and rate are in force in place of profile 1's. They
are sent live, and nothing is written to the mouse's onboard memory.

- With a game profile loaded and host mode on,
  `gearbench-ctl mouse-dpi-stages` and `gearbench-ctl mouse-rate`, and the
  stage and rate rows on the Mouse tab, save to the game profile and leave
  profile 1 as it is. The reply says so. A profile with no stages of its own
  starts from profile 1's. Left out, `--default` and `--shift` stay on
  profile 1's default and shift DPIs, or on their places in the list when
  those DPIs are gone. With host mode off, and on the G305, the commands and
  rows go on as with no game profile loaded.
- The rows show the profile's own while it is loaded, and the Game profile
  row at the top of the DPI stages group says whose they are.
- `mouse-dpi-stages --reset` and `mouse-rate --reset` drop the profile's
  own, and profile 1's are used again. On a mouse whose onboard profile
  Gearbench does not write yet, the rate kept for the model is used, where
  one is kept. Use profile 1's, on the Game profile row, drops both, once
  you confirm; Cancel is the default.
- When a profile with its own stages loads, by hand or for its program, the
  DPI goes back to the one last chosen under it since the daemon started. So
  switching away from a game and back keeps the DPI you picked in it. At the
  profile's first load after a start, or once that DPI is not one of its
  stages, the DPI goes to its default stage. When the profile unloads, or
  one without its own stages takes its place, the DPI goes back to where it
  was before the profile loaded. None of these flashes the stage's color.
- The DPI buttons, the paddle and the tray menu use the profile's own
  stages. A DPI chosen under them, with a button, the tray or `mouse-dpi`,
  is put back after a power cycle while the profile stays loaded. The DPI
  remembered for the desktop is left as it was.
- An edit to the loaded profile's stages, or `gearbench-ctl reload` after a
  hand edit to its file, keeps the mouse on the stage it is on, at its new
  value. Where the new list ends before that stage, the mouse's DPI stays as
  it is. With the paddle held, letting go puts the mouse on the stage it was
  on before the paddle, at its new value, as on profile 1. If the mouse
  refused the profile's stages when it loaded and has not taken them since,
  the edit moves it as a load does. A stage a hand edit puts between the
  sensor's steps, or outside its range, is taken at the nearest DPI the
  mouse takes, as `mouse-dpi` does, unless two stages would land on the same
  one.
- A profile's own stages and rate apply on every mouse in host mode, the
  trial ones included, whose stage and rate rows stay on while they edit a
  game profile.
- A profile's own stages and rate do not apply on the G305, on another mouse
  with no host mode (its lookup of the button reports was refused, or found
  none), or with `mouse-host off`. Such a mouse runs an onboard profile. The
  G305 and the G502 X PLUS run profile 1, and the two commands and the rows
  write it; the reply says so. A trial mouse keeps the profile it runs, and
  the commands are refused with nothing written and the rows grayed out (see
  "Mouse models"). `--reset` is refused. For a profile with stages or a
  rate of its own, the `game stages:` line of `gearbench-ctl mouse` and the
  Game profile row say they are not in force.
- A mouse switched off when the profile loads gets the profile's stages and
  rate when it is back. The reply to a change made while it is off says the
  change takes effect once the mouse answers. A change the mouse refuses is
  still saved: the reply says it was refused, and the mouse gets it at its
  next setup or the profile's next load.

### Lighting and color sync

`gearbench-ctl mouse-color` and `mouse-effect`, and the picker and Effect
row in the Mouse tab's Lighting group, set the mouse's own color and effect,
live as you drag. The Effect row shows the effect last set, which the daemon
does not read from the mouse. It shows Static for a color of its own, or
when none has been set since the daemon started. The mouse's color is not
stored: `gearbench-ctl mouse` reports what was last written since the daemon
started.

`gearbench-ctl mouse-sync on`, or Sync with keyboard, makes every mouse with
lights follow the keyboard. A synced mouse shows the background in Per-key
mode, the Static color in Static mode and, steady, under the animations, and
black under `disabled`. Live previews go to the mouse too. A mouse with no
lighting Gearbench drives, such as the G305, is left as it is, and
`mouse-sync on` names it. The mouse has no brightness control, so the daemon
scales its color by the keyboard's brightness. At 50 %, the default, the
mouse shows half of each color, and at 100 % the color itself. The mouse
goes off with the screen lock. It dims with the keyboard's idle dimming once
nothing on the desktop, the mouse included, has been touched for the
keyboard's Dim after time (see "Idle, lock and suspend").

While sync is on, `mouse-color` and `mouse-effect` are refused and the tab's
picker and Effect row are grayed out. The Background row on the Keyboard tab
says when the background is black, which leaves a synced mouse dark.
`mouse-sync off` gives each mouse back its own color or effect. A mouse with
none keeps the synced color, at the keyboard's brightness, as its own. It
goes back to that color at every later switch off while the daemon runs.
With sync off, a mouse with no color or effect of its own would stay dark
after a DPI flash. So once the flash is over, it takes the keyboard's color
at the keyboard's brightness. A G502 HERO, G502 LIGHTSPEED, PRO WIRELESS or
G903 LIGHTSPEED HERO with none takes no color in either case and shows its
own lighting (see "The G502 HERO").

The setting is `mouse_sync_color` in `config.json`, one for all mice and all
profiles. A mouse that is off or asleep gets the color when it is back. A
failed write to the mouse never affects the keyboard. With two keyboards
plugged in, the switcher at the top of the Keyboard tab picks the one the
mouse follows; `gearbench-ctl sync-keyboard` picks it too, or the one
connected first (see "One keyboard's lighting, another's").

### The onboard profile and its backups

A mouse runs an onboard profile from its own memory: one of five on the G502
X PLUS, and the only one on the G305. Gearbench writes profile 1 only, and
only on these two. On a trial model each write to it is refused with nothing
written (see "Mouse models"). Profile 1 holds the report rate, the DPI
stages with the default and shift stage, and the button entries.

`mouse-enable-buttons`, `mouse-fallback-buttons`, `mouse-rate`,
`mouse-dpi-stages` and `mouse-restore` write profile 1. In the window,
Enable… or Write fallback…, the rate and stage rows, Use factory stages… and
Restore profile backup… write it. With a game profile loaded and
`mouse-host on`, the rate and stage commands and rows, and Use factory
stages…, write the game profile instead on a mouse with host mode, whether
or not host mode took.

The Onboard profile 1 group on the Mouse tab shows what profile 1 holds: its
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
- takes effect at once, with no power cycle, and the reply says so. Outside
  host mode, on a mouse that runs another onboard profile (below), the write
  is not in effect until the mouse runs profile 1, and the reply says that
  instead. In host mode, where no profile runs, the daemon takes the stages
  and rate from profile 1 at once, except any a loaded game profile has of
  its own. The mouse runs profile 1 when the daemon stops. The reply says
  what holds while the daemon runs and what changes once it stops. After a
  restore or a report rate change in host mode, the reply also says if the
  mouse did not take the report rate. After the other writes in host mode,
  it says if the mouse stopped answering. If the mouse did not answer, the
  daemon sends the rate once it does. Otherwise the rate waits for the
  mouse's next setup, as after it is switched off and on.

  The window locks those rows while a write is on its way, with a spinner
  beside the Onboard profile 1 heading. A toast says when the write is in
  effect. A write takes about a second when the mouse answers.

  `gearbench-ctl mouse-restore`, or Restore profile backup…, writes this
  model's newest backup of profile 1 back. It backs up the current state
  first, so two restores in a row toggle between the two. Over a profile 1
  with a bad CRC, the second writes the same backup again, as the window's
  dialog says before the first. The newest is the model's, whichever mouse
  of that model wrote it: with two G502 X PLUS, it can be the other one's. A
  restore that fails and leaves a profile 1 with a good CRC as it was
  renames the backup it just made, adding `.unchanged` to its name. So
  trying again writes the backup you meant; nothing is deleted. RECOVERY in
  `man gearbench-ctl` has what to do after a write or a restore that failed,
  how to restore an older backup, and what is left with no good backup.

The daemon keeps the mouse on profile 1. G HUB can leave the G502 X PLUS on
profile 2, where G4 and G5 are back and forward again and nothing written to
profile 1 shows. At attach and after each power-on the daemon switches it
back, which writes nothing to the mouse's memory, and puts the remembered
DPI back. The journal notes the switch, and warns with the reason if it does
not take. A profile 1 that fails its CRC is never switched to, since what
the mouse would run from it is not known. The journal and
`gearbench-ctl status` say so, and that the newest good backup written over
it with Restore profile backup… mends it. Outside host mode, where that
profile then runs, `gearbench-ctl status` says the mouse runs another
profile and the Mouse tab shows a banner. The Profile backup row and the
Enable… and Restore profile backup… dialogs say before a write that the
write is not in effect while that profile runs. `gearbench-ctl mouse` names
the profile on its `running:` line. In host mode no profile runs, and the
daemon tries the switch again when host mode ends.

The enable write and the fallback live in the mouse's own memory, and no
Gearbench setting records them. The mouse keeps either on another computer
and through a reinstall of Gearbench. A different G502 X PLUS is on its
factory profile until it gets its own write. `mouse-rate` and
`mouse-dpi-stages` change their own bytes and carry the button entries
across.

`gearbench-ctl mouse` reports the button state on its `buttons:` line:
enabled, fallback, partly enabled or not enabled, and the write to run. The
Mouse tab reports it on the Programmable buttons row under Onboard profile 1.
Both show profile 1 as the daemon last read it, at attach or at the last
write. Once the buttons are enabled, the row's button reads Re-apply…, which
writes the same entries again.

In host mode, `gearbench-ctl mouse` starts the line by saying G4 to G9 come
in the mouse's button reports. The row says first that G4 to G9 reach
Gearbench directly and that the mouse runs profile 1 without Gearbench. The
row then has Write fallback… in place of Enable…, in the accent color until
profile 1 holds the fallback. Its dialog says what the write does and that
every profile sector is backed up first.

On a mouse whose onboard profile Gearbench does not write yet, such as the
G502 X LIGHTSPEED, the `buttons:` line names no write. In host mode the line gives
each of the mouse's buttons and its job in profile 1, and says why. Outside
host mode it says the bindings work in host mode only, and the
`in profile 1:` lines under it give each button's job. The Programmable
buttons row gives the same buttons and jobs and offers neither write: its
buttons are grayed out, neither in the accent color. In host mode the row
says first that the buttons reach Gearbench directly. It says nothing of
what the mouse runs without Gearbench, since once Gearbench stops the mouse
runs the profile it ran before. While profile 1 fails its CRC, the row gives
no jobs and says the buttons are not known.

A mouse whose switch back to onboard mode was refused stays in host mode
with its programmable buttons doing nothing until it is switched off and on,
or `gearbench-ctl mouse-host on` arms it again. `gearbench-ctl mouse` then
says `host, stuck there` on its `mode:` line, and its `buttons:` line and
the Mouse tab's Programmable buttons row read as they do outside host mode.
A mouse asleep or switched off at the time is not stuck: its `mode:` line
says `host until the mouse answers`, and once it does, Gearbench makes the
switch. With host mode on, it tries host mode again first and makes the
switch only if that fails too.

### Asleep or switched off

A mouse switched off behind its receiver stays attached.
`gearbench-ctl mouse` says `not answering`, and the Mouse tab's Status row
has an amber warning sign, with the rows that write to the mouse grayed out.
Until the mouse moves, clicks or reports, the daemon sends it nothing on its
own but a probe: 30 s after it went quiet, then at doubling intervals up to
two minutes. It sends none while the mouse's receiver says the link is down.
A command that writes to it, such as `gearbench-ctl mouse-dpi`, still tries
it. `gearbench-ctl mouse` and the Mouse tab send it nothing: they show what
was last read from it. `gearbench-ctl mouse`, and the window's 5 s read on
any tab while the window is on screen, bring its next probe within 30 s
unless its receiver says the link is down. The G502 X PLUS's receiver says
when the mouse is switched on, and the daemon sets the mouse up again at
once, lighting and DPI included.

Each setup that does not come from turning host mode on or off also puts
back the wheel's high resolution, as an earlier setup found it, in case
another program turned it off. In low resolution the G502 X PLUS's wheel
scrolls about an eighth as far per notch. The journal says when a setup puts
high resolution back. If the wheel stays slow, switch the mouse off and on
behind its receiver. The kernel turns high resolution on as the mouse
connects, and from then on the daemon puts it back whenever such a setup
finds it off.

A mouse off when the daemon starts is set up as soon as its receiver says it
is switched on, or it moves, clicks or reports. Failing that, it is looked
for again after 10 s, then less often, up to every two minutes. With no
other mouse attached, `gearbench-ctl mouse`, or the window's 5 s read on any
tab while the window is on screen, brings the next try within 30 s.

## Mouse models

"The mouse" covers the G502 X PLUS and the G305; each other mouse's section
is here. Each of these eight is a trial. The G502 HERO has been tried on
one, as its section says; none of the seven others has run on one yet. What
the eight share comes first; each section then says what its model was built
from, does and lacks. Gearbench writes the G502 HERO's onboard profile, as
its section says, so the next three paragraphs hold for the seven others
alone.

On each of the seven, the report rate, the stages and onboard profile 1 show
as the mouse holds them. Writing any of them is refused with nothing written.
The stage rows and Restore profile backup… are grayed out and say beneath
them that Gearbench does not write that model's onboard profile until that
is tested on this hardware. Outside host mode, so is the rate row.
`mouse-rate`, `mouse-dpi-stages`, `mouse-enable-buttons`,
`mouse-fallback-buttons` and `mouse-restore` answer the same. Each then says
what stays as it is for that write: the rate or the stages as the profile
holds them, the buttons doing what it holds outside host mode and without
Gearbench, or the profile itself.

In host mode, the default, the Report rate row and `mouse-rate` set the rate
live instead, and a loaded game profile's own stages and rate apply (see
"Host mode"). With host mode off, a mouse's buttons do what the onboard
profile it runs gives them.

Gearbench never switches one of the seven to profile 1, since whether that
switch stays in the mouse's memory is not known yet. The mouse keeps the profile it
runs, and `gearbench-ctl mouse` says so when that is another.

On the G502 HERO, the G502 X LIGHTSPEED, the PRO X SUPERLIGHT, the G502 X,
the G502 X PLUS MILLENNIUM FALCON and the G502 LIGHTSPEED, the DPI row and
`gearbench-ctl mouse-dpi` set the DPI live, from 100 to 25600 in steps of 50.
The daemon puts back the DPI you chose when a wired one is plugged in again,
and when a wireless one is switched on again or moves between its receiver
and its cable.

On the G502 HERO and the G502 X, the udev rule grants the interface that
carries the mouse's control protocol. That interface also carries the
keystrokes the mouse sends for a button its onboard profile binds to a key.
A program you run can read those keystrokes there.

### The G502 HERO

The G502 HERO was built from published logs of the mouse, and tried on a
G502 SE HERO on 2026-10-17 and, its lighting included, on 2026-10-18. It
attaches on its cable alone. Its tab has the mouse's group, the DPI and
Report rate rows, the DPI stages, Lighting, Buttons and Onboard profile 1.

Its Lighting group, `gearbench-ctl mouse-color`, `mouse-effect` and
`mouse-sync` drive its two zones, the logo and the DPI stage lights, with
the effects the mouse reports: static, breathe, cycle and disabled. The DPI
stage lights, which `gearbench-ctl mouse` calls `primary`, show the zone's
color on the light of the stage in use alone. Until the mouse is given a
color or an effect, it shows the lighting its onboard profile holds. Until
then, Gearbench sends it nothing for its lighting, after a replug or a
resume too. The one exception is a release as it connects, in case a daemon
that crashed still held its lighting. The first color or effect set for it,
or color sync turned on, hands its lighting to Gearbench. When no color or
effect has been set for it since the daemon started, color sync turned off
gives it its own lighting back. The Lighting group says under its title
which of the two the mouse shows, and `gearbench-ctl status` says it under
its `mouse:` line.

Its onboard profile 1 is written as the G502 X PLUS's is: the stages, the
default and shift stage and Use factory stages…, the report rate, Write
fallback… and Restore profile backup…, each after the profile's sectors are
backed up. The stage write was tested on the hardware on 2026-10-21, a
stage changed and put back, with a replug between; the others have not run
on it yet.
Enable… is not offered on it, and `gearbench-ctl mouse-enable-buttons` is
refused with nothing written: Gearbench reads its buttons in host mode
alone, so enabled they would do nothing outside it. Found on another
profile, it is switched back to profile 1, as the G502 X PLUS is. Its
factory stages are 1200, 2400, 3200 and 6400, default
2400, shift 1200, on the G502 SE HERO Gearbench was tried on.

Each color and effect is sent for the mouse's working memory alone, which a
replug clears. Unplugged and plugged in again while Gearbench was stopped, a
G502 HERO showed its onboard profile's lighting again (tested on
2026-10-18).

In host mode, G4 to G9 reach Gearbench through the mouse's button reports
and take their bindings, as on the G502 X PLUS. The G502 HERO's buttons are
G4 and G5 on the side, G6 the sniper button in front of them, G7 and G8 the
DPI buttons beside the left button, and G9 behind the wheel. The wheel tilts
scroll sideways, the daemon making each notch. None of this has run on a
G502 HERO yet. With host mode off, the Buttons group shows what profile 1
gives the buttons, its rows grayed out. When the mouse leaves host mode, at
the switch or when the daemon stops, its firmware writes its running
profile's two lighting records, unchanged, into its own settings memory.

If another program that drives these devices never wrote to the mouse, its
profile 1 may be blank, which fails its CRC. Gearbench then writes nothing
over it and does not switch the mouse to it. With no backup of it to
restore, RECOVERY in gearbench-ctl(1) writes the factory copy back.

### The G502 X LIGHTSPEED

The G502 X LIGHTSPEED is the G502 X PLUS without lighting. It was built from
G HUB's description of the model and a published listing of its features. It
attaches through its LIGHTSPEED receiver and on its cable. Its tab has the
mouse's group with the battery, the DPI and Report rate rows, the DPI
stages, Buttons and Onboard profile 1, with no Lighting group.

In host mode, the default, its G4 to G9 and wheel tilts work as the G502 X
PLUS's, whose button layout it is taken to have. As on the G502 X PLUS, a
setup puts back the wheel's high resolution (see "Asleep or switched off").

### The PRO X SUPERLIGHT

The PRO X SUPERLIGHT was built from its firmware, a published listing of its
features and G HUB's description of it. It attaches through its LIGHTSPEED
receiver and on its cable. It has five buttons and no lighting you can set.
So its tab has the mouse's group with the battery, the DPI and Report rate
rows, the DPI stages, Buttons and Onboard profile 1, with no Lighting group.
`mouse-color` and `mouse-effect` say it has no lighting, and color sync
leaves it as it is.

In host mode, the default, its side buttons, G4 and G5, reach Gearbench and
take their bindings, back and forward unless you bind them to something
else. The left, right and middle buttons work as any mouse's.

When the DPI is one of its stages, the mouse's DPI indicator light takes the
stage's color: yellow, white, orange, pink and purple, stage 1 first.
Outside host mode the rate cannot be set, since while it runs its onboard
profile the mouse refuses a rate sent to it and takes one from that profile
alone.

### The G502 X

The G502 X is the wired model of the G502 X family, with no lights and no
battery. It was built from a published log of one unit and published
listings of its features. It attaches on its cable. Its tab has the mouse's
group, the DPI and Report rate rows, the DPI stages, Buttons and Onboard
profile 1, with no Battery row and no Lighting group. In host mode, the
default, its G4 to G9 and wheel tilts work as the G502 X PLUS's.

### The G502 X PLUS MILLENNIUM FALCON

The G502 X PLUS MILLENNIUM FALCON is an edition of the G502 X PLUS with ids
of its own. No published source lists its features, so Gearbench takes all
of them from the G502 X PLUS, as G HUB's description of it does. It attaches
through its LIGHTSPEED receiver and on its cable. Its tab has the mouse's
group with the battery, the DPI and Report rate rows, the DPI stages,
Lighting, Buttons and Onboard profile 1. In host mode, the default, its G4
to G9 and wheel tilts work as the G502 X PLUS's.

Its Lighting group, `gearbench-ctl mouse-color`, `mouse-effect` and
`mouse-sync` drive its lights as the G502 X PLUS's, each write for the
mouse's working memory alone. So, switched off and on while Gearbench is
stopped, the mouse should show the lighting its onboard profile holds again.
That has not been checked on one yet, and `gearbench-ctl status` says its
lighting is under test until it has. Its two signature effects, which on the
G502 X PLUS are snipe-pulse and neural-wave, are its own animations.
Gearbench names them as G HUB does, Millennium Falcon: `millennium-falcon`
for the one G HUB offers and `millennium-falcon-inactive` for the one it
keeps for power saving. The G502 X PLUS's names are refused on it.

### The G502 LIGHTSPEED

The G502 LIGHTSPEED, the G502 HERO's wireless model, was built from G HUB's
description of it and published logs of the mouse. It attaches through its
LIGHTSPEED receiver, or a POWERPLAY mat's, and on its cable. Its tab has the
mouse's group with the battery, the DPI and Report rate rows, the DPI
stages, Lighting, Buttons and Onboard profile 1.

Its two zones, the primary zone and the logo, take colors and effects as the
G502 HERO's do: static, breathe, cycle and disabled, each for the mouse's
working memory alone. The mouse shows its own lighting until it is given
one. Going by the firmware of the PRO WIRELESS, whose lighting answers the
same, a color sent while Gearbench does not hold the lighting is stored in
the mouse. The mouse lets go of that hold when its power state changes, as
when it goes to sleep. So Gearbench takes hold of the lighting again right
before each color or effect it sends. `gearbench-ctl status` says its
lighting is under test.

The mouse reports only its voltage, and the Battery row's percentage comes
from G HUB's curves for its cell. While it charges, the percentage climbs to
66 % at 4.19 V, just under the 4.20 V the charger holds until the cell is
full. From there it reads "charging, nearly full" with no number.

In host mode, the default, its G4 to G9 and wheel tilts work as the G502
HERO's, G6 the sniper button. Without Gearbench, its G9 shows the battery
level. Outside host mode the rate cannot be set, since while it runs its
onboard profile the mouse refuses a rate sent to it. Through its receiver,
as on the G502 X PLUS, a setup puts back the wheel's high resolution (see
"Asleep or switched off"). Its cable has no high-resolution wheel to keep.

### The PRO WIRELESS

The PRO WIRELESS, an ambidextrous mouse, was built from its firmware, a
published log of the mouse and G HUB's description of it. It attaches
through its LIGHTSPEED receiver and on its cable. Gearbench treats it as the
G502 LIGHTSPEED (see "The G502 LIGHTSPEED"): the same tab, the same live DPI
and refusals, and the same battery curves, which G HUB gives both mice. It
has the same two zones, taken hold of right before each color or effect,
here because its own firmware stores a color sent otherwise. It has no
high-resolution wheel.

Its buttons are G4 and G5 on the left side, G6 its DPI button, and G7 and G8
on the right side. In host mode, the default, they reach Gearbench as on the
G502 LIGHTSPEED, each at its own default: back and forward on each side, and
DPI cycle on the DPI button. DPI cycle steps up through the stages and from
the last back to the first, as from the factory. Its firmware keeps host
mode in its working memory and reloads the profile it runs on leaving it.
With host mode off, the Buttons group shows what profile 1 gives the
buttons, grayed out.

### The G903 LIGHTSPEED HERO

The G903 LIGHTSPEED HERO was built from its firmware, a published log of the
mouse and G HUB's description of it. It is not the older G903, which
Gearbench does not match. It attaches through its LIGHTSPEED receiver, or a
POWERPLAY mat's, and on its cable. Its tab has the mouse's group with the
battery, the DPI and Report rate rows, the DPI stages, Lighting, Buttons and
Onboard profile 1.

Its Lighting group, `gearbench-ctl mouse-color`, `mouse-effect` and
`mouse-sync` drive its two zones, the primary zone and the logo, with the
effects the mouse reports: static, breathe, cycle and disabled, each for the
mouse's working memory alone. Holding its lighting also hands the mouse's
power saving to Gearbench, which puts no mouse to sleep. So, as on the G502
HERO, Gearbench takes the lighting only once the mouse is given a color or
an effect, or color sync is turned on. Until then the mouse is sent nothing
for its lighting but one release when it connects, and it dims and sleeps on
its own. The Lighting group says under its title which lighting it shows. A
color is each zone's static effect: the mouse has no per-key lighting.
Whether the mouse still dims and goes to sleep on its own while Gearbench
holds its lighting has not been checked. `gearbench-ctl status` says its
lighting is under test.

Its buttons are G4 and G5 on the left side, G6 and G7 on the right side, and
G8 and G9, its DPI down and up buttons. Which DPI button has which printed
label has not been read off a mouse. In host mode, the default, they reach
Gearbench as on the G502 LIGHTSPEED, each at its own default: back and
forward on each side, and the DPI steps. In host mode its wheel tilts
scroll. Its firmware keeps host mode in its working memory. Its profile
button does nothing in host mode, where no onboard profile runs, and has no
binding. With host mode off, the Buttons group shows what profile 1 gives
the buttons, grayed out.

As on the G502 LIGHTSPEED (see "The G502 LIGHTSPEED"), its DPI is set live
and put back, and its report rate is set live in host mode alone. Its
battery reads as the G502 LIGHTSPEED's does, from G HUB's curves for its own
cell. It reads "charging, nearly full" from 4.18 V, at 86 %, just under the
4.198 V where its charging curve ends at 88 %. Through its receiver, as on
the G502 X PLUS, a setup puts back the wheel's high resolution. On its
cable, the kernel leaves the wheel in low resolution, which Gearbench keeps.

## The headset

![The Headset tab](images/headset.png)

The Headset tab and the `gearbench-ctl headset` commands cover the Astro A50
Gen 4 through its base station, on USB with its switch on PC (`9886:002c`).
Gearbench sends the base station a fixed list of commands and refuses any
other before it is sent, a reset, the bootloader and the headset's firmware
updater among them. It never updates the base station's or the headset's
firmware. With its switch on Console the base station has no control
interface, and Gearbench does not look for the Xbox edition (`002d`). Only
Gearbench may talk to the base station: any other program on its node sees
Gearbench's replies and mixes its own in. The Logitech G432 has a page of
its own in the tab, and commands of its own (see "The Logitech G432").

A change goes to the base station's memory and is in effect at once; Save
writes it to flash (see "Saving"). Gearbench checks every value before it
sends it, since the base station clamps nothing. The tab shows what the base
station reads back, and leaves out a row whose setting the base station
would not read.

The tab has the daemon read every setting and preset again, as
`gearbench-ctl headset --read` does. It asks the first time it shows with
the base station answering, and again after the base station or the daemon
was lost. The daemon also reads them all again when the headset links to its
base station, after a save, and after a resume, since the base station may
have lost what its memory held. While a save runs, the read waits for it to
end.

The tab, from the top: the Astro A50 group with its status, Battery and
Balance; Microphone; Equalizer; Stream port; Base station; Revert to saved;
and Spatial sound. The A50's two outputs in GNOME's Sound settings come from
`install.sh` and the package, not from the tab (see "Game and Chat").

### Status and battery

The status group is titled with the model, Astro A50. Its first row says
Headset on, with a green check mark, while the headset is on and linked to
its base station. It says Headset off or out of range, amber, while the
headset has no link to its base station, and Not answering, amber, while the
base station does not answer. The row's subtitle gives the firmware versions
of the base station and, once it has been on, the headset; its tooltip gives
the node.

The Battery row gives the headset's charge and `on battery`, `charging` or
`fully charged`, amber at 30 % and red at 15 % while not charging. At 100 %
on its base station it says `fully charged`, though the base station no
longer reports it charging. While the headset is on, the daemon reads the
battery once a minute, and again when the headset is put on or taken off its
base station.

While the headset is off or the base station does not answer, the row keeps
the last reading and adds the time it was read, with the date for a reading
from an earlier day. The row shows no reading while no base station is
attached or the daemon cannot be asked. It shows none either while the
headset has not been on and linked to its base station since the base
station was plugged in or the daemon started.

The Balance row is the live mix that the headset's Game and Voice buttons
set. Its slider is grayed out, with Voice at the left, Game at the right and
ticks at 25, 50 and 75 %. The row is read only, and shows while the headset
is on. Writing Default balance moves the live mix to the same place.

`gearbench-ctl headset` prints the same, a line each: `headset:`, which
reads `on` or `off or out of range`, and `on, docked` or
`switched off, docked` while the headset sits on its base station;
`versions:`; `battery:`; and `balance:`. While the base station does not
answer, the command says so and gives the time of the readings below, dated
when they are from an earlier day. Before the first full read, it says the
settings have not all been read yet. `gearbench-ctl status` says whether a
base station is attached.

Every 5 s, whether or not the window is open, the daemon asks the base
station whether the headset is on and, while it is, for the live balance and
the active preset. This lets the tab and `gearbench-ctl headset` follow the
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
  Frequency, 80 to 15000 Hz. Bands 2 to 4 also show Width, 0.1 to 3.0: the
  band's width as a multiple of its frequency (1/Q), where 2.0 is wide and
  0.5 narrow. Bands 1 and 5 are shelves and have no width. A gain is sent
  after 0.3 s at rest, a frequency or a width after 0.6 s.

`gearbench-ctl headset-eq` changes any preset with `--preset N`, and the
active one without it. `--gains 0,2,0,-1,3` sets all five gains, and `--band
3:2750:1.5` sets one band's frequency and width (`--band 1:90` for a shelf),
once for each band. A width is rounded to a tenth, as the Width row shows
it. Gearbench checks everything before the first write, so one bad value
writes nothing. The command answers with the preset as read back.

Import from Astro Command Center… loads an `.astroeq` file, the preset file
Astro's app writes on Windows or the Mac. It goes into the preset that was
active when the dialog opened: its name, its five gains and all five bands.
`gearbench-ctl headset-import "A50 MOD KIT.astroeq" --preset 3` does the
same for any preset.

A file that does not parse, or holds a value that is missing, out of range
or not a number (a whole number for a gain or a frequency), is refused
before anything is sent, and the message names it. A `Name` that holds a
control character is refused too. Both refuse anything but a regular file,
such as a named pipe or a device, and a file over 16 KiB. In the name, a
line or paragraph separator, or a character that turns the direction of the
text after it, becomes U+FFFD. The exception is a separator written as
itself, outside quotes, at the start or end: it is trimmed with the white
space there.

Reset to default, under Load preset to headset…, writes the active preset's
own name, gains and bands back: Rock and Alt in preset 1, FPS Spatial in 2
and Everyday in 3. Gearbench designed the three from published measurements
of the A50 Gen 4 and a model of the base station's filters. Rock and Alt
adds a little bass and cuts the low mids, FPS Spatial takes the bass down
and lifts the footstep range, and Everyday is the daily one, nearest
neutral. The row's tooltip names the default. Like any change, a reset is
unsaved until Save, and an edit of the preset still settling is dropped.
`gearbench-ctl headset-eq --reset` does the same, `--preset 2` for preset 2,
and the other options then change the default before it is sent.

Which preset is active is not on the unsaved list, since the base station
gives no saved value to compare it with.

### Saved presets

Gearbench keeps up to 16 presets of your own on this computer, apart from
the base station's three. Save as new preset…, under Import in Equalizer,
keeps the active preset's sound as its rows show it, a slider still settling
included. You type its name, which starts as the active preset's own.
`gearbench-ctl headset-eq --save-as "Night"` does the same after its other
options, and `--save-as` with no name keeps the preset's own name.

Load preset to headset…, under Save as new preset…, opens a dialog that
lists the saved presets, each with its five gains. With none saved yet, the
dialog says to use Save as new preset…. Under the list, Load into picks slot
1, 2 or 3, the active one each time the dialog opens. A preset's Load puts
it in that slot: its name, gains and all five bands. The Equalizer group
shows them when that slot is the active one; either way they are unsaved
until Save, as any change is. Load needs the base station attached and
answering, with no save running; the rest of the dialog works without it.

The menu button beside a preset's Load holds:

- Rename…, which asks for the new name.
- Replace with the active preset, which gives it the active preset's gains
  and bands as its rows show them and keeps its name. To change how one
  sounds, load it into the active slot, shape it with the Equalizer's
  sliders, then replace.
- Delete, which takes it out once you confirm; those after it move up a
  number. A slot it was loaded into keeps it.

Add from Astro Command Center…, at the bottom of the dialog, adds an
`.astroeq` file to the saved presets, read and refused as the Equalizer's
import reads and refuses one.
`gearbench-ctl headset-import "Night.astroeq" --library` does the same.

`gearbench-ctl headset-library` lists the saved presets by number, each with
its five bands, and `headset-library 2` shows the second. With a number,
`--from-slot` takes the active slot's gains and bands, or `--from-slot 3`
slot 3's, `--gains` and `--band` change them as `headset-eq` takes them,
`--name` renames the preset, and `--write 1` then writes it to slot 1.
`--delete`, alone, deletes it. Everything given is checked before anything
is saved or sent. With `--write`, the change is saved to the library first,
and it stays there if the write to the slot then fails, as with no base
station found. The library is the file `a50-eq.json` (see "Settings files");
the daemon never reads it, and `headset-library` asks the daemon only for
`--from-slot` and `--write`.

### Stream port

Game, Chat, Mic and Aux, 0 to 100 %, set the mix the base station sends to
its stream port: `gearbench-ctl headset-set stream-game 80`, and likewise
`stream-chat`, `stream-mic` and `stream-aux`. These have not run on the
hardware yet.

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

The base station cannot report Auto shut-off's flash value, so Revert to
saved cannot put it back. For the same reason, Auto shut-off is on the
unsaved list from the time it is written until a save works, the base
station is unplugged or the daemon restarts. There is no row for the base
station's light brightness: the base station stores it, but setting it did
not change its lights.

### Saving

The base station runs on its memory and starts from its flash after a power
cycle, such as being unplugged. A save to flash takes over 2 s and the
flash's wear limit is unknown, so Gearbench writes flash only when you ask.

While anything differs from flash, a banner at the top of the tab lists it,
with Save. The unsaved list has each setting whose value differs from its
flash value, each preset whose name, gains or a band does, and Auto shut-off
once written. Any other setting put back to its flash value leaves the list.

`gearbench-ctl headset` prints the same list on its `unsaved:` line. Until
the daemon has read everything once, the line also says the rest is not read
yet, and that `gearbench-ctl headset --read` reads it. That hint is left out
while a save runs or the base station is not answering, when `--read` asks
nothing. At other times, while the list is not empty,
`gearbench-ctl headset` adds the command that saves it, and `headset-set`,
`headset-eq`, `headset-import` and `headset-library --write` add it when the
setting or preset they changed is on the list.

The banner hides while no base station is found, since an unplugged base
station comes back with its flash values. The banner shows again if the
daemon's read then finds differences.

Save, or `gearbench-ctl headset-save`, writes every setting and preset to
flash. A slider, frequency or width not sent yet goes first. While the save
runs, the banner says so with no button, and the rows are grayed out (Load
preset to headset… still opens, with its Load grayed out). Until it ends,
`gearbench-ctl headset` says it is saving, and any other change is refused.

A save started with Save ends with a toast saying it worked or why not; one
started with `gearbench-ctl headset-save` ends with none. Either way
`gearbench-ctl headset` gives why it failed on its `save:` line. A save that
worked is read back at once. After a failed save the daemon reads everything
again: at once for Save, and for `gearbench-ctl headset-save` once the
headset is on, or at `gearbench-ctl headset --read`.

If the base station is unplugged or the daemon stops during the save, no
toast says how it ended. After the daemon stops, the unsaved list from its
next read says what the save left out, apart from Auto shut-off. After an
unplug, the list is empty whether or not the save got through.

Revert to saved, under the Base station group, asks first, with Cancel the
default. It then puts every setting and preset that differs back to what
flash holds, and reads everything again. `gearbench-ctl headset-revert` does
the same. The row is grayed out while nothing is unsaved.

Which preset is active stays as it is. A written Auto shut-off stays too,
since it has no saved value to go back to, and a note says so. A value the
base station refuses, or one Gearbench refuses before sending it, is named
in a note if it still differs from flash once everything is read again, and
the rest go back. The tab shows a note as a toast. When something still
differs from flash afterward, `gearbench-ctl headset-revert` names it and
exits 1.

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
Chat. The first time, each output starts at 40 % and the microphone at 100
%; WirePlumber keeps each level from then on. The profile set keeps
PipeWire's automatic profiles (`auto-profiles = yes`). With them, beside the
profile with both outputs, the card has Game, Chat or the microphone alone,
and Game or Chat with the microphone. The profile with both outputs ranks
highest, and Sound settings can list the others under a Configuration row
for the A50. The node names stay PipeWire's own,
`alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game` and `.stereo-chat`,
so a default output or a program's saved choice still points at the same
output.

WirePlumber reads the two files only when it starts, and it starts with your
user manager. Until it restarts, GNOME lists one entry, and while Gearbench
Spatial plays to the A50, the Spatial sound group shows its Outputs row. To
restart it, log out of every session and back in (with lingering on, restart
the machine instead), or run `systemctl --user restart wireplumber`, which
stops all sound for a moment. `install.sh` restarts it when one of these
files or the G432's rule is new or changed.

### The headset's own buttons

- Dolby switches the base station's Dolby processing, Pro Logic IIx and
  Dolby Headphone, on the Game channel: the headphone icon is on, the star
  off. The button is its only switch. No USB command turns it on or reads
  it, so the tab has no Dolby switch and Gearbench cannot tell which state
  it is in. Keep it on the star while a game plays on Gearbench Spatial on
  the A50.
- EQ switches the active preset in the base station. The Preset row
  follows within 5 s, or within 10 s while the window is not the active one.
  `gearbench-ctl headset` asks the base station when it runs, so its
  `preset:` line shows the new preset at once.
- Game and Voice move the live balance, which the Balance row shows within
  5 s, or within 10 s while the window is not the active one. Gearbench sets
  only the Default balance.

### Plugged in, unplugged or not answering

The daemon attaches a base station within about 5 s of it being plugged in.
It lets go of one unplugged, after which `gearbench-ctl headset` says no
base station was found. A base station found that does not answer is looked
at again after 5 s, then at doubling waits up to a minute. Unplugging it for
more than about a second ends the wait, and once it is plugged back in it is
looked at within about 5 s. One found that Gearbench cannot set up is looked
at again every 5 s, and `gearbench-ctl headset` and the tab say to unplug it
and plug it back in.

One attached that stops answering is sent nothing but a status request every
30 s until it answers. Meanwhile the tab says Not answering,
`gearbench-ctl headset` says when its readings were taken, and a banner on
the tab says to unplug the base station and plug it back in. After a resume
the daemon asks the base station again at once.

### Without Gearbench running

The settings belong to the base station, and Gearbench keeps no copy. With
the daemon stopped, the headset keeps what the base station holds in its
memory, and after a power cycle what it holds in flash. Stopping the daemon
sends the base station nothing. The low-battery notices and the tray's
headset line need the daemon.

## The Logitech G432

Gearbench sets the sidetone of the Logitech G432, a wired headset, through
its USB adapter (`046d:0a9c`), and gives it an equalizer. Both were built
from what others report of the headset, and tried on a G432 on 2026-10-17
and again on 2026-10-18. A change made since has not been tried on one yet.
`gearbench-ctl status` names the G432 when its adapter is plugged in.

`install.sh` and the package give the adapter a WirePlumber rule,
`60-gearbench-logitech-g432.conf`, that selects PipeWire's own
`analog-only.conf` profile set for it. Without the rule, PipeWire offers the
adapter's one output twice, as analog and as digital stereo. Only the analog
one moves the adapter's own volume, and a switch between the two stops the
sound and pauses players. With the rule, the adapter has the analog output
alone, and the microphone. The node names stay the ones PipeWire gave them
before, such as
`alsa_output.usb-Logitech_G432_Gaming_Headset_<serial>-00.analog-stereo`,
where `<serial>` is what the adapter reports. So a saved volume or a
program's saved choice still points at the same output. WirePlumber reads
the rule when it starts, as with the A50's files (see "Game and Chat").

![The Headset tab on the Logitech G432, picked on its switcher beside the Astro A50: Sidetone, Microphone with Noise suppression, and Equalizer](images/g432.png)

The Headset tab shows the G432's page while its adapter is plugged in, and
the A50's otherwise. With both plugged in, a switcher at the top of the tab
picks one. The page, from the top:

- Logitech G432. Its status row says Connected, with the adapter's node in
  its tooltip. Its Firmware row gives the version the adapter reported when
  it was plugged in, such as U1 42.00.B0001, or Not read.
- Sidetone, with its slider, Sidetone: your voice in the earcups, whose row
  gives the level, such as 40%, or Not read, and Put back, which names the
  level it puts back.
- Microphone, with the Noise suppression switch.
- Equalizer, with the Logitech G432 Equalizer switch, Preset, Name, a slider
  each for the bass, the ten bands and the treble, Copy from Astro A50, Save
  as new, Reset to default and Delete preset.
- Spatial sound, the same group as on the A50's page (see "Spatial sound").

A slider sends its change once you stop moving it.

The sidetone is your voice in the earcups: how loud the microphone is in
them, from 0 to 100. `gearbench-ctl g432-sidetone` shows the firmware
version and reads the level from the headset, and
`gearbench-ctl g432-sidetone 20` sets it. The daemon reads the level each
time the adapter is plugged in. It keeps the first level it read since it
started, through an unplug too. `gearbench-ctl g432-sidetone reset` puts
that level back, or 0 when the daemon wrote a level before any read found
one. If you hear nothing, open the microphone in a program, such as the
input level meter in GNOME's Sound settings, and listen again. The headset
keeps its level through an unplug.

Noise suppression, in the Microphone group, makes Logitech G432 Microphone,
a cleaned-up input from the G432's microphone. Pick it as the input in a
game or chat app, or in GNOME's Sound settings. It is meant for steady
background noise, such as a fan or a hum. In a test without a headset,
steady white noise came through about 18 dB lower (measured 2026-10-18). The
G432's own microphone stays as it is, so a program that records that one
hears it unchanged. It is off until you switch it on, and Gearbench never
makes it the default input.

Noise suppression is PipeWire's own echo-cancel module with its webrtc
plugin, so nothing is installed for it. Its automatic gain stays off, so
your voice keeps the level you set. Its echo canceler also runs, on the
sound of the default output. `gearbench-ctl g432-noise on` and `off` do the
same, and `gearbench-ctl g432-noise` says whether it runs and what it
cleans. Switching it on writes its PipeWire configuration to
`$XDG_RUNTIME_DIR/gearbench-g432-mic.conf` and starts it as
`gearbench-g432-mic.service`, which runs until you switch it off or log out.
It refuses while the G432 is not plugged in, or while its sound profile has
no microphone. It has been heard on a G432, not yet in a call.

While the daemon drives it, Gearbench sends the adapter only the sidetone's
lookup, its read and its write, and the three questions that read its
firmware version when it is plugged in. When the daemon is not running,
`gearbench-ctl report` asks it more, each a read (see "Reporting a device").
Only Gearbench should talk to the adapter: another program on its node sees
Gearbench's replies and mixes its own in.

The equalizer is not in the headset: it is a separate output, Logitech G432
Equalizer, that plays to the G432 and to no other output. It is off until
you switch it on. Switching it on makes it the default output while the
G432's own output is the default, and after it went off as the default with
the G432 unplugged (below). Otherwise, pick it in GNOME's Sound settings, or
in a program's own output setting. It has ten bands an octave apart, from 32
Hz to 16 kHz, and a bass shelf at 100 Hz and a treble shelf at 8 kHz, each
from -12 to +12 dB. A boost raises its band on top of the volume and leaves
the rest of the sound as it was, so near full volume a boosted band can
clip. If a loud passage crackles, lower the volume or the boost.

The equalizer plays one of its presets, each a name and its gains. A change
in the tab goes to the one playing: pick it in Preset and rename it in Name.
Save as new keeps the one playing, as it sounds now, as a new preset under
the name typed there, and plays it. There can be 16 presets in all. The G432
has presets of its own, apart from the A50's. Three are shipped, and Reset
to default puts back a shipped preset's own name and gains. A preset saved
as new has no default. Delete preset takes it out once you confirm, and the
preset before it plays in its place, as `gearbench-ctl g432-eq --delete`
does. A shipped one is never deleted.

The shipped three are the A50's three, made for the G432 from published
measurements of it. They aim at the Harman over-ear target, the response
most listeners prefer in Harman's studies. On a G432 the gains as designed
sounded too subtle, so each preset ships at 2.5 times them. Everyday adds
bass and lifts voices, and plays until you pick another. Rock and Alt adds
more bass and cuts the low mids. FPS Spatial takes the bass down and lifts
the region where footsteps and reloads are heard. The largest boost in any
of them is about +11.6 dB, near 4 kHz in FPS Spatial.

While the equalizer is on, the G432's own output is held at 0 dB, the
adapter's volume with no cut, about 83 % in GNOME's Sound settings. So the
equalizer's volume sets how loud it is. Switching the equalizer on sets its
volume to sound as loud as the G432's own output did. Switching it off sets
the G432's own volume to sound as loud as the equalizer did. The two show
different numbers for the same loudness, since the equalizer's 100 % is the
G432's 0 dB. The G432 at 40 % is the equalizer at about 48 %. With the G432
above 83 %, the equalizer starts at 100 %, a little quieter than the G432
was, because more would add gain that clips a loud passage.

While the equalizer is on, something that plays straight to the G432's own
output plays at that 0 dB, which can be louder than before. Send it to the
equalizer instead. A G432 unplugged and plugged back while the equalizer is
on is held at 0 dB again within a few seconds of its return. With the
equalizer off, a replug changes nothing.

If the hold or the put-back fails, the window or `gearbench-ctl g432-eq`
says so, and the equalizer still switches. If the equalizer's volume cannot
be set at a switch-on, it says so too, and the G432's own output is not
held, so both volumes apply. If the equalizer cannot be made the default at
a switch-on while the G432's own output is the default, it says so, and the
G432 is not held either. What plays there stays at the G432's own volume.
Switch the equalizer off and on to try again.

While the equalizer is on, Gearbench Spatial on the G432 plays through it
(see "Spatial sound"), and after a switch-off straight to the G432 again. A
switch-on waits up to 2 s, before the hold, for Gearbench Spatial to move to
the equalizer. If it has not moved by then, the switch-on says so and does
not hold the G432, so spatial sound is no louder. Switch the equalizer off
and on to try again.

Switching the equalizer on while the G432's own output is the default makes
the equalizer the default, and says so. What plays on the G432 then moves to
the equalizer, as loud as before. Switching it off while it is the default
output makes the G432's own output the default first, and says so. What
plays on the equalizer then moves to the G432 before the equalizer goes.
Switching it on again while the G432's own output is still the default makes
the equalizer the default again. With another output the default, such as
one you picked after the switch-off, a switch-on leaves your pick as it is.
Only after a switch-off as the default with the G432 unplugged does the next
switch-on make the equalizer the default again whatever the default is, and
say so.

```
gearbench-ctl g432-eq on                      switch it on; the G432 must be plugged in
gearbench-ctl g432-eq --select 2              play preset 2, FPS Spatial
gearbench-ctl g432-eq --band 1000:4 --bass 3  change the playing preset's gains, on or off
gearbench-ctl g432-eq --preset 1 --name Mine  rename preset 1; --preset picks the preset changed
gearbench-ctl g432-eq --reset                 a shipped preset back to its default name and gains
gearbench-ctl g432-eq --save-as Night         the playing preset kept as a new one, 4, which plays
gearbench-ctl g432-eq --preset 4 --delete     preset 4, saved as new, deleted; alone, the playing one
gearbench-ctl g432-eq --flat                  every gain of the preset back to 0 dB
gearbench-ctl g432-eq off                     switch it off, which removes the output
gearbench-ctl g432-noise on                   noise suppression on: Logitech G432 Microphone
```

Copy from Astro A50 loads one of the presets in the A50's base station into
the playing preset, once the daemon has read them (see "Saving") and while
it is plugged in. Pick it in the row's list, then Copy, and the playing
preset takes its name, and its sound as the A50 makes it, fitted onto the
G432's ten bands with the bass and treble at 0. With none to list, the row
says why. For the A50's own presets the fit is 0.5 to 0.8 dB from the A50's
curve on average. Those presets were made to correct the A50's own
sound, though, so on the G432 the same preset sounds different.

The presets are kept in `~/.config/gearbench/g432-eq.json`, and a change
while it plays takes effect at once. A `g432-eq.json` that cannot be read,
or that holds a preset Gearbench leaves out, takes no change until you fix
it or move it away. The Equalizer group says why, and
`gearbench-ctl g432-eq` refuses with the same words. Before
the presets, `g432-eq.json` held the gains alone. Those gains become preset
1, named Custom, which plays, unless every gain was 0 dB. The old file is
kept as `~/.config/gearbench/backup/g432-eq.v1.json` before the first save
over it.

Switching the equalizer on writes its PipeWire configuration to
`$XDG_RUNTIME_DIR/gearbench-g432-eq.conf` and starts it as
`gearbench-g432-eq.service`, which runs until you switch it off or log out.
After a login it is off, and the G432 is back at its own volume. Beside it,
`gearbench-g432-eq-watch.service` watches PipeWire for the G432's return and
stops with it. If it cannot start, the switch-on says so, and switching the
equalizer off and on after a replug holds the G432 again. While the G432 is
unplugged, the equalizer stays listed and plays nothing, and it plays again
once the G432 is back.

## Spatial sound

![The Spatial sound group](images/headset-spatial.png)

Gearbench Spatial is a 7.1 output of its own, for games. It plays on the
headset you use: the Astro A50, through Astro A50 Game, or the Logitech
G432. It renders each of the seven speakers for headphones with the SADIE II
database's measured head responses. A 5.1 or 7.1 game's rear and side sounds
then come from behind and beside you. Music and voice chat stay on the plain
outputs.

Before Gearbench 1.1.0 it was the A50's alone, named Astro A50 Spatial, with
the launch option `PULSE_SINK=gearbench-a50-spatial %command%`. That name is
gone, and a game whose launch option still names it plays on the default
output in stereo. Change its launch option to
`PULSE_SINK=gearbench-spatial %command%`. While such a game plays, the
switch's subtitle names it.

Gearbench Spatial runs as a user service of its own,
`gearbench-spatial.service`, from each login once you switch it on. The
service follows PipeWire and plays only while a headset's output is there. A
headset plugged in lacks that output while its card is on a profile without
it, such as the A50's Chat alone. With none, the output is gone until one
comes back.

With both headsets plugged in it plays to the one picked in the Headset it
plays to row, and to the other while that one is unplugged. With none picked
it plays to the one connected first. On the G432 it plays through Logitech
G432 Equalizer while the equalizer is on, so the equalizer shapes spatial
sound too. With the equalizer off it plays straight to the G432 (see "The
Logitech G432"). At a pick, and when a plug-in, an unplug or an equalizer
switch changes where it plays, the output starts again there. A game playing
on it goes to the default output for about a second and comes back, still in
7.1.

The Spatial sound group is the last one on the Headset tab, on the A50's
page and on the G432's alike. Both show the same output and the same
settings. It switches the service with `systemctl --user` and shows what
systemd and PipeWire (`pw-dump`) say of it. The daemon has no part in it, so
the service keeps running with the daemon stopped. Without the window or the
daemon, `gearbench-ctl headset-spatial` does the same from a terminal.

The group's rows:

- Gearbench Spatial, the switch, is off until you turn it on. On enables the
  service and starts it; off stops it and disables it. On stays on across
  logins and machine restarts. Its subtitle says the state, in the table
  below.
- Headset it plays to shows while both headsets are plugged in, or once you
  have picked one: The one connected first, the default, Astro A50 or
  Logitech G432. Changing it restarts Gearbench Spatial while it runs.
- Head picks whose measured head is used. KU100, the default, is the Neumann
  KU100 (SADIE II subject D1), which listeners preferred most in SADIE II's
  own test. KEMAR (subject D2) is a dummy head with torso and shoulders.
  Changing it restarts Gearbench Spatial while it runs. While Gearbench
  Spatial is not running, as with the switch off or no headset plugged in,
  the head applies at its next start. Both headsets use the same head.
- Room is Competitive, the default, with no room: direction alone. Immersive
  adds a short, dry room about 11 dB under the sound, which moves it out of
  your head. The room changes while the output plays, with no restart. If
  you choose it while Gearbench Spatial is not running, as with the switch
  off, no headset plugged in or a start that failed, it applies at its next
  start. If you choose it while Gearbench Spatial is starting, it waits up
  to 2 s for the output. When the change fails or the output is still
  starting after 2 s, a toast says the room did not change, with the cause
  behind Details. The room then applies at the next start. Both headsets use
  the same room.
- Steam launch option shows `PULSE_SINK=gearbench-spatial %command%`, with a
  copy button.
- Other launchers shows how to set the same variable in the game's
  environment, with a copy button for `PULSE_SINK=gearbench-spatial`.
- Or pick it in GNOME says to pick Gearbench Spatial before the game starts,
  and your usual output after it quits.
- Dolby on the star shows while the switch is on and the output plays on the
  A50, or would if it were running (below).
- Outputs shows while Gearbench Spatial plays to a headset whose outputs
  GNOME still lists the old way, until WirePlumber restarts. The old way is
  the A50's Game and Chat as one output, or the G432's analog and digital
  outputs.

The switch's subtitle and its status icon:

| Subtitle | Icon |
| --- | --- |
| `Off` | none |
| `On, linked to Astro A50 Game`, or the G432's output, or `Logitech G432 Equalizer` | green check mark |
| `In use: ARC Raiders, 8 channels, 3 streams`, a game playing on it | green check mark |
| `Starting` | amber warning sign |
| `On; waits for Astro A50 Game or a Logitech G432's output` | amber warning sign |
| `On, but stopped: switch it off and on again` | amber warning sign |
| `On, but not linked to Astro A50 Game` or `the G432`, and PipeWire's reason when it gives one | amber warning sign |
| `Off, but a Gearbench Spatial runs that Gearbench did not start` | amber warning sign |
| `Did not start: journalctl --user _SYSTEMD_USER_UNIT=gearbench-spatial.service says why` | red error sign |
| `On, but no chain runs: the chain was not written for ...`, with the cause | red error sign |
| `On, but PipeWire's pw-dump is not installed` | red error sign |
| `On, but PipeWire could not be read: ...` | red error sign |
| `Not installed: run ./install.sh again, or install the Gearbench package` | red error sign |
| `systemctl --user did not answer: ...` | red error sign |

`On, but no chain runs` says what kept Gearbench Spatial from starting on
the headset, such as a missing file. It tries again when you plug the
headset in again, switch spatial sound off and on, or change the head or the
headset it plays to.

A game that sends only 2 channels adds a line saying to set its audio output
to surround or System Default and start it again. A game whose launch option
names the old `gearbench-a50-spatial` adds one saying to change it. The
switch and the three choices are grayed out while a change runs, and when
the service is not installed or systemctl does not answer. While systemctl
does not answer, the switch stays where it was. The three launch rows are
hidden while the service is not installed.

A change that fails shows a toast saying why: systemctl's or PipeWire's own
error when it prints one, and Gearbench's words otherwise. An error too long
for a toast shows a short line, with the whole error behind Details. When a
choice cannot be saved to `spatial.json`, the toast names that file and
gives the system's error, as in
`Could not save ~/.config/gearbench/spatial.json: Permission denied`.

To play a game on Gearbench Spatial:

- Steam: paste the launch option into the game's Properties, General,
  Launch Options.
- Lutris: the game's Configure, System options, Environment variables, with
  `PULSE_SINK` as the key and `gearbench-spatial` as the value.
- Heroic: the game's Settings, Advanced, Environment Variables, with the
  same name and value.
- A terminal: `PULSE_SINK=gearbench-spatial <the game's command>`.
- Or choose Gearbench Spatial as the output in GNOME's quick settings before
  the game starts. GNOME keeps that choice as the default, so pick your usual
  output again when the game is done.

In the game's own audio settings, choose surround, 7.1 or System Default,
not stereo or headphones. A game with a headphone mode of its own is better
left on the headset's own output with that mode. A Proton game picks its
channel count once, when it opens its audio, so switch spatial sound on
before the game starts. With spatial sound off, a game with the launch
option plays on the default output in stereo. If you switch spatial sound on
later, the game moves to Gearbench Spatial still in stereo, and the second
line says to start it again.

The launch option names the output, and any program can make an output of
that name. If one does while spatial sound is off, the switch's subtitle
says a Gearbench Spatial runs that Gearbench did not start, and the game
plays into that program's output.

While a game plays on Gearbench Spatial on the A50, keep the headset's Dolby
button on the star, which is Dolby off. Otherwise the base station processes
the sound a second time. No USB command reads the button, so the Dolby on
the star row reminds you. Its icon turns to an amber warning sign while
something plays on the A50. A toast says so when the Headset tab finds
something playing there that its last look did not. A look that could not
read systemd or PipeWire leaves the icon as it was and shows no toast. The
tab looks as it comes up, as its page changes, and every 5 seconds while it
shows. So a game started while the window is closed or on another tab gets
the toast when you next open the tab, if it still plays.

A flatter EQ preset suits Gearbench Spatial. The head measurements assume a
neutral headphone, and Astro's ASTRO preset adds 7 dB at 4 and 7 kHz, where
the front and back cues are.

The head, the room and the headset picked are kept in
`~/.config/gearbench/spatial.json`. On and off is the service's enable link,
kept by systemd. The service writes its PipeWire configuration to
`$XDG_RUNTIME_DIR/gearbench-spatial.conf` each time it starts the output,
for the headset it plays to. It logs to
`journalctl --user _SYSTEMD_USER_UNIT=gearbench-spatial.service`, the
command its `Did not start` subtitle names, with a line each time the output
starts on a headset or stops for want of one.
`journalctl --user -u gearbench-spatial` also shows what systemd says of the
service, such as a start refused after five failures, and any line another
program of yours logs under its name.

## Idle, lock and suspend

The keyboard's lights go off while the screen is locked or blank, and a
mouse synced to the keyboard goes off with them; a mouse with its own color
keeps it. While the screen is still locked, any input brings them back: a
key, the pointer, or a G, M or MR key. After 30 s without input they dim to
half, and after 60 s they go off again, even while a game keeps the screen
from going idle. Unlocking brings them back. A keyboard dimmed or turned off
by its own idle dimming stays so until the next keypress, a lighting change
or new idle times; a synced mouse comes back with any input. The brightness
you chose is not touched: dim is half of every color. A lighting change made
while the lights are down is saved and shows when they are back.

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
the mouse over, lit. With the keyboard on and in range, a new Lights off
after wakes it if it has dimmed or gone off, as a new Dim after does, and a
synced mouse lights with it. With the keyboard off or out of range, a new
Lights off after alone changes only when the mouse goes off: a mouse off
already comes back to half for a longer time that has not run out yet, and
goes off once it has; one that has run out already leaves it off. Switching
sync on when it is already on changes nothing. Never dim keeps the mouse lit
too. The mouse also goes off with the screen lock, the
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
profiles, except on a G915 TKL: it gets its own profile back until then, so
its F1 to F12 type for whoever has the screen. Whether its own lighting
shows meanwhile has not been checked.

Stopping the daemon hands the keyboard back: it shows its own stored effect,
its own power saving turns it off a few minutes after the last key, and its
G keys type its onboard bindings. When the machine shuts down or reboots,
the daemon turns the keyboard's lights off instead, and a synced mouse's
with them, so the keyboard does not light up again with the PC off. A mouse
that is not synced gets its own lighting back, as at a stop. By 2026-10-03
this had run at reboots, not yet at a power-off. A keyboard the daemon has
found not answering, such as one switched off behind its receiver, is sent
nothing at either stop.
A G915 TKL is handed back its own profile after the lights go off, or its
F1 to F12 would type nothing until it is switched off and on; whether it
lights up again at that has not been checked.

## Battery and DPI notices

Gearbench's desktop notices need `python3-gi`. Switching Gearbench off in
GNOME Settings, Notifications turns all of them off, and the DPI notices also
have a switch of their own. A tray menu choice the daemon refused comes back
as a notice, with the reason.

### Low battery

The daemon reads the keyboard on its receiver once a minute, and a notice
says when the battery is below 30 %, and again below 15 %. Two readings in a
row have to be below. The G915's percentage comes from its battery's
voltage, which rises when the lights go off, so a rise does not bring the
notice back.
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

![A drawing of a DPI notice from Gearbench: G502 X PLUS: 3200 DPI](images/dpi-notice.png)

When a button bound to `dpi-up`, `dpi-down` or `dpi-cycle` steps the DPI
(G8 and G7 on the G502 X PLUS unless rebound), or a mouse's own DPI button,
such as the G305's, moves it to another stage, a notice gives the mouse and
its new DPI. A step within 1.5 s of the last changes the notice in place. A
later one closes it and posts a new one: a notice that changes just as its
banner slides away stops GNOME from showing any app's banners until you log
out. Gearbench takes the notice down 5 s after the last step. A
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

![A drawing of the tray icon's three looks on a dark top bar: Gearbench's own icon with nothing wrong, a low battery, and a warning sign for a problem](images/tray-icon.png)

The icon is Gearbench's own, a headset around a small screen. It turns into
a warning sign while anything is wrong, meaning anything `gearbench-ctl
status` lists under problems, and, with nothing wrong, into a low battery
while a keyboard, a mouse or the headset is below 15 % and not charging.
Where the tray shows tooltips, pointing at the icon gives the batteries and
the problems, a line each. Ubuntu's tray shows no tooltips, and the menu has
both.

![A drawing of Gearbench's tray menu under its icon: two battery lines, Open Gearbench, five DPI stages and three switches](images/tray-menu.png)

A click opens the menu:

- Anything wrong, a line each with a warning sign: what `gearbench-ctl
  status` lists under problems. A line longer than 80 characters is shortened;
  `gearbench-ctl status` has the whole of it.
- Each device's battery, the headset's while it is on: `Astro A50: 91%`.
- Open Gearbench, which starts the window or brings it to the front.
- The selected mouse's DPI stages, the current one marked, while that mouse
  has stages and is answering. These are the stages in force: a loaded
  game profile's own in host mode, else onboard profile 1's. Choosing one
  sets it as `gearbench-ctl mouse-dpi` does.
- M profile, while a keyboard with lighting and no M keys, the G515, is
  attached: its three M profiles, the active one marked. Choosing one
  switches to it as `gearbench-ctl profile` does.
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

## The command line

`gearbench-ctl` sends a request to the daemon and prints its reply. `--json`,
before or after the subcommand, prints the raw JSON reply instead, with the
library added for `headset-eq --save-as`. The exit
code is 0 when the command did what was asked, and 1 when the daemon refused
or the command refused its arguments or failed, with `error:` and the reason
on standard error, or with `--json` the reply on standard output. It is 2 when
the daemon could not be reached, did not answer in time, closed the
connection without answering or sent a reply that could not be read, and when
the command line did not parse, as with an unknown command or option, or a
value of the wrong type or outside its choices, such as `profile 4`.
`gearbench-ctl --version` prints the version. `probe`, `report`, `keys`,
`config`, `gui`, `daemon`, `headset-spatial`, `g432-eq`, `g432-noise` and
`headset-import --library` need no daemon; `headset-library`
needs it only to read or write a slot, and `import-ghub` and `import-pack`
only to import. `man gearbench-ctl` has every option; from a checkout,
`man ./man/gearbench-ctl.1`.

Names in the output come from devices, files, running programs and
PipeWire, and any of them can hold a character a terminal would act on or
not show. The formatted output prints each such character as its escape,
`\x1b` for ESC or `\u202e` for a right-to-left override; `--json` escapes
them itself. The names of running programs, and of the streams and the output
in Gearbench Spatial's report, show U+FFFD in place of such a character, in the
window too. A stream's or output's name over 100 characters is cut to its
first 99 and an ellipsis. G HUB's names are changed as they are read (see
"Moving from G HUB").

The daemon and its state:

```
gearbench-ctl status                 what the daemon and the devices are doing, and any problems
gearbench-ctl gui                    open the window
gearbench-ctl daemon --verbose       run the daemon in the foreground with debug logging; a macro's steps show by type only
gearbench-ctl probe                  read-only dump of the keyboard; refuses while the daemon runs, unless --force
gearbench-ctl report                 what every Logitech device on USB says about itself, to paste into an issue (see "Reporting a device")
gearbench-ctl config                 the configuration on disk
gearbench-ctl save                   write the daemon's copy over a hand edit (see "Settings files")
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
gearbench-ctl mouse-bind G6 dpi-shift   bind the selected mouse's button: back, forward, dpi-shift, dpi-up, dpi-down, dpi-cycle, a key, a macro
gearbench-ctl mouse-defaults         put the selected mouse's buttons back to their default bindings
gearbench-ctl mouse-color '#ff00aa'  the mouse's own color
gearbench-ctl mouse-effect breathe   an effect gearbench-ctl mouse lists, with --color
gearbench-ctl mouse-sync on          the mouse takes the keyboard's color
gearbench-ctl sync-keyboard G915     the keyboard model driven, which the mouse follows, or first
gearbench-ctl mouse-host off         each mouse with host mode runs its onboard profile, not host mode
gearbench-ctl dpi-notices off        no desktop notice when a DPI button steps the DPI
```

These write the mouse's onboard profile 1, after backing up every profile
sector. On a mouse whose onboard profile Gearbench does not write yet, they
are refused with nothing written; in host mode, `mouse-rate` sets the rate
live instead and keeps it for the model. With a game profile loaded and
`mouse-host on`, on a mouse with host mode, `mouse-rate` and
`mouse-dpi-stages` save the game profile's own rate and stages instead, and
`--reset` drops them:

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
gearbench-ctl headset-import night.astroeq   an .astroeq preset file into an EQ preset; --library into the saved presets
gearbench-ctl headset-library        the A50's saved presets: list them; with a number, --from-slot, --gains, --band, --name, --write SLOT, --delete
gearbench-ctl headset-save           write the settings and presets to the base station's flash
gearbench-ctl headset-revert         put back what the base station's flash holds
gearbench-ctl headset-spatial        the spatial output: on or off, headset, head, room, the launch option
gearbench-ctl headset-spatial on --headset g432   on or off, --headset a50, g432 or first, --head ku100 or kemar, --room competitive or immersive
gearbench-ctl g432-sidetone 20       the G432's sidetone, 0 to 100; reset puts back the level first read; alone, shows it
gearbench-ctl g432-eq on             Logitech G432 Equalizer: on or off, --select N, --preset N, --name, --reset, --save-as NAME, --delete, --band FREQ:GAIN, --bass, --treble, --flat
gearbench-ctl g432-noise on          the G432's noise suppression, Logitech G432 Microphone: on or off; alone, its state
```

### Reporting a device

A Logitech device the "Supported devices" table in the README leaves out
can still be supported, and a listed one that misbehaves fixed, from what it
says about itself. A keyboard or mouse that table lists as not yet run on
the hardware has a note saying so in `gearbench-ctl status`,
`gearbench-ctl mouse`, the journal and its tab, which asks for this report
from it and gives the form's address. `gearbench-ctl report` reads what each
Logitech device plugged in over USB says about itself and writes nothing to
any of them: it asks each device only the questions on one fixed list, each
a read, and stops before anything else would be sent.

```
gearbench-ctl report                 the report, as a block to paste into an issue
gearbench-ctl report --json          the whole report, with each question and its reply
```

The block goes on standard output, opened by a line of three backticks and
the word text and closed by a line of three backticks; paste it whole into a
device report, the issue form at
https://github.com/gearbench/gearbench/issues/new?template=device-report.yml,
with the model in the title.
What to do next goes on standard error, below it. For each device it lists
the product id and the connection, the name the device gives itself, its
firmware versions, the features it lists, and what Gearbench can read of
the ones it knows: the lighting zones and effects, the brightness levels,
the lights a keyboard can color one by one,
the onboard memory's layout, the battery, the DPI and report rate, and a
headset's sidetone and equalizer; last, five times over, whether the device
keeps apart its answers to two questions asked at once. For a headset it
also gives what PipeWire, the sound server, says of its sound card: the
card's profiles,
the one in use, and its output's volume, read with `pw-dump` where that is
installed. `--json` adds the factory profiles in the
onboard memory, the read-only copies every unit of a model carries, and
each question and answer in hexadecimal.

Nothing in either form names you, your computer or the unit: no serial
number, no computer or user name, no folder, and nothing from the user
profiles in a mouse's onboard memory, which can hold the text of a macro.

A device Gearbench is driving is left alone, as is anything on the same
receiver: the report gives what Gearbench itself knows of it and says to
stop the service, `systemctl --user stop gearbench`, for the full read, and
to start it again after with `systemctl --user start gearbench`. While the
service runs, or may be running, every device of a model Gearbench drives
is left alone, whether the service is driving it or not: the service opens
those as it looks for them, and two programs asking one device at once can
take each other's answers.

A device no rule of Gearbench's grants you is shown from what the system
says of it without opening it, and the report prints the command that lets
you open that one device until you unplug it:
`sudo setfacl -m u:$USER:rw /dev/hidrawN`, with the device's own node in
place of `hidrawN`. Nothing is installed; run the report again after it.
Some devices send what is typed over that same node, keyboards among them
and mice that send the keystrokes of a button bound to a key, and the
report says so for those: until you unplug the device, any program you run
could read what is typed on it.

The exit code is 0 when the report was printed, whatever it could not read,
and 1 when no Logitech device is on USB, or when it stopped before a
question that is not on its list, with that question unsent: a fault in
Gearbench to report.

## Settings files

These live under `~/.config/gearbench/`
(`$XDG_CONFIG_HOME/gearbench/` when that is an absolute path):

- `config.json`: the three M profiles, each with its lighting for each
  keyboard model, its G-key and MR layer bindings and its bindings for each
  mouse model; which M profile
  is in use, and the game profile loaded by hand, which the next start loads
  again; each keyboard model's brightness, and the saved colors; the settings
  the window and the commands set,
  `mouse_sync_color`, `lights_off_when_locked`, `idle_dim_after`,
  `idle_off_after`, `never_dim`, `dpi_notices`, `mouse_host_mode`, `tray`,
  `mouse_dpi`, the DPI last chosen for each mouse model, and `mouse_rate`,
  the report rate last set live on each mouse model whose onboard profile
  Gearbench does not write; `keyboard_link_notices`, on by default and
  set only by hand, which has the keyboard's receiver say when the keyboard
  goes off or comes back; and `sync_keyboard`, the keyboard model the mice
  follow and a keyboard with no lighting of its own starts from (see "One
  keyboard's lighting, another's"), or null for the keyboard connected first
  since the daemon started; a reload that changes it switches to that
  keyboard, as `sync-keyboard` does.
- `profiles/`: the game profiles, one JSON file each, named after the
  profile: its name in lowercase, with its letters and digits kept and each
  run of anything else between them made one hyphen, as in `arc-raiders.json`
  for Arc: Raiders. A name with no letter or digit gets `profile-` and a short
  hash instead, and one that comes out longer than 200 bytes is cut short and
  given a hash at its end. Each file holds the profile's name, the programs
  that load it, its lighting for each keyboard model, its G-key and MR layer
  bindings, and per mouse model its bindings and its own DPI stages and
  report rate.
- `macros/`: the macros, one JSON file each.
- `backup/`: a mouse's onboard sectors, saved before each write, and a
  copy of `config.json`, of each game profile's file and of `g432-eq.json`
  as an earlier Gearbench wrote them, `config.v1.json`, `profiles.v1/` and
  `g432-eq.v1.json`, or `config.v2.json` and `profiles.v2/` for a file of
  version 2, made once just before each file's first save in this version's
  format. If the copy cannot
  be made, nothing is saved and the message gives the copy's path and why.
  `mouse-restore` takes the newest backup only if it is a regular file, or a
  link to one, of exactly the mouse's sector size, 255 bytes, whose CRC
  checks.
- `spatial.json`: Gearbench Spatial's head, room and headset picked, written by the window
  and `gearbench-ctl headset-spatial`, never by the daemon (see "Spatial
  sound").
- `g432-eq.json`: Logitech G432 Equalizer's presets, the shipped three and
  those saved as new, and the one that plays, written by the window and
  `gearbench-ctl g432-eq`, never by the daemon (see
  "The Logitech G432"). One that cannot be read, or is of another version,
  is never written over: the equalizer plays the shipped three, the
  Equalizer group says why, and the commands refuse each change, until you
  fix the file or move it away. One holding a preset saved as new that does
  not read, or more than 16, plays the others and says what it left out;
  nothing changes the file until you fix it.
- `a50-eq.json`: the A50's saved presets, written by the window,
  `gearbench-ctl headset-eq --save-as`, `headset-library` and
  `headset-import --library`, never by the daemon (see "Saved presets"). One
  that cannot be read, or is of another version, is never written over: the
  Load preset to headset dialog says so, and the commands refuse, until you
  fix the file or move it away. One holding a preset that does not read, or
  more than 16, lists the others, which still load, and says what it left
  out; nothing changes the file until you fix it.

This version keeps the lighting of `config.json` and of each game profile,
and the brightness, for each keyboard model, and the mouse bindings for each
mouse model. Settings an earlier Gearbench saved load as the G915's, and
their mouse bindings as the G502 X PLUS's. An earlier Gearbench cannot read
the files once this version has saved them: it starts on its defaults and
writes nothing over them. The exception is a build from before the G432's
presets, which plays `g432-eq.json` flat and writes over it at its next
equalizer change. To go back to one, copy each file in `backup/` over
the file it was copied from: `backup/config.v1.json` over `config.json`, each
file in `backup/profiles.v1/` over the file of that name in `profiles/`, and
`backup/g432-eq.v1.json` over `g432-eq.json`.

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
rename that would make a file larger than 1 MiB is refused, and the file
stays as it was. A save writes through a link to the file it names.

Where the service's sandbox applies, the only folders of yours the daemon
can write are `~/.config/gearbench` and `$XDG_RUNTIME_DIR`, so a save
through a link to a file in any other folder, in your home folder or outside
it, fails. With `XDG_CONFIG_HOME` set, the sandbox goes by the value
systemd's user manager started with. One set only in
`~/.config/environment.d`, or by the desktop session, reaches the daemon and
not the manager: the daemon logs an error at its start, and where the
sandbox applies nothing is saved. Set it for the manager too, with
`ManagerEnvironment=` in `~/.config/systemd/user.conf`, or leave it unset.

The A50's settings are in its base station, and the G432 keeps its sidetone
itself; of the headsets' settings, only the equalizer presets are kept here,
in `a50-eq.json` and `g432-eq.json`. The daemon's control socket is
`$XDG_RUNTIME_DIR/gearbench.sock`, which refuses a request over 1 MiB, and
its lock, which keeps a second daemon from starting, is
`$XDG_RUNTIME_DIR/gearbench.lock`. Gearbench Spatial's PipeWire
configuration, written each time its service starts the output, is
`$XDG_RUNTIME_DIR/gearbench-spatial.conf`. The daemon refuses a
`$XDG_RUNTIME_DIR` that another account owns or that other accounts may
write in, and so does switching the G432's equalizer on or off, or its noise
suppression on, since another account could put its own file in place of one
of Gearbench's there. The daemon also refuses one below, or reached through, a
folder or link that an account other than yours or root owns, and one below a
folder other accounts may write in without the sticky bit, since another
account could swap it for its own. The `/run/user/<uid>` your login gets is
none of these.

The headsets' audio files: `install.sh` writes
`~/.config/alsa-card-profile/mixer/profile-sets/gearbench-astro-a50.conf`,
`~/.config/wireplumber/wireplumber.conf.d/60-gearbench-astro-a50.conf` and
`60-gearbench-logitech-g432.conf` beside it, and
`~/.config/systemd/user/gearbench-spatial.service`; the package installs the
same under `/usr/share/alsa-card-profile/`, `/usr/share/wireplumber/` and
`/usr/lib/systemd/user/`, with the spatial data in
`/usr/share/gearbench/spatial`. When `XDG_CONFIG_HOME` is an absolute
path, `install.sh` writes these four files under it in place of
`~/.config`. It removes `gearbench-spatial-login.service`, which an install
from before Gearbench Spatial wrote.

The profiles and macros pack is read, never written, from `pack/` in a
checkout and from `/usr/share/gearbench/pack` when installed by the
package: `profiles/` and `macros/` there, in the same formats as yours,
and `notes.json`, the line shown under an item.

Every keyboard change and every mouse binding is saved as it is made, and a
color being dragged is saved at the release. Lighting and bindings go to the
loaded game profile's own file, or to the M profile in `config.json` when
none is loaded. The brightness, the saved colors, and the switches and times
of "Idle, lock and suspend" go to `config.json` whatever is loaded. The
mouse's stages and rate go to the loaded game profile's file on a mouse with
host mode and `mouse-host on`, and otherwise to its onboard profile 1. On a
mouse whose onboard profile Gearbench does not write yet, that write is
refused, and a rate set in host mode goes to `config.json` in its place. The
mouse's own color and effect are not stored. A change on the A50's page
waits for Save (see "Saving"), except to a saved preset, which goes to
`a50-eq.json` as it is made (see "Saved presets"). The G432's page has no
Save: a G432 change takes effect at once, its equalizer's presets go to
`g432-eq.json` as they change, and the adapter keeps its sidetone. In the
Spatial sound group, on either headset's page, the switch acts at once, and
Headset it plays to, Head and Room go to `spatial.json` as they are chosen.

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
holds, unless the daemon has been on its defaults since a start that could
not read the file: then `save` is refused.

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
that file may hold it. Once every file loads, a reload or a later start
loads it if a file holds its name or is the one named for it, and forgets
it if not.

A `lights_off_when_locked`, `mouse_sync_color`, `mouse_host_mode` or `tray`
changed in the file takes effect at a reload as `lock-lights`, `mouse-sync`,
`mouse-host` or `tray` would, and `idle_dim_after`, `idle_off_after` or
`never_dim` as `idle` would. A `sync_keyboard` changed in the file switches
to the keyboard it names, or with null to the one connected first, if that
one is plugged in and switched on beside another connected, as
`sync-keyboard` or a pick at
the top of the Keyboard tab does. Otherwise the mouse's onboard profile is
left alone. In host mode the loaded game profile's own DPI stages and rate follow
its file, and on a mouse whose onboard profile Gearbench does not write yet,
a `mouse_rate` changed in `config.json` is sent live unless the loaded game
profile has a rate of its own for that mouse.

`gearbench-ctl save` writes the daemon's copy of `config.json` and of the
loaded game profile over your edit instead. It does not write over a
`config.json` the daemon could not read at its start, which left it on the
defaults, and then saves neither file: fix `config.json` and reload. It
writes no macro, since the daemon keeps no copy of one: a macro's file is
saved over only after a reload.

## Install in detail

Gearbench is made for Ubuntu 26.04 with GNOME 50 on Wayland. It needs Python
3.14 and a kernel with uinput.

Install it one way, the Debian package or `install.sh` from a git checkout,
never both. The checkout's service file and launcher would shadow the
package's, so `install.sh` refuses while the package is installed. A udev
rule that a checkout install left in `/usr/local/lib/udev/rules.d/` takes
the place of the package's rule of the same name. The package warns about
each one and never deletes them. After `apt remove` they keep granting
access, and `./install.sh --uninstall` in that checkout removes them.

Run no other program that drives these devices alongside Gearbench: two
programs with one device open each read the other's replies. When the daemon
finds one it knows running, `gearbench-ctl status` and a banner in the window
say so and what to do (see "Banners and messages").

### The Debian package

Each release on the repository's
[Releases page](https://github.com/gearbench/gearbench/releases) has the
Debian package, `gearbench_X.Y.Z_all.deb`. Install it with apt, which pulls
in what it needs:

```
sudo apt install ./gearbench_*_all.deb
```

The package puts `gearbench-ctl` in `/usr/bin`, creates the `gearbench`
group and installs the udev rules (see "The udev rules"), applying them to
devices already plugged in. The user service starts at once in each desktop
session already open, and with each graphical login after that.

The package also carries the window's desktop entry and icons, the man page,
the README, this guide and the headsets' audio profiles. The audio profiles
list the A50's Game and Chat as two outputs and give the G432 one output
with its own volume. They are an ALSA profile set in
`/usr/share/alsa-card-profile/mixer/profile-sets/` and WirePlumber rules in
`/usr/share/wireplumber/wireplumber.conf.d/`. WirePlumber reads them when
your user manager next starts: at your next login once every session of
yours has ended or, with lingering on, when the machine next restarts.
`systemctl --user restart wireplumber` makes it read them at once, and stops
all sound for a moment (see "Game and Chat").

The spatial sound service, `gearbench-spatial.service`, with its data in
`/usr/share/gearbench/spatial` (see "Spatial sound"), and Gearbench's GNOME
Shell extension (step 7 of "First-time setup") stay off until you turn them
on.

To build the package yourself from a checkout, install `build-essential` and
the build dependencies `debian/control` lists, then run
`dpkg-buildpackage -us -uc -b`. It leaves the `.deb` in the folder above the
checkout. Install it from the checkout with
`sudo apt install ../gearbench_*_all.deb`.

### From a git checkout

`./install.sh` installs from the checkout. `./install.sh --uninstall` removes
all but your settings and the `gearbench` group, and the keyboard goes back
to its onboard profile.

```
./install.sh
```

Run it as yourself from a terminal in your desktop session, not with `sudo`;
it refuses to run as root. It creates the `gearbench` group, adds you to it
and installs the udev rules through sudo, asking for your password there.
The rest goes in as you:

- a user service that runs the checkout
- a `gearbench-ctl` launcher in `~/.local/bin`
- the icons
- the desktop entry, when GTK 4 and libadwaita import
- the headsets' audio profiles, under `~/.config/alsa-card-profile/` and
  `~/.config/wireplumber/`
- the spatial sound service, written and left off
- the GNOME Shell extension, turned on for your next login

`install.sh` starts the service. It restarts WirePlumber only when one of
the audio files is new or changed, which stops all sound for a moment. If
WirePlumber does not restart, the audio files apply when it next starts. Run
it again after moving the checkout.

Keep the checkout in a folder only you can write, such as your home folder:
the daemon runs the checkout's code as you. `install.sh` refuses a checkout
under `/tmp`, or anywhere else another account owns or can write, a write
through your own group aside.

### GTK and python3-gi

The window needs GTK 4 and libadwaita for Python (`python3-gi`,
`gir1.2-gtk-4.0`, `gir1.2-adw-1`) from the distribution, never from pip. The
daemon uses `python3-gi` too, for:

- exiting at the login screen, where the package's service also starts
- the screen lock, suspend and notices
- the tray icon
- a synced mouse's idle dimming
- the GNOME Shell extension's focus reports
- holding bindings and macros back while another account or a text console
  has the seat

Without it, the daemon runs at the login screen as well, and the others are
off. Game profiles then follow only X11 windows, a Proton game's among them
unless it runs Wine's Wayland driver. The journal says python3-gi is missing
when the service starts.

## The udev rules

Only the udev rules and the `gearbench` group need root. The daemon always
runs as you: manage its service with `systemctl --user`, never with `sudo`.
Both routes put the same rules in place, the package in
`/usr/lib/udev/rules.d/` and `install.sh` in `/usr/local/lib/udev/rules.d/`.

Some of the nodes the rules grant also carry what you type. With the rule
installed, any program you run can read your typing there; Gearbench drops
those reports unread. They are the G515 LIGHTSPEED TKL's node through its
receiver and the G815's control interface on its cable. On Linux 7.3 or
later, going by the kernel's source, the G915 TKL's node of its own behind
its receiver is one too. The control interface of the G502 HERO and of the
G502 X carries the keystrokes the mouse sends for a button its onboard
profile binds to a key. A program you run can read those the same way.
`70-gearbench-uinput.rules` lets any program you run create a virtual
keyboard.

These grants cross accounts. A program that opened `/dev/uinput` keeps its
keyboard after a user switch, or after a logout that leaves its processes
running. It can type into a later account's session. A program that opened a
node that carries keystrokes keeps reading it the same way, so it reads what
a later account types on that device. On a keyboard, that includes a
password typed at the login screen. On a machine several people use, the
group grant from the uinput rule's comment, put into a copy of each of these
rules in `/etc/udev/rules.d/`, limits these grants to the group's accounts.

Each rule and what it grants:

- `70-g915.rules`, `70-g502x.rules` and `70-g305.rules` let you open the
  control interface of the keyboard and of the cabled G502 X PLUS, and the
  node of each mouse behind its receiver. That node carries the mouse's
  motion and buttons too. The G502 X PLUS's event node, where the daemon
  reads G4 to G9 with host mode off, is readable by the `gearbench` group
  and writable by root alone. A program that could write it could move the
  pointer and click, and, through the receiver, type. No other node of the
  devices is granted. With host mode on, the default, G4 to G9 come in the
  mouse's button reports. The group is still needed for G4 to G9 whenever
  host mode does not take, and for the warning about a keyboard paired to
  the mouse's receiver (see "The mouse").

  Both routes create the group. `install.sh` adds you to it; with the
  package, run `sudo usermod -aG gearbench $USER`. The daemon gets the group
  at your next login once every session of yours has ended. With lingering
  on for your account (`loginctl show-user $USER -p Linger` says
  `Linger=yes`), it gets the group when the machine next restarts. Until
  then, with host mode off or not taken, `gearbench-ctl status` says what is
  missing.
- `70-g515.rules` lets you open the G515 TKL's control interface on its
  cable, interface 02 on the wired G515 TKL, read on the hardware. On the
  G515 LIGHTSPEED TKL's cable, which interface is the control one has not
  been checked. The rule's comment says how to check it and, if it is not
  02, how to take back the rule's grant of interface 02 with a copy in
  `/etc/udev/rules.d/`.
  Through its receiver, the rule grants the G515 LIGHTSPEED TKL's own node,
  which also carries what you type on the keyboard. With the rule installed,
  any program you run can read your typing on it; Gearbench drops those
  reports unread.
- `70-g915-tkl.rules` lets you open the G915 TKL's control interface on its
  cable and through its receiver. Which interfaces those are has not been
  checked on the hardware. The rule's comment says how to check them and, if
  one is not 02, how to take back the rule's grant of interface 02 there
  with a copy in `/etc/udev/rules.d/`. On a kernel that gives the keyboard a
  node of its own behind its receiver, Linux 7.3 or later going by the
  kernel's source, the rule grants that node. That node also carries what
  you type on the keyboard, so a program you run can read it.
- `70-g815.rules` lets you open the G815's control interface on its cable.
  That interface also carries what you type on the G815. With the rule
  installed, any program you run can read your typing on it; Gearbench drops
  those reports unread. Which of its interfaces is the control one has not
  been checked on the hardware.
- `70-g502hero.rules` lets you open the G502 HERO's control interface on its
  cable. That interface also carries the keystrokes the mouse sends for a
  button its onboard profile binds to a key, so a program you run can read
  those.
- `70-g502x-lightspeed.rules` lets you open the G502 X LIGHTSPEED's own node
  through its receiver, and its control interface on its cable, as the
  G502 X PLUS's rule does for that mouse. Which of its cable's interfaces is
  the control one has not been checked on the hardware.
- `70-pro-x-superlight.rules` does the same for the PRO X SUPERLIGHT, on the
  interface its firmware gives the control protocol.
- `70-g502x-wired.rules` lets you open the G502 X's control interface. As
  the G502 HERO's does, it carries the keystrokes of a button bound to a
  key, so a program you run can read those.
- `70-g502x-falcon.rules` does for the G502 X PLUS MILLENNIUM FALCON what
  `70-g502x-lightspeed.rules` does for its mouse. Which of its cable's
  interfaces is the control one has not been checked on the hardware.
- `70-g502-lightspeed.rules` lets you open the G502 LIGHTSPEED's own node
  through its receiver, and its control interface on its cable. Which of its
  cable's interfaces is the control one has not been checked on the hardware.
  Its receiver pairs one device, so no keyboard can share its node.
- `70-pro-wireless.rules` does the same for the PRO WIRELESS, on the
  interface its firmware gives the control protocol.
- `70-g903-hero.rules` does the same for the G903 LIGHTSPEED HERO, on the
  interface its firmware gives the control protocol.
- `70-astro-a50.rules` lets you open the A50 base station's one control
  interface, which carries its settings and no keystrokes. Its audio needs
  no rule.
- `70-g432.rules` lets you open the G432 adapter's one control interface,
  which carries its sidetone, its volume wheel and its mute, and no
  keystrokes. Its audio needs no rule.
- `70-gearbench-uinput.rules` lets you open `/dev/uinput`, where the daemon
  makes the virtual keyboard and mouse that bindings and macros type
  through. It is the line the steam-devices package ships. The rule's
  comment has the trade-off and, for a machine several people use, a group
  grant to use instead. The rule is in `/usr/lib/udev/rules.d/` with the
  package or `/usr/local/lib/udev/rules.d/` from a checkout. Without the
  rule, a bound key types nothing, and a G915 TKL keeps its own profile, so
  Gearbench drives none of its lighting or keys. The other devices' lighting
  works. `gearbench-ctl status` lists `no virtual keyboard` and the window
  shows a banner. The daemon tries again every 30 seconds, so a rule added
  later needs no restart of the service.

Removing the rules does not take away the access they gave (see "Upgrading
and removing").

## Upgrading and removing

A new `.deb` installs over the old one with `sudo apt install`, which
restarts a running service on the new code. From a checkout, `git pull` and
run `./install.sh` again, which restarts the service on the new code, or
starts it if it was stopped.

From 1.1.0 the app id is `io.github.gearbench.Gearbench` and the focus
extension `gearbench-focus@gearbench.github.io`, named after the gearbench
organization on GitHub, which holds the repository. After the upgrade from
1.0, set Gearbench's switch in Settings > Notifications again if you had
changed it, and log out and back in once, which starts the focus extension
under its new name. `./install.sh` removes the files left under the old
names, moves the dock's pin to the new app id, and turns the extension on
under its new name, whether it was on or off before. With the package the
old files go with the old package: pin Gearbench to the dock again, and
after that login turn the extension on with the window's Turn on. Until the
extension runs, game profiles follow only X11 windows.

If you had spatial sound on before 1.1.0, switch it off and on once after
the package's update, so it starts at each login again. `./install.sh`
removes the login service for spatial sound that an install from before
1.1.0 wrote, and keeps spatial sound on where it was on.

To remove the package, run `sudo apt purge gearbench` (or `remove`, which
keeps the service's enablement), then `systemctl --user restart wireplumber`.
The removal first stops the service and spatial sound of every account with
a running user manager, one kept by lingering included, which hands the
keyboard back. If you switched spatial sound on, run `systemctl --user
disable gearbench-spatial.service` before it, so no link to the unit stays
in your settings. From a checkout, `./install.sh --uninstall` does the same.
Neither touches your settings in `~/.config/gearbench/` (see "Settings files").

Removing the rules, by either route, does not take away the access they
gave: you keep it to the devices plugged in until they are replugged, and to
`/dev/uinput` until the machine restarts. The G502 X PLUS's event nodes go
back to `root:input` at once. The `gearbench` group stays, with you in it;
`sudo groupdel gearbench` removes it.

## The models and their ids

Gearbench attaches to the models below by their product ids, and to no
other. Several mice can be attached at once, and one keyboard is driven at a
time (see "One keyboard's lighting, another's"). The G915 and the G915 TKL
are not driven over Bluetooth, and the A50's base station only with its
switch on PC. The G305 has no lights and no buttons to bind.

| Device | Ids | How it was built |
| --- | --- | --- |
| G915 | 046d:c33e on its cable, its receiver 046d:c541 | on a G915 |
| G515 TKL | 046d:c358 on its cable | from Logitech's setup guide and published listings of its features, then tried on one on 2026-10-17 and 2026-10-18 |
| G515 LIGHTSPEED TKL | 046d:c355 on its cable, 046d:40b4 on receiver 046d:c547 | from the G515 TKL's sources |
| G915 TKL, G913 TKL | 046d:c343 on its cable; 046d:c545, or 046d:408e on receiver 046d:c545 where the kernel gives the keyboard a node of its own (see "The udev rules") | from its firmware, the key layout Logitech's G HUB draws for it and published listings of its features |
| G815, G813 | 046d:c33f on its cable | from published listings and logs of its features and its quick-start guide |
| G502 X PLUS | 046d:4099 on receiver 046d:c547, 046d:c095 on its cable | on a G502 X PLUS |
| G305 | 046d:4074 on receiver 046d:c53f | on a G305 |
| G502 HERO | 046d:c08b on its cable | from published logs of the mouse, then tried on a G502 SE HERO on 2026-10-17 and, its lighting included, on 2026-10-18 |
| G502 X LIGHTSPEED | 046d:409f on receiver 046d:c547, 046d:c098 on its cable | from G HUB's description of the model and a published listing of its features |
| PRO X SUPERLIGHT | 046d:4093 on receiver 046d:c547, 046d:c094 on its cable | from its firmware, a published listing of its features and G HUB's description of it |
| G502 X | 046d:c099 on its cable | from a published log of one unit and published listings of its features |
| G502 X PLUS MILLENNIUM FALCON | 046d:40b2 on receiver 046d:c547 (assumed), 046d:c09e on its cable | from G HUB's description of it, which matches the G502 X PLUS's; no published source lists its features, so all of them are assumed |
| G502 LIGHTSPEED | 046d:407f on receiver 046d:c539, 046d:c08d on its cable | from G HUB's description of it and published logs of the mouse |
| PRO WIRELESS | 046d:4079 on receiver 046d:c539, 046d:c088 on its cable | from its firmware, a published log of the mouse and G HUB's description of it |
| G903 LIGHTSPEED HERO | 046d:4087 on receiver 046d:c539, 046d:c091 on its cable | from its firmware, a published log of the mouse and G HUB's description of it; it is not the older G903, which Gearbench does not match |
| Astro A50 Gen 4 | 9886:002c, its base station | on an A50 |
| Logitech G432 | 046d:0a9c, its USB adapter | from what others report of the headset, then tried on one on 2026-10-17 and 2026-10-18 |

The G915, the G502 X PLUS and the G305, which Gearbench was built on, and the
wired G515 TKL have run on the hardware. Three others have run in part:

- The G502 HERO: its DPI, its lighting, and the report rate and onboard
  profile 1 as the mouse holds them. G4 to G9 in host mode and the writes
  to onboard profile 1 are built and not yet checked.
- The Astro A50 Gen 4: the battery, the microphone, the three EQ presets, the
  base station's settings, Game and Chat as two outputs, and Gearbench
  Spatial, its 7.1 output for games. The library of your own EQ presets to
  load into the three, and Reset to default for each of the three, were
  checked once. The stream port mix, the library's Load preset to headset…
  dialog, the low-battery notices and Auto shut-off's idle wait are built and
  not yet checked.
- The Logitech G432: the sidetone; one analog output with the adapter's own
  volume; Logitech G432 Equalizer, an output that holds that volume at 0 dB
  while it runs, after a replug too, carries the volume and the default
  output across as it goes on and off, and plays its three presets or an A50
  preset copied or imported onto its ten bands; Gearbench Spatial, through
  the equalizer too; and noise suppression for its microphone, heard on it.
  Noise suppression in a call, what the Spatial sound group says while
  Gearbench Spatial plays, and the equalizer's switch-on waiting for
  Gearbench Spatial to move onto it are built and not yet checked.

The other ten have not run on one yet. Until one has, `gearbench-ctl status`,
`gearbench-ctl mouse`, the journal and its tab ask for a device report from
it (see "Reporting a device"). On each mouse among them, Gearbench writes
nothing to the mouse's onboard memory until that write has been tested on
the hardware (see "Host mode"), and it writes nothing to the onboard memory
of the G915 TKL or the G815. It writes the G502 HERO's for that test (see
"The G502 HERO").

![A PRO X SUPERLIGHT's group on the Mouse tab, with the row Not yet checked on this model and its Device report button](images/untested-row.png)

Each model's own section says what it does and lacks: "The keyboard" for the
five keyboards, "The G502 HERO", "The G502 X LIGHTSPEED", "The PRO X
SUPERLIGHT", "The G502 X", "The G502 X PLUS MILLENNIUM FALCON", "The G502
LIGHTSPEED", "The PRO WIRELESS" and "The G903 LIGHTSPEED HERO" under "Mouse
models", and "The headset" with "The Logitech G432".

## Troubleshooting

About Gearbench, in the window's main menu, has a Troubleshooting page. Its
Debugging Information page gathers these, ready to copy or save:

- the version
- the daemon's status and problems
- whether you may open each device's node and `/dev/uinput`
- the state of spatial sound
- the daemon's last 30 journal lines

From a terminal, start with `gearbench-ctl status` and
`journalctl --user -u gearbench`. `-u` also shows any line another program
of yours logs under the service's name. The Debugging Information page
leaves those out: its journal section shows only the daemon's lines and
systemd's about it. RECOVERY in `man gearbench-ctl` covers a mouse write or
restore that failed, and a keyboard or headset left in a bad state.

Report a problem at https://github.com/gearbench/gearbench/issues, with the
Debugging Information page's text. The project takes issues, not pull
requests. For a device Gearbench does not drive yet, or one that misbehaves,
send a device report (see "Reporting a device"). Report a security problem
privately instead, as [SECURITY.md](../SECURITY.md) says.

## Why and how we built it

The vendor apps for these devices run on Windows, some on macOS too, and none
on Linux. Linux is our daily system, games under Steam and Proton included,
and we wanted everything this hardware can do without booting Windows to set
it. The Linux tools that exist each cover part of that, one device family or
one feature at a time, and two programs with one device open each read the
other's replies. So Gearbench drives every device it supports, and it has to
be the only program that does.

Four kinds of work went into it. First, published research: we researched
other open-source code for the keyboard's and mice's protocol and for the
headset's, and copied no source code from it. Second, our own captures of the
USB traffic of Logitech's G HUB driving the keyboard and mice on Windows.
Third, reverse engineering: G HUB's own files, two builds of Astro's control
app, and the firmware of the G915, the G915 TKL, four of the mice and the base
station answered what the captures and the research could not. Last, testing
on the wire: what Gearbench does with a device it has run on rests on requests
sent to it and its replies measured; the README's
[Supported devices](../README.md#supported-devices) and "The models and
their ids" say which devices and features are built but not yet checked on
the hardware.
