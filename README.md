<div align="center">

![Gearbench's icon](desktop/icons/scalable/io.github.gearbench.Gearbench.svg)

# Gearbench

</div>

Gearbench is an open-source Linux app for Logitech G keyboards, mice and
headsets. The keyboards and mice get per-key lighting, G keys, macros, DPI
stages and game profiles; the headsets get equalizers and spatial sound. It
imports your G HUB profiles, bindings and macros from a copy of G HUB's
settings file.

It is made for Ubuntu 26.04 with GNOME 50 on Wayland. It is
[physically tested](docs/GUIDE.md#the-models-and-their-ids) on the G915,
G515 TKL and G915 X LIGHTSPEED keyboards, the G502 X PLUS, G502 HERO and G305
mice, and the Astro A50 Gen 4, Astro A50 X, G432 and G933 headsets.
[Forty more models](#supported-devices) are built and wait for their first
physical test.

<div align="center">

**[Install](#install)** ·
[Download](https://github.com/gearbench/gearbench/releases/latest) ·
[Supported devices](#supported-devices) · [User guide](docs/GUIDE.md) ·
[Moving from G HUB](docs/GUIDE.md#moving-from-g-hub)

![The Keyboard tab: a G915 painted key by key](docs/images/hero.png)

[Keyboard](docs/GUIDE.md#the-keyboard): keys painted by click or drag, G keys and M profiles

| ![The Mouse tab: a G502 X PLUS's DPI, report rate and DPI stages](docs/images/gallery-mouse.png) | ![The Headset tab: the Astro A50 Gen 4's microphone and equalizer](docs/images/gallery-headset.png) |
| :---: | :---: |
| [Mouse](docs/GUIDE.md#the-mouse): DPI stages and the report rate | [Headset](docs/GUIDE.md#the-headset): the A50 Gen 4's microphone, sidetone and equalizer presets |
| ![The Macros tab: a macro of keys, a click and a line of text](docs/images/gallery-macros.png) | ![The Game profiles dialog: a profile that loads for PioneerGame.exe](docs/images/gallery-game-profiles.png) |
| [Macros](docs/GUIDE.md#macros): keys, text, clicks and delays, added or recorded | [Game profiles](docs/GUIDE.md#game-profiles): lighting and bindings that load while their game has focus |

</div>

## Supported devices

Gearbench attaches to these models by their product ids, and to no other.
It drives no device over Bluetooth. Several mice and several keyboards can be
attached at once. One keyboard leads: the others show its lighting unless
set to keep their own, and a mouse's or a headset's lights can follow it.

Physically tested says Yes for a model Gearbench has run on, and the row
names what is not tested on it yet. The models marked No were built without
one on hand, from published sources, G HUB's files and, for some, their
firmware.

Gearbench writes nothing to the onboard memory of the mice marked No, the
G915 TKL, the G815 or the PRO X TKL LIGHTSPEED. Before each write to a
mouse's onboard profile 1, it backs up the mouse's onboard profiles. Restore
profile backup… on the Mouse tab writes the newest backup back
([The onboard profile and its backups](docs/GUIDE.md#the-onboard-profile-and-its-backups)).
It reads the A50 Gen 5, PRO X 2 LIGHTSPEED, G522 LIGHTSPEED and G325
LIGHTSPEED but changes nothing on them until each change is tested. On the
mice marked No, it drives the buttons and sets the report rate live, in
[host mode](docs/GUIDE.md#host-mode), the default.

Until a model is physically tested, its tab, the journal and the
`gearbench-ctl` command ask for a device report from it.
[Your device isn't supported yet?](#your-device-isnt-supported-yet) says how
to make one. [The models and their ids](docs/GUIDE.md#the-models-and-their-ids)
lists each model's ids, what it was built from and what is not tested on it
yet.

### Keyboards

| Device | Physically tested | What Gearbench drives |
| --- | --- | --- |
| G915 (cable or receiver) | Yes | G1 to G5, M1 to M3, MR, per-key lighting, animations, brightness, battery |
| G515 TKL (cable) | Yes | per-key lighting, animations, brightness, FN keys lit while FN is held, Game Mode's state |
| G515 LIGHTSPEED TKL (cable or receiver) | No | per-key lighting, animations, brightness, FN keys lit while FN is held, battery, Game Mode's state |
| G915 TKL and G913 TKL (cable or receiver) | No | F1 to F12 as G keys, M1 to M3, MR, per-key lighting, animations, brightness, battery |
| G815 and G813 (cable) | No | G1 to G5, M1 to M3, MR, per-key lighting, animations, brightness |
| PRO X TKL LIGHTSPEED (cable or receiver) | No | F1 to F12 as G keys, per-key lighting, animations, brightness, battery |
| G915 X (cable) | No | per-key lighting, animations, brightness, FN keys lit while FN is held, Game Mode's state; G1 to G9 bind once tested on the model |
| G915 X LIGHTSPEED (cable or receiver) | Yes | G1 to G9, per-key lighting, animations, brightness, FN keys lit while FN is held, battery, Game Mode's state |
| G915 X LIGHTSPEED TKL (cable or receiver) | No | per-key lighting, animations, brightness, FN keys lit while FN is held, battery, Game Mode's state; F1 to F12 bind once tested on the model |
| G316 X and G316 X 98 (cable) | No | per-key lighting, animations, brightness, FN keys lit while FN is held, Game Mode's state; F1 to F12 bind once tested on the model; the light bar is not drawn on the tab |
| G512 X 75 and G512 X 98 (cable) | No | per-key lighting, animations, brightness, FN keys lit while FN is held, Game Mode's state; F1 to F12 bind once tested on the model; the analog keys are not set yet, and the light bar is not drawn on the tab |
| G213 Prodigy (cable) | No | lighting in five areas, a color each, animations |

### Mice

| Device | Physically tested | What Gearbench drives |
| --- | --- | --- |
| G502 X PLUS (cable or receiver) | Yes | G4 to G9, DPI stages, report rate, lighting, battery |
| G305 (receiver) | Yes | DPI stages and its DPI button, report rate, a coarse battery level |
| G502 HERO (cable) | Yes | G4 to G9, DPI stages, report rate, lighting; not yet tested: G4 to G9, and every write to its onboard memory except the DPI stages, the report rate included |
| G502 X LIGHTSPEED (cable or receiver) | No | G4 to G9, DPI, report rate, battery |
| PRO X SUPERLIGHT (cable or receiver) | No | G4 and G5, DPI, report rate, battery |
| G502 X (cable) | No | G4 to G9, DPI, report rate |
| G502 X PLUS MILLENNIUM FALCON (cable or receiver) | No | G4 to G9, DPI, report rate, lighting, battery |
| G502 LIGHTSPEED (cable or receiver) | No | G4 to G9, DPI, report rate, lighting, battery |
| PRO WIRELESS (cable or receiver) | No | G4 to G8, DPI, report rate, lighting, battery |
| G903 LIGHTSPEED HERO (cable or receiver) | No | G4 to G9, DPI, report rate, lighting, battery |
| PRO X SUPERLIGHT 2 and PRO X SUPERLIGHT 2 SE (cable or receiver) | No | G4 and G5, DPI and report rate in host mode alone, battery |
| PRO X SUPERLIGHT 2 DEX (cable or receiver) | No | G4 and G5, DPI and report rate in host mode alone, battery |
| PRO X SUPERLIGHT 2c (cable or receiver) | No | G4 and G5, DPI and report rate in host mode alone, battery |
| PRO X2 SUPERSTRIKE (cable or receiver) | No | G4 and G5, DPI and report rate in host mode alone, battery; its analog button settings are not set yet |
| G203 LIGHTSYNC and G102 LIGHTSYNC (cable) | No | G4 to G6, DPI, report rate, lighting |

### Headsets

Each headset's page in the Headset tab also sets its microphone's volume, the
level GNOME's Sound settings show for it.

Ten of the headsets are the G432's relatives: the G431, G433, G533, G535,
G633, G635, G733, G933, G935 and PRO X Wireless. Of the settings these
headsets keep, Gearbench writes the sidetone. On the G933, the one of them
physically tested, it also writes its auto shut-off time, its startup effect
and the lighting it shows on its own.

| Device | Physically tested | What Gearbench drives |
| --- | --- | --- |
| Astro A50 Gen 4 (base station on USB, its switch on PC) | Yes | microphone, equalizer presets, base station settings, Game and Chat outputs and their volume, spatial sound, battery; some features not yet tested |
| G432 (USB adapter) | Yes | sidetone, a ten-band equalizer, microphone noise suppression, spatial sound; some features not yet tested |
| G431 and G433 (USB adapter) | No | sidetone, a ten-band equalizer, microphone noise suppression, spatial sound |
| G633 and G635 (cable) | No | sidetone, lights, the G633's equalizer in the headset and its presets, the G635's ten-band equalizer, G1 to G3, microphone noise suppression, spatial sound |
| G533 (receiver) | No | sidetone, battery, a ten-band equalizer, microphone noise suppression, spatial sound |
| G535 (receiver) | No | sidetone, battery, a ten-band equalizer, microphone noise suppression, spatial sound |
| G733 (receiver) | No | sidetone, battery, lights, a ten-band equalizer, microphone noise suppression, spatial sound |
| G933 (receiver) | Yes | sidetone, battery, lights, the equalizer in the headset and its presets, G1 to G3, microphone noise suppression, spatial sound; its lights following the keyboard not yet tested |
| G935 (receiver) | No | sidetone, battery, lights, the equalizer in the headset and its presets, G1 to G3, microphone noise suppression, spatial sound |
| PRO X Wireless (receiver) | No | sidetone, battery, a ten-band equalizer, microphone noise suppression, spatial sound |
| Astro A50 X (base station on USB) | Yes | battery, sidetone, volume, Game and Voice balance, noise gate, auto sleep, tones, the stream mix, the microphone's boom, the base station's deep sleep and brightness, and the headset's two equalizers, their presets and presets of your own; Game and Chat outputs and their volume, spatial sound; some features not yet tested |
| Astro A50 Gen 5 (base station on USB) | No | read-only until tested: battery, sidetone, volume, Game and Voice balance, noise gate, auto sleep, tones, the stream mix, the microphone's boom, the base station's brightness and the headset's two equalizers; spatial sound |
| PRO X 2 LIGHTSPEED (receiver) | No | read-only until tested: battery, sidetone, auto sleep, microphone noise suppression and the equalizer in the headset; spatial sound |
| G522 LIGHTSPEED (receiver or cable) | No | read-only until tested: battery, sidetone, auto sleep, eco mode and the equalizer in the headset; spatial sound |
| G325 LIGHTSPEED (receiver) | No | read-only until tested: battery, sidetone, auto sleep, eco mode, microphone noise suppression and the equalizer in the headset; spatial sound |

[Why and how we built it](docs/GUIDE.md#why-and-how-we-built-it) says how
Gearbench was built and tested.

## Install

Gearbench is made for Ubuntu 26.04 with GNOME 50 on Wayland. It needs Python
3.14 and a kernel with uinput. Other distributions and desktops are not
tested. [Install in detail](docs/GUIDE.md#install-in-detail) says what changes on
another desktop. Install it one of two ways, the package or a git checkout,
never both. Run no other program that drives these devices alongside
Gearbench: two programs with one device open each read the other's replies.
The daemon starts as soon as the install ends.

Both ways install udev rules that let any program you run open each
supported device and `/dev/uinput`, through which bindings and macros type.
They also add each supported mouse to the system's hardware database, so its
pointer moves at the DPI you set. The rules, that entry and the `gearbench`
group are all that needs root.

On the models below, a connection the rules open also carries what you type,
or the modifier keys you press, so a program you run can read them, another
account's included. Gearbench reads only FN and drops the rest unread.
Through `/dev/uinput`, a program you run can also type, even into a later
account's session. [The udev rules](docs/GUIDE.md#the-udev-rules) names
each such connection. For a computer several people use, it says how to
limit the rules to one group's accounts.

- A connection the rules open can also carry what you type on the G815 and
  G813, G515 LIGHTSPEED TKL, G915 TKL and G913 TKL, PRO X TKL LIGHTSPEED,
  G915 X LIGHTSPEED, G915 X LIGHTSPEED TKL and G213 Prodigy keyboards, the G502 HERO, G502 X, G203
  LIGHTSYNC and G102 LIGHTSYNC mice, and each mouse through its receiver.
- The control connection of the G515 TKL, the G515 LIGHTSPEED TKL on its
  cable and the G915 X, G316 X and G512 X models carries each press and
  release of Ctrl, Shift, Alt, Super and FN once a program asks the keyboard
  for them. Gearbench asks each time it sets the keyboard up. The keyboard keeps sending them until it loses power:
  switched off, or unplugged where it has no battery.

To install the package, download `gearbench_X.Y.Z_all.deb` from the
[Releases page](https://github.com/gearbench/gearbench/releases). Then run this
in a terminal in the folder you saved it to; apt pulls in what it needs:

```
sudo apt install ./gearbench_*_all.deb
```

To install from a git checkout, clone it into a folder only you can write,
such as your home folder: the daemon runs the checkout's code as you. The
window needs `python3-gi`, `gir1.2-gtk-4.0` and `gir1.2-adw-1` from the
distribution, never from pip (see
[GTK and python3-gi](docs/GUIDE.md#gtk-and-python3-gi)).
Run `install.sh` as yourself, not with `sudo`. It asks for your password to
install the udev rules, the mice's hardware database entry and the
`gearbench` group:

```
git clone https://github.com/gearbench/gearbench.git
cd gearbench
./install.sh
```

Either way, Gearbench's service, the daemon, starts at once in your desktop
session and at each graphical login after that. Then log out and back in
once: GNOME loads the Gearbench focus extension, which lets game profiles
follow every window, only at a login. After a package install, Turn on in
the window's banner then enables it. When the daemon recognizes
another program that drives these devices, `gearbench-ctl status` and a
banner in the window say so.

Next, follow [First-time setup](docs/GUIDE.md#first-time-setup):
`gearbench-ctl status` shows what the daemon found, the window opens from the
app grid and a key lights at a click.
[Install in detail](docs/GUIDE.md#install-in-detail) has what each way installs
and where, how to build the package and what the window needs.
[Upgrading and removing](docs/GUIDE.md#upgrading-and-removing) covers a new
version and taking Gearbench off. If something does not work,
[Troubleshooting](docs/GUIDE.md#troubleshooting) says what to gather and where
to report it.

## Your device isn't supported yet?

A device report is how support for a device gets built and fixed: it shows
what works and what to fix. Send one from a Logitech keyboard, mouse or
headset that [Supported devices](#supported-devices) leaves out or marks No,
or from a listed one that misbehaves.

While the Gearbench daemon runs, the report gives only what Gearbench already
knows of a device it drives. It reads no other device Gearbench supports,
since the daemon opens those as it looks for them. The report says how to
stop the daemon for the full read and start it again.

With Gearbench installed:

1. Plug the device in over USB.
2. Run this in a terminal:

   ```
   gearbench-ctl report
   ```

   It writes nothing to any device: it reads what each Logitech device on USB
   says about itself. The block it prints holds no serial number, no folder
   and no name for you, your computer or the unit. It holds nothing from the
   part of a mouse's memory that can hold your macros.
3. Paste the block into a
   [device report](https://github.com/gearbench/gearbench/issues/new?template=device-report.yml),
   with the model in the title. The form also asks how it was plugged in.

For a device no Gearbench rule lets your account open, the report prints a
`sudo setfacl` command. It lets your account open that one device until you
unplug it, and installs nothing. Run it, then run the report again. Some
devices send what is typed over that same connection, keyboards among them
and mice that send the keystrokes of a button bound to a key. For those, the
report warns that any program you run could read what is typed on the device
until you unplug it. Unplug it once the report is done; that ends the access.

[Reporting a device](docs/GUIDE.md#reporting-a-device) has what the report asks
each device and its `--json` form. Report a security problem privately
instead, as [SECURITY.md](SECURITY.md) says.

## License

GPL-3.0-or-later; the full text is in [LICENSE](LICENSE). The head
measurements in `spatial/sadie-ii/` are SADIE II's, from the University of
York, under Apache-2.0 ([spatial/sadie-ii/LICENSE.txt](spatial/sadie-ii/LICENSE.txt)).
[spatial/README.md](spatial/README.md) has their attribution and where they
came from. The AppStream metadata,
`desktop/io.github.gearbench.Gearbench.metainfo.xml`, is under CC0-1.0.

## Trademarks

Gearbench is an independent project, not affiliated with or endorsed by
Logitech. Logitech, Logitech G, LIGHTSPEED, G HUB and ASTRO are trademarks of
Logitech. Dolby is a trademark of Dolby Laboratories.
