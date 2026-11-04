# Security

Gearbench runs as you and sends nothing over the network. Only the udev
rules, the mice's hardware database entry and the `gearbench` group need
root. The user guide's [The udev rules](docs/GUIDE.md#the-udev-rules) says
what each rule grants and which of the nodes it grants also carry what
you type. It also says how to limit the rules to one group's accounts on a
computer several people use.

Report a security problem privately, not in a public issue: on the
repository's [Security tab](https://github.com/gearbench/gearbench/security),
choose Report a vulnerability. Give your version
(`gearbench-ctl --version`), what you did and what happened. Only you and
the maintainer see the report until a new release fixes it.

A security problem is anything that lets another program or another account:

- open a keyboard, a mouse, a headset or `/dev/uinput` through the udev
  rules Gearbench installs, beyond what each rule grants according to the
  user guide's [The udev rules](docs/GUIDE.md#the-udev-rules);
- send a request to the Gearbench daemon, or read its replies, from
  another account;
- have Gearbench write a file or run a command it would not otherwise;
- have Gearbench send a device something it would not send otherwise.

Supported versions: the latest release.
