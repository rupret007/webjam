# Installed Mac Join recovery check

This small check drives the ordinary installed WebJam launch dialog through
macOS Accessibility. It does not launch the app, change permissions, press Host,
use a valid invitation, start playback, access the clipboard or record audio.
It cannot qualify Music, Shared Track, Webex, hearing or relay/account readiness.

Open the verified candidate normally and leave it at **Host / Join**. Run on
that Mac, substituting the actual existing PID and full candidate build SHA:

```sh
python3 tools/check_native_join_recovery.py \
  --pid 12345 \
  --expected-source FULL_40_CHARACTER_SOURCE_SHA \
  --output /tmp/webjam-native-join-attempt-1.json
```

The command verifies the PID's actual executable, bundle build ID and strict
code signature. It refuses another audio process, an unexpected screen, load
at least 10, swap at least 80%, or root free space below 15 GiB. No guard override
is provided. Accessibility must already be granted to the actual local/remote
caller. No Screen Recording permission is needed.

It checks masked malformed input, actionable refusal, normal Back navigation,
clearing the old input, the restored launch screen and absence of Jamulus.
The output file is exclusive and owner-only. Keep failed outputs; inspect the
same application state before deciding whether a changed test warrants another
attempt. A failed check only tries Back when still on a recognized Join surface;
it never quits or restarts an unexpected workspace.

Two details matter when extending this:

- A DNS-shaped dummy is a valid legacy endpoint, so it may begin an actual
  audio connection. The fixed test text contains spaces and is rejected by the
  production invitation parser; regressions exercise that real parser.
- The invitation field intentionally exposes bullets to Accessibility. Do not
  disable masking or expect plaintext. Nested Qt instruction labels require a
  buffered traversal of the actual AX contents, not window-level text queries.

Use a different, explicitly authorized procedure for valid invitations or media.
Source tests, this installed-GUI result, provider activation and human hearing
must stay separate in readiness reports.
