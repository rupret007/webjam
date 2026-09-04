# Support and troubleshooting

Start with the [creator guide](USER_GUIDE.md), [quick help map](QUICK_HELP_MAP.md),
and [test procedure](TEST_PROCEDURE.md). WebJam deliberately leaves live audio
configuration to Jamulus and meeting state to the selected meeting service.
Conversation can hand off any public HTTPS DNS-host meeting link that passes
WebJam's safety checks; known services receive friendly labels and another
accepted provider remains neutral. This does not verify that provider or the
meeting state.

## Before opening an issue

Record:

- WebJam version, exact package filename, and SHA-256;
- operating system, architecture, and whether the app came from source or a
  GitHub release;
- Jamulus version and whether it is embedded, managed, or system-installed;
- the shortest reproduction and expected result;
- the relevant privacy-safe diagnostic summary or redacted log tail.

Never paste passwords, meeting links, invitations, tokens, private keys, raw
support bundles, private paths, device identifiers, or unredacted logs.
For an unknown meeting provider, WebJam removes both the full URL and hostname
from its support projection; do not add either back to an issue manually.

## Where an issue belongs

- WebJam session lifecycle, recording, Studio, Reference Track, Pocket Stage,
  privacy, packaging, or updater: open a WebJam issue with the template.
- Jamulus devices, buffers, channels, server connectivity, or the Jamulus
  client itself: consult [Jamulus help](https://jamulus.io/wiki/Getting-Started).
- Meeting login, admission, camera, microphone, participants, mute, or state:
  consult the selected service's support. For Webex, use
  [Webex help](https://help.webex.com/).

If a physical gate is not yet tested, report it as **NOT RUN** rather than
assuming that a source test or moving meter proves audibility.

## When Join needs attention

A remote Join must move through **Checking invite**, **Contacting host**,
**Securing connection**, and **Opening Jamulus**, or stop at **Needs
attention**. It does not have an unbounded “trying to connect” state.

- **Try Again** means the failure occurred before WebJam submitted the
  one-use invitation.
- **Paste New Invite** means the invitation expired, timed out, or may already
  have been consumed. Ask the host to create a new invitation.
- A fresh Mac may separately leave Jamulus audio setup open for the person to
  choose the interface, channels, headphones, and buffer. That human setup is
  not proof of connection.

If the bounded recovery repeats, record only the visible state, safe error
category, WebJam/Jamulus versions, platform, and whether the host was still
available. Never include either invitation, its QR code, a meeting link, raw
addresses, credentials, private paths, or unredacted logs. Jamulus band chat
starts only after Jamulus is connected and therefore cannot be used to recover
the connection that carries it.
