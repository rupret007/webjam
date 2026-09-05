# Session help — development preview and handoff

This is source-only, default-off work after transport #66, based on exact
`master` `a004bbbce20a8ced3b67f7ec89798e0a9416f208`, branch
`codex/webjam-room-help-ui-20260904`. It does not update the installed app,
immutable unsigned v0.27.2 release, tag, component catalog or signing policy.

## What a person can do

Within the existing owner-approved reference-local lab, the explicit
`WEBJAM_ENABLE_REFERENCE_LOCAL=1` opt-in exposes one direct **Help** button on
the session controls. It opens a non-modal **Session help · Preview** panel,
so the person can keep working through setup. No new menu hierarchy or
startup choice is added. The flag is not saved to settings. Offline Reference
Studio excludes the preview even when that flag is present.

At the 720-pixel session-window minimum, only the enabled preview tightens
control spacing; the recording receipt and existing actions remain readable.
Turning the preview off restores the original density. The help dialog fits
at 300 pixels wide, and Enter has one submission path rather than also
activating a Qt auto-default button.

After the private host/guest connection proves the peer, the person can send
one-line plain-text setup questions without waiting for Jamulus to be ready.
This does **not** help before secure enrollment succeeds and is not offline
messaging, meeting-number lookup, PIN joining or a public chat service.
Regular Jamulus band chat remains unchanged and separately owned.

Each message is at most 500 UTF-8 bytes and uses the existing protocol's NFC,
plain-text validation. There are no attachments or markup. A successful local
send is **Accepted locally**. Only a matching authenticated peer receipt can
change it to **Peer acknowledged — not a read receipt**. No status means a
person saw or understood the message.

An uncertain send keeps its draft for the same session and explains that
retrying may send twice. There is no automatic resend. A new peer, Reset
Invite, leave, failure or shutdown clears messages and drafts, including
Undo history. A dead sidecar disables help independently of audio readiness.

## Implementation and privacy

- `webjam_qt/widgets/room_help.py`: bounded plain-text view, separate from the
  notes canvas, exports, diagnostics and support bundles.
- `webjam_qt/controllers/room_help.py`: one bounded send worker, 16 early/queued
  frames, one scheduled UI drain, 40 display entries and 8 early receipt IDs.
  Replaced sources and old generations cannot publish into the new session.
- `services/native_remote_transport.py` and `remote_session_runtime.py`:
  current help availability and expected-generation dispatch fencing. A
  generation is checked before text is sent, not only after a late result.
- `ApplicationController`: source-bound callbacks, explicit development gate,
  first-message staging, 500ms liveness checks and session cleanup. It does
  not infer peer proof from the Help button, Jamulus roster or audio meters.

Early messages may arrive before Qt processes its CONNECTED snapshot. An
explicitly armed source can stage only the bounded canonical frames; nothing
is displayed or send-enabled until exact live source/role/generation proof.
Old queued reset/failure snapshots cannot erase a newer generation's content.

No message is appended to saved notes, diagnostic timelines, public API,
notifications, analytics, support bundles or an offline queue. Memory dumps,
a compromised endpoint and a user manually copying visible text are outside
this ephemeral-storage boundary. The authenticated peer can send objectionable
plain text; this is not content moderation.

## Verification and remaining gates

Focused coverage executes the real Qt app/widget/presenter against synthetic
transport owners, including help before Jamulus starts, ACK-before-local-
acceptance, first-message staging, Reset Invite, old-source delivery, bounded
queues/workers, sender-generation races, failed-send retention and Qt Undo
privacy. Existing native protocol/reference-relay tests remain the transport
evidence.

Verified locally on 2026-09-04, after the final layout and keyboard fixes:

- Complete process-per-file pytest suite: **314 modules, 6,764 passed,
  97 subtests passed, 25 skipped**, exit 0. Nothing was retried or deselected
  in the final run. Platform/live gates remain skipped, not newly certified.
- Focused presenter/widget/app/layout combination: **70 passed**. The eight
  actual-themed layout/keyboard cases cover active Music, recording cleanup
  and completion receipts, Art, 720-pixel fit, 900-pixel density transitions,
  original-layout restoration, 300-pixel dialog fit and one Enter submission.
- Ruff for source and all touched/new tests, compileall, `pip check`,
  `tools/runtime_dependency_policy.py --check`, `git diff --check` and
  `ux_smoke_test.py`: **PASS**.
- Offline Qt-rendered 720-pixel Music/Art and 300/380-pixel help-dialog views
  inspected. This is synthetic layout evidence, not a live room or feel test.

The full-suite command uses the existing virtual environment, offscreen Qt
and `WEBJAM_ENABLE_REFERENCE_LOCAL=0`, then runs each `tests/test_*.py` file in
its own Python process. Individual preview fixtures opt in under test.
GitHub's exact-head run remains authoritative for hosted checks and desktop
builds; this local report does not claim a packaged or release result.

Still **NOT RUN**: packaged two-Mac interaction, physical accessibility/feel,
sleep/network interruption on actual hosts, long-session behavior with real
audio, public-service privacy/abuse/operations approval, default-product
enablement, signing/notarization and production deployment. No public service
was started, no real invitation was used and no message was sent to a person
during this source session.

For Bob/Grok/Claude: do not redo #66 or replace this with a second chat stack.
Review this draft for Karen leftover + security, then seek the exact owner
gate for physical validation/default enablement. Leave parked #37/#49 and the
immutable release alone. Do not infer a meeting-code/PIN decision from this
preview; that remains in the separate architecture plan.
