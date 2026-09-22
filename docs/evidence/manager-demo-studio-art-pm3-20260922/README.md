# PM3: compact Studio and a next step for every artist

One additional OPEN DRAFT above #148 (`844ea1080500fc937101b19b21168b5a5525600c`).
The [baseline audit](audit-baseline.md) records #145–#148, parked holds, master,
and unchanged Latest. The new work resolves #148's explicitly ranked compact
Studio layout gap and the Art room's lost own-tools guidance when an optional
activity is offered.

## BEFORE / AFTER

| State | Before | After |
| --- | --- | --- |
| Studio inside the actual 760×600 app | Fixed chrome consumes the roughly 407px workspace; Arrange/mixer overlap or disappear. | Recording, Play/Stop, Export and guidance remain outside a scrollable editing workspace. Arrange comes first; existing mix/output/details controls remain reachable. |
| Studio with enlarged text | Labels clip while rows compete for height. | Header and transport reflow; editing content keeps measured sizes with scrolling. Full labels and the outer window size are retained. |
| Studio save/playback failure and retained Notes | Crowded editing workspace shares recovery space. | Existing guidance and explicit recovery stay readable; output failures reveal the existing Playback output picker. Notes recovery still opens independently. |
| Art with canvas or Paint along offered | Own-space guidance is replaced by the offered activity; canvas is the only apparent way to make. | “Start making with paper, clay, a model, printer, or your usual app. Shared activities are optional.” Existing optional actions remain unchanged. |
| Missing Drawpile | Host is told to install it “to paint together”; guest may “just talk.” | Installation is only for the shared canvas; artists may keep making with their own tools. |

In compact Art rooms the actionable own-tools sentence takes the place of
repeated display/prose context. The confirmed room heading and existing actions
remain visible; the complete connection context remains in the accessible
description. No status fact, action label, route, media owner or recording
permission changes. Art still has exactly **Make together / Paint along**.

Selected render pairs:

| Surface | BEFORE | AFTER |
| --- | --- | --- |
| Compact Studio, actual theme | [Clipped Arrange/mixer](before-studio-760-600-normal.png) | [Readable actions and Arrange](studio-after-760-theme-top.png) |
| Studio recovery | [Crowded save failure](before-studio-retry-125.png) | [Retry Save plus retained Notes, 22px](studio-after-retry-and-notes-22px.png) |
| Studio playback failure | Existing copy retained | [Playback output revealed, 22px](studio-after-playback-output-22px.png) |
| Studio recording/export safety | Existing owner preserved | [Stop with Notes](studio-after-live-stop-and-notes-22px.png), [pending export](studio-after-export-busy-22px.png), [Windows label](studio-after-aligned-originals-22px.png) |
| Art optional canvas | [Guest before](art-before-guest-make-canvas-100.png) | [Any-artist next step](art-after-guest-make-canvas-100.png) |
| Art combined activities | [Guest before, 125% glyph width](art-before-guest-paint-both-125.png) | [Own-tools step plus both actions](art-after-guest-paint-both-125.png) |
| Missing Drawpile | [Host before](art-before-host-install-125.png), [guest before](art-before-guest-install-125.png) | [Host after](art-after-host-install-125.png), [guest after](art-after-guest-install-125.png) |

## Iteration evidence

- Fresh renders at the unchanged #148 tip prove the embedded Studio gap; the
  existing standalone 760×600 tests did not model its actual app-shell height.
- The first Art trial added a row and failed 12 existing 125%-width geometry
  cases. An isolated #148 archive passed those same 54 tests. The new sentence
  now replaces redundant visual context; all original assertions remain.
- Independent review caught a three-sentence missing-Drawpile message against
  the existing two-sentence copy contract. The message was shortened and the
  original contract test retained.
- Independent Studio review found a 40px/44px output-picker height loop after
  enlarging then restoring fonts; one measured height now replaces competing assignments.
- A second independent Studio probe found keyboard re-entry could focus a
  control outside the scroll viewport and a queued layout event could restart
  the timer after shutdown. Focus reveal and the shutdown guard are covered
  by the new compact tests.
- The Studio trial initially placed Export inside the scroll. Review found
  that the HUD delegates this action to Studio, so the same Export button now
  stays beside Play/Stop, using its unchanged handler.

## Proof

