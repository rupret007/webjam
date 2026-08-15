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
