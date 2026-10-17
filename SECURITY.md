# Security

Report a security problem privately, not in a public issue: on the
repository's Security tab, choose Report a vulnerability. Only you and the
maintainer see the report until a fix is out.

A security problem is anything that lets another program or another account:

- open the keyboard, a mouse, the headset or `/dev/uinput` through the udev
  rules Gearbench installs, where it should not;
- send a request to the Gearbench service, or read its replies;
- have Gearbench write a file outside its own settings folder, or run a
  command;
- have Gearbench send a device something it would not send otherwise.

Say which version you run (`gearbench-ctl --version`), what you did and what
happened. The fix comes in a new release.