The full Art sibling baseline on an isolated exact #148 archive passed **2,797 tests**,
with **14 opt-in Swift integration skips across 79 modules** and zero source drift.
See [baseline summary](art-baseline-summary.json) and [module list](art-baseline-modules.json).
Those opt-in checks are not a local PASS; hosted integration evidence is separate.

Final local gate: **4,685 tests + 49 subtests passed, 14 skipped across 170
fresh-process modules** in 358.60 seconds. This includes the full 79-module Art
sibling gate, Studio/Notes recovery, ownership, stale actions and recording
paths. See [summary](focused-summary.json), [module manifest](focused-modules.json)
and [results](focused-results.json). All 18 new Studio regressions also passed
after two fixture-only portability refinements; runtime source remained frozen.

Ruff across runtime trees and changed tests/renderers, syntax, dependency
consistency/policy, UX smoke and diff checks passed. The Studio renderer passed
9 cases; Art produced 28 finite cases. No Go source changed; exact-tip hosted
Transport provides the Go/security/cross-build proof. Hosted results are recorded
in PRE_KAREN and AFTER and must be green before completion.

The Art patch, including new assertions, applies cleanly to a temporary archive
of separate OPEN DRAFT #145 at `ec6fac77bd9ec477bfefd64bb07b8ca1d6cb478a`.
**1,187 tests passed, zero skipped across 16 fresh-process modules** in that
combined source, including its YouTube, source-specific next actions, two doors,
shared-canvas and stale ownership contracts. See [module list](compatibility-modules.json),
[results](compatibility-results.json) and [source/patch fingerprints](compatibility-summary.json).
This is compatibility evidence; #145 itself was not changed or merged into this
branch. Its source-kind-aware player and Conversation-label fixes remain its scope.

Run the focused gate from the repository root, with a fresh Qt process for
each module (matching hosted CI):

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'RUN'
import json, subprocess, sys
from pathlib import Path
folder = Path('docs/evidence/manager-demo-studio-art-pm3-20260922')
for module in json.loads((folder / 'focused-modules.json').read_text()):
    subprocess.run([sys.executable, '-m', 'pytest', '-q', module], check=True)
RUN
```

Reproduce Studio fixtures:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  docs/evidence/manager-demo-studio-art-pm3-20260922/render_studio.py
```

`WEBJAM_STUDIO_EVIDENCE_DIR` overrides the temporary output directory. Studio
fixtures use the unchanged theme or 22px labels/buttons, with synthetic takes,
local-save and playback failures, retained Notes, recording and pending export.
The Studio baseline images marked `125` use 16px text and 125% glyph width;
final stress images use 22px text. The normal-theme BEFORE/AFTER pair uses the
same unmodified theme. Baseline geometry measurements accompany the images.

Reproduce Art fixtures from the repository root:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python docs/evidence/manager-demo-studio-art-pm3-20260922/render_art.py
```

`WEBJAM_ART_EVIDENCE_DIR` overrides the temporary output directory. The committed
[Art manifest](art-selected-manifest.json) records the exact source, renderer,
font and image hashes for eight selected BEFORE/AFTER pairs. The full renderer
covers 28 cases per source. Its 125% setting stretches glyph width, matching
the established Art geometry gate; it is not a claim about OS scaling.

All screenshots are actual Qt widgets using synthetic state. They do not prove
physical microphones, cameras, meeting providers, installed-device behavior,
recording quality, or real two-computer playback.

## Ranked leftovers and limits

1. Studio external-document conflict recovery still needs a safe workflow that
   preserves both versions; PM3 changes presentation, not persistence authority.
2. Karen review and Jeff feel across the draft stack; follow with #145's existing
   two-computer/provider/mixed-version checks and physical recording/storage rehearsal.
3. Existing fine track-lane typography can crowd a track number/name or status
   at 22px. The standard theme and persistent action labels are covered here;
   a wider track-lane typography pass remains for Jeff feel.
4. In the unchanged own-tools Art room, the 125%-width fixture retains its baseline
   24px scroll. #145 already addresses that room hierarchy; offered-activity PM3
   cases fit without scrolling. This draft does not duplicate #145's scope.

Keep OPEN DRAFT for Karen and Jeff feel before merge/ship. #145–#148 and parked
#37/#49 remain unchanged. No merge, tag, release, deployment, spend, signing,
notarization, or physical PASS. Unsigned Latest v0.28.1 stays unchanged.
