# Merge and release map

> **Current download and publication status:** [GitHub Latest](https://github.com/rupret007/webjam/releases/latest).
> **Historical checkpoint, checked 2026-09-29 before the v0.29.0 release round:**
> immutable unsigned/ad-hoc v0.28.4 release `396603181`, published `2026-09-25T12:57:13Z`. Annotated tag
> object `dc494f0450ccc6f690ba7c9768ff4b575fe2da6c` peels to
> `ce52e9c9302cb3f28510a3e3b0e9edfff8b31111`. The seven packages plus
> `WebJam-v0.28.4-SHA256SUMS.txt` identify the published build.
> **Source identity:** v0.29.0. Jeff authorized this unsigned
> testing release once its real gates pass; feel and Final Build remain his.

Earlier product work #14, #15, #16, #17, and #19 is already on `master`;
#17 merged 2026-08-22. The current release adds saved workspaces, rehearsal
plans, Art projects, and take review. `master` is the only ship target.
Historical release entries v0.28.0, v0.27.2, and Jamulus catalog v1–v3 were
deleted by owner; their git tags remain. Do not recreate them or change their
historical identities. #37 and #49 stay parked.

## 1. Ten-second UX gate

Design ships with the code. A PR is not ready to land, and `master` is not ready
to be called released, if a person who opens the app fails the ten-second test:
they see what to do, and do it, without being told.

| Room | Doors on the first screen |
| --- | --- |
| Art | **Art** and **Music** as equal first choices; then **Make together**, **Paint along**, then **Host** / **Join** |
| Music | **Art** and **Music** as equal first choices; then **Host** / **Join**, nothing else |

Banned on the first screen: Studio Visit, Drawpile, Krita, Jamulus, Webex,
Moises, Music AI, stems, BYOK, host-clocked, Preview caveats, API. Tool and
vendor names belong inside the room, never in the door.

Where a test can hold a door it does —
[#19](https://github.com/rupret007/webjam/pull/19) landed
`tests/test_art_start_ux.py` on `master` — but a green suite is not a claim that
the first screen makes sense, so the human read happens before the merge.
The current checksum-bound human read is the **NOT RUN**
[historical owner click gate](../UX_ACCEPTANCE_CHECKLIST.md#historical-owner-click-gate-v0284-two-card-door).
It uses an exact v0.28.4 release asset and stops before Host or Join, so it
does not create a room or claim live audio. Every physical gate remains **NOT RUN**.

#19 originally established three Art start cards. Current source combines the
room-only and shared-canvas choices into **Make together**; artists work
locally, and the host may open one shared canvas from inside the room. #15 is
not the Art door. #15 landed a Studio Visit Preview ahead of #14; #19 replaced
that earlier door. Art and Music are equal first choices. Music still has to
keep **Host** / **Join** and nothing else after that choice.

## 2. Land order

Use one current release branch and one PR. Do not merge duplicate paths.

| Step | Action | Gate before it happens |
| --- | --- | --- |
| 1 | Keep #37 and #49 parked and preserve historical releases | do not retag, replace, or mutate published evidence |
| 2 | Prepare source and docs from current `master` | isolated checkout; intended product scope and version agree |
| 3 | Verify the exact candidate | source checks and all required hosted jobs pass; failures are fixed on a new tip |
| 4 | Merge the authorized release PR to `master` | one reviewed tip; no force-push |
| 5 | Annotate the unused version tag at exact master and build it | tag/version/source match; all tag CI gates pass and create a new draft |
| 6 | Verify and promote the unsigned testing draft | exact inventory, warnings, hashes, current master/tag, and user authorization; reverify immutable public Latest |

Jeff's authorization for this named round covers the merge and unsigned test
publication after the gates. It does not supply a physical or Final Build PASS.
Future work needs its own scope and authorization.

## 3. Why this order

#14 was the audio core: recording recovery plus the multitrack proof lab, about
100 files, including the shared session core the rooms build on. #19 (Art) and
#17 (Music song tools) are rooms on top of that core. The core landed first, so
each room was rebased once instead of resolving the same core twice.

#19 landed before #17 because Art was the room that failed the section 1 gate
on `master`, and because landing it first put the shared UI files both rooms
touch on `master` before the branch that had to be reworked around them.

| Pair | Overlapping files | Resolved in |
| --- | --- | --- |
| #14 and #19 | `core/session_conductor.py`, `core/session_intelligence.py`, `core/session_transfer.py`, `core/session_transfer_runtime.py` | done on `master` |
| #19 and #17 | `core/settings.py`, `webjam_qt/controllers/application_controller.py`, `webjam_qt/widgets/session_strip.py`, `tests/test_host_share_join_flow.py`, `tests/test_offline_invitation_gate.py` | done on `master` |
| #14 and #17 | none | — |

## 4. Release round

The historical published baseline for this round is v0.28.4. Tag CI `36133468143` passed and produced its
eight-asset draft; immutable release `396603181` became Latest on 2026-09-25.
The source version for this round is v0.29.0. GitHub Releases records its
publication status; this document does not infer publication from source.

The generic `.github/workflows/publish-latest-release.yml` requires a live
signed catalog for the exact version. Its `jamulus-components-v3` release was
deleted by owner; the historical signed catalog only targeted v0.22.5. Do not
dispatch it or claim catalog approval for this candidate. The baked, unchanged
Jamulus 3.12.2/3.12.3 records extend through v0.29.0; they are a different policy
from managed-update signatures.

The tag lane in `ci.yml` creates an unsigned draft after source/package gates.
For this authorized maintainer round, verify all eight assets and seven
package checksums, then promote that exact draft and verify public Latest.
The [desktop runbook](DESKTOP_RELEASE_RUNBOOK.md#current-unsigned-testing-release)
records the binding and recheck steps. No historical release is replaced.

### Complete local suite first

Run the following once, in this order, on the exact candidate head:

1. `ruff check webjam_qt/ core/ ui/ services/ api/`
2. dependency audits: `python -m pip check`,
   `python tools/runtime_dependency_policy.py --check`, `pip-audit`, and every
   supported native dependency lock
3. `python -m compileall -q core webjam_qt ui services api tests`
4. `python ux_smoke_test.py`
5. every tracked `tests/test_*.py` module, in deterministic order, with one
   fresh Python process per module and no retry

`git diff --check` is part of the source check. A subset, one combined long-lived
pytest process, or a prior commit's result is not the complete local suite.
The registered `requires_local_socket` marker identifies modules that open a
real OS-local listener or connection; it never skips them in hosted CI. A
sandboxed run that excludes those modules is explicitly incomplete until each
marked module passes once, in a fresh process, with local-socket permission.

### Complete hosted suite second

The exact candidate head must then pass all 13 required hosted jobs in one
automatic workflow run:

- `test`
- `Integration (real Jamulus 3.12.2)` and
  `Integration (real Jamulus 3.12.3)`
- `Jamulus 3.12.3 update input (windows-x64)` and
  `Jamulus 3.12.3 update input (macos-universal)`
- `Build Desktop (windows-x64)`, `Build Desktop (macos-arm64)`,
  `Build Desktop (macos-x64)`, and `Build Desktop (linux-x64)`
- `Pocket Stage (iOS app)`
- `Art companion (iPhone and iPad)` — real LAN interoperability plus unsigned
  simulator UI checks; fixture screenshots require human inspection and do
  not certify physical touch, installation, or meeting media.
- `Transport (Go security and cross-build)`
- `Reference service (protocol and container)`

`Build Desktop` requires the integrations, update-input checks, Transport,
Reference, Pocket Stage, and hosted `test` through `needs:`, so all of them gate
the same round. Art companion runs independently in that workflow and must
also pass on the same candidate head before review.

Red means stop. Do not re-run a job to change its result, do not tag, and do not
create or publish a release to hide a failure. Fix the cause and repeat the
whole round on a new exact head. The section 1 gate counts here too: a green
matrix behind a first screen that fails the Art or Music doors is not a release.

These stay **NOT RUN** unless real evidence exists for the exact candidate:

| Gate | Why it is NOT RUN |
| --- | --- |
| `Certify Jamulus/JACK (one hour, manual)` | manual dispatch only (`run_one_hour_certification`) |
| `Windows Release Trust (windows-x64)`, `macOS Release Trust` | credentialed signing/notarization rehearsals behind `windows_signing_rehearsal` / `macos_signing_rehearsal` |
| `Jamulus 3.12.3 HEADLESS evidence` | quarantined dispatch-only evidence build |
| `Publish GitHub Release` | tag-only draft creation; check the exact tag workflow and GitHub release for its status |
| Two-Mac Art room video and Drawpile | two physical machines, real observation |
| Live Music AI | needs a real service credential |
| Physical and hardware checklist rows | real musician observation against an exact package |

## 5. Docs pass

One docs-only pass over `CHANGELOG.md`, `USER_GUIDE.md`, `README.md`,
`README_SIMPLE.md`, `QUICK_HELP_MAP.md`, `HELP_ROUTING_MAP.md`, `FIRST_JAM.md`,
`ARCHITECTURE.md`, `UX_ACCEPTANCE_CHECKLIST.md`, and
`docs/PROJECT_BRIEF.md`. This map and its executable contract,
`docs/MERGE_AND_RELEASE.md` and `tests/test_merge_and_release_map.py`, are part
of the same change:

1. Musician-visible names read **Art** and **Music**. #19 already renamed the
   Art door in those guides plus `ARCHITECTURE.md` and `docs/PROJECT_BRIEF.md`.
   `CREATIVE_MODES_MVP_SPEC.md` keeps one historical line that Art shipped its
   Preview under the name Studio Visit; that is history, not leftover door copy.
   The Music names landed with #17; the pass only fixes a page that still
   reads otherwise.
2. Webex stays native, external, and optional. No add-on, no embedded-app
   promise.
3. KISS. No integration wall and no feature matrix a musician has to read
   before playing.
4. Keep the implemented / planned / automated-only / physical / **NOT RUN**
   boundary the [documentation rules](README.md#documentation-rules) already
   require.
5. Keep #58's **Art starts with fewer choices** entry in the released v0.27.2
   section. `Unreleased` is for work after that exact tag. Never rewrite the
   released v0.27.1 feature history.

## 6. Who merges

Jeff owns release scope, feel, and Final Build judgment. Codex may complete the
merge and unsigned testing publication explicitly authorized for this named
round once the gates pass. That authority does not waive failing tests or
package verification. There is no force-push over someone else's product branch.
Signing, notarization, spend, and a signed component channel remain separate.

`tests/test_merge_and_release_map.py` keeps the doors, job names, and docs list
on this page in step with `.github/workflows/ci.yml` and the repository.
