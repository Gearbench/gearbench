# Security

Report a security problem privately, not in a public issue: on the
repository's Security tab, choose Report a vulnerability. Only you and the
maintainer see the report until a fix is out.

A security problem is anything that lets another program or another account:

- open a keyboard, a mouse, a headset or `/dev/uinput` through the udev
  rules Gearbench installs, beyond what the user guide's
  [The udev rules](docs/GUIDE.md#the-udev-rules) says each rule grants;
- send a request to the Gearbench service, or read its replies, from
  another account;
- have Gearbench write a file or run a command it would not otherwise;
- have Gearbench send a device something it would not send otherwise.

Supported versions: the latest release.

Say which version you run (`gearbench-ctl --version`), what you did and what
happened. The fix comes in a new release.
