# Pre-Karen QA — Art room clarity and continuity

2026-09-05 CT. Branch `codex/webjam-art-room-clarity`; exact base
`c18e0b9ac039e8e99d3a5fa19305c155de3b160e` (post #70).
Coord BEFORE: [Bob #3](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5554058853).

The Art room now has its own useful body, driven by current connection and
existing activity evidence. The door remains two Art choices followed by
Host/Join; Music keeps its mixer. This is one entry → room → recovery → exit
slice, including the cold-bootstrap persistence and native retry defects found
in the Worth-Building gate.

| Product claim challenged | Direct verification |
| --- | --- |
| A room invitation is not a connected artist | Host waiting and confirmed connection have distinct room context. Art does not use the Music roster or invent artist names/counts. An own-tools room is complete without canvas or video setup. |
| Current activity has a useful route | Existing canvas/Paint along panels are offered from their public room projection; queued actions revalidate current room and target. Conversation only reveals the existing meeting controls. Nothing joins a meeting or launches a canvas automatically. |
| Leave preserves personal work | Cold LAN/native entry across all four saved profiles, warm native entry, and cancellation before host-profile discovery preserve personal profile/activity/title/notes. Explicit title edits persist. Failed cleanup retains borrowed context until successful retry. |
| Recovery can actually succeed | Native guest/host failure → safe retry → Art connected opens a new conductor generation; duplicate commands retain one pending attempt and old-source/generation callbacks cannot overwrite the recovered room. |
| End and Quit stay truthful | Current room facts supersede the previous clean End latch on restart. Pending cleanup outranks connection facts, and failed Quit keeps its retained owner visible with Quit-again wording. Completed and quitting guests keep the captured guest role. |
| Compact rooms remain usable | 50 native-widget cases include production copy at 720×560 and 1440×900, with and without Conversation, phase/role changes, keyboard access, and Music/profile switching. Native Cocoa renders of both doors and seven room states were inspected. Focused actions scroll into view in short panes; fixture teardown proves each test window is destroyed. |
| Art stays Preview; Music stays evidence based | #70 labels remain in the door/status surfaces. Art stage switching leaves Music participant cards intact. No new roster protocol, audio readiness claim, video stack, or public invitation service was added. |

**Product self-QA PASS.** Focused proof: **40 model/controller/invitation/retry tests and 50 UI tests
passed**. Adjacent native controller/recovery/transport tests passed (49);
launch/door/persistence/ingress tests passed (111 and two subtests). Ruff,
compileall, pip check, UX smoke, and whitespace checks pass. The final raw
local suite passed: **7,078 tests, 26 skipped, 3 warnings, 99 subtests** in
284.69 seconds. The immutable hosted run/job results are recorded in the open draft
against its exact tip; hosted SUCCESS and all four Build Desktop jobs are
mandatory before handoff.

Self-review found and fixed the End→restart latch, pre-profile borrowed-title
restore, failed-Quit projection, and compact focus visibility defects. No
assertion was removed or weakened, and no new skip or test-run exclusion was
introduced. The first raw full run exposed a missing optional shutdown
attribute in a legacy controller harness, then exhausted local disk space and
cascaded file-creation failures. The controller keeps that harness compatible;
only a regenerable virtual environment from an earlier temporary WebJam build
was removed to recover space. Its built app and the failed-run log were kept.
After those corrections, 101 focused UI, lifecycle, invitation, and shutdown
tests passed. The next full run reached 7,077 passes with one Studio
callback test race: promotion woke the producer while the test expected an
uncontended buffer read. The test now parks that future producer work outside
its queue lock, retaining every sample, deadline, and no-file-I/O assertion.
All 41 renderer tests pass; Studio product code is unchanged. Native renders use synthetic connection facts to inspect real Qt
widgets; they are not physical multi-computer evidence.

**Holds / NOT RUN:** physical audio/output and external-device/editor checks;
public rendezvous; live meeting-provider calls; installed unsigned v0.27.2
click/feel; signing, notarization, tags, release, Pages, and Release Trust.
Unsigned 0.27.2 remains Jeff-only. No merge. #37/#49 remain parked; #67 and its
branch remain untouched. Existing stashes and local work remain preserved.
