# Manager demo PM2: readable Notes with one primary next action

This additional slice is stacked on OPEN DRAFT #147 at
`78a24d75bd1dc046c59452de59e9702e907851c4`, which includes #146. Art #145 stays
separate. Remote master was confirmed as `a51154e533ce5662ca4b541a866dd9b1954452b1`.
The [baseline audit](audit-baseline.md) explains why this is net-new work, with
no reopened Art or persistence scope.

## BEFORE / AFTER

| Reproduced state | Result | Proof |
| --- | --- | --- |
| Music Notes in the full 760×600 window clips NOW's next action at normal 13px; 22px also cuts tool labels. | The HUD remains the primary next-action surface. Notes starts with its tools and editor; **Session details** explicitly reveals existing NOW and Creative Pulse. | [Before 13px](before-music-13.png), [after 13px](after-music-13.png), [before 22px](before-music-22.png), [after 22px](after-music-22.png) |
| Healthy 280×560 Notes grows to 627px at the wider 22px font. | Full tool labels fit; Export moves to its own row when needed. The requested 280×560 geometry and writing area fit. | [Before](before-healthy-22.png), [after](after-healthy-22.png) |
| Long details exceeded their allocated labels. | Details scroll independently of the editor, with full measured label heights and keyboard Space/Tab/PageDown/Backtab access. | [Scrolled details](after-details-scrolled-22.png) |
| A trial version let an expanded details panel crowd save-failure text inside the actual small window. | Local recovery temporarily hides details, preserving the user's expanded choice for afterward. Permission and unreadable-original remedies stay visible with the editor. | [Permission failure](after-save-failure-22.png), [original recheck](after-recheck-22.png) |
| Trial reflow lost Export focus and scheduled repeated idle layout work. | Reflow restores the same focused button and changes styles only when needed. Idle layout settles. | `test_export_keeps_keyboard_focus_when_tools_reflow`, `test_music_layout_settles_after_font_width_and_details_changes` |

The only new on-screen label is **Session details**. Its accessible description
is “Show or hide session guidance and the creative pulse. Your local notes stay
below.” Music, Podcast & Voice, and Review & Rehearsal share this Notes layout.
Art keeps its direct readouts, **Make together / Paint along** doors, and existing
Conversation behavior. [Art combined recovery](art-combined-22.png) remains readable.
No storage, recording, export, chat, or session command owner changes.

## Proof and reproduction

Final local gate: **1949 passed, 49 subtests, 0 skipped across 84 fresh-process modules** in 241.75 seconds.

Nineteen new regression cases cover actual embedded 760×600/1000×740 windows,
13px/22px wider fonts, both failed-save and unreadable-original recovery,
keyboard navigation, idle layout, focus, and profile transitions. They preserve
the same QTextDocument, draft, selection, undo availability, unsent chat, and
recovery state without emitting save/send/edit signals from layout changes.
The initial nine cases failed on the old source. Eight further recovery cases
failed against the first trial. Both batches now pass. The broad sibling run
also caught the original Art→Music normal-geometry assertion; product styling
was restricted to narrow Notes and the unchanged assertion now passes.

The focused gate uses [focused-modules.json](focused-modules.json), with one
fresh Qt process per module as in hosted CI. [focused-results.json](focused-results.json)
records every module's result. Run from the repository root:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'PY'
import json, subprocess, sys
from pathlib import Path
folder = Path('docs/evidence/manager-demo-notes-pm2-20260922')
for module in json.loads((folder / 'focused-modules.json').read_text()):
    subprocess.run([sys.executable, '-m', 'pytest', '-q', module], check=True)
PY
```

This is a documented Art compatibility subset covering the room/start doors,
profile guidance, native return/retry, local Paint along, Notes/Conversation,
canvas, lesson, invitation, and room-ownership paths. #145's new YouTube source
was audited separately at its exact draft tip; this stacked draft does not
contain or change that separate player implementation.

Render the current fixtures (15 cases; output defaults to a temporary directory):

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=. .venv/bin/python -m pytest -q \
  -p tests.conftest docs/evidence/manager-demo-notes-pm2-20260922/render_fixtures.py
```

The [render manifest](render-manifest.json) records source and screenshot hashes.
Images use actual Qt widgets with synthetic state and controlled microphone
permission values; they do not prove real microphone, recording, meeting,
provider, or installed-device operation. Ruff, syntax, dependency consistency,
runtime dependency policy, UX smoke, and whitespace checks are required locally.
The first UX smoke attempt was blocked only by sandbox bytecode-cache writes;
using a writable temporary `PYTHONPYCACHEPREFIX` passed without product changes.
Exact-tip hosted results, including all four desktop builds, belong in the PR's
PRE_KAREN body and Bob-the-Bot #3 AFTER before completion.

## Ranked remaining work

1. **Studio layout in the full small window.** The 760×600 shell gives Studio
   approximately 407px of height; its simultaneous Arrange/mixer layout needs
   an interaction pass. Standalone 760×600 Studio tests did not model that
   shell. [Audited current layout](leftover-studio-760.png). This PR avoids
   changing recording/editing authority or hiding editing controls ad hoc.
2. **Studio external-document conflict resolution.** Two real Studio owners
   preserve the external sidecar, source audio and unsaved in-memory edits;
   Close vetoes correctly. Retry Save cannot resolve the changed durable token.
   A separate preserve-both-versions recovery-copy/reload flow needs design and
   Jeff feel. Typed permission/space remedies follow that boundary; changing
   text alone would leave a dead end. [Audit](storage-audit.md),
   [rendered conflict](leftover-studio-conflict.png).
3. **Jeff/Karen acceptance of the existing draft stack.** #145's mixed-version
   limitation, real two-computer/provider playback, and actual recording/storage
   rehearsal remain outstanding. Those are not replaced by rendered or CI proof.

Leave OPEN DRAFT for Karen and Jeff feel before merge/ship. Parked #37/#49,
prior draft tips, six unrelated user screenshots, and unsigned Latest v0.28.1
remain unchanged. No merge, tag, release, deployment, spend, signing,
notarization, or physical PASS.
