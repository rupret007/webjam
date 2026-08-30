# Merge and release map

> **Named candidate:** Jeff has named the full unsigned v0.27.2 release round.
> It starts from exact `master` commit
> `dab7d803b7551e8dbec517a2e5945f0af76285c9`. This docs-and-tests branch and
> its draft pull request are source review, not a package. No v0.27.2 tag,
> GitHub release draft, release, checksum manifest, or physical PASS exists.
> The existing exact Jamulus 3.12.2 and 3.12.3 records are explicitly approved
> through v0.27.2, so Host/Join and required component-input CI are
> source-eligible. Karen must PASS the exact pull-request head before Jeff's
> attended merge. Only after that merge may Bob tag the exact landed `master`;
> this pull request does not merge, tag, package, publish, or create a release.
>
> **Latest download:** unsigned/ad-hoc v0.27.1 release `377614785`, exact tag
> commit `1fc25f87c3386b1cd94303ecb407cdaff6509d1f`, with seven packages plus
> `WebJam-v0.27.1-SHA256SUMS.txt`. Tag run `33045632613` is red because its
> publisher refused to mutate an already-existing release; do not call the
> round publish-green. Do not retag or mutate v0.27.1. Do not restack #37.
> Do not invent a signed catalog.
> Do not add a version-specific publisher with invented pins.

The record of the finished product land and the honest boundaries that remain.
#14 (audio core), #15, #16, #19 (Art), and #17 (Music song tools) are all
already on `master`; #17 merged 2026-08-22 as `5ca6ba5`. There is no open
product branch.

`master` is the default branch and the only ship target. `main` is a stale,
non-authoritative mirror and must not be described as synchronized. Baseline
run `33285848154` is green on exact starting commit `dab7d803`; it proves that
base only, not this candidate head. Nothing is released until this repository's
own suite is green on the exact landed `master` commit. Fail closed: a red
required job stops the round, and a gate with no evidence stays **NOT RUN**.
**NOT RUN** is not a failure claim.

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

#19 originally established three Art start cards. Current source combines the
room-only and shared-canvas choices into **Make together**; artists work
locally, and the host may open one shared canvas from inside the room. #15 is
not the Art door. #15 landed a Studio Visit Preview ahead of #14; #19 replaced
that earlier door. Art and Music are equal first choices. Music still has to
keep **Host** / **Join** and nothing else after that choice.

## 2. Land order

The product land is complete. Already on `master`:
[#14](https://github.com/rupret007/webjam/pull/14)
`codex/v027-multitrack-proof-lab`, [#15](https://github.com/rupret007/webjam/pull/15),
[#16](https://github.com/rupret007/webjam/pull/16),
[#19](https://github.com/rupret007/webjam/pull/19)
`cursor/art-drawpile-shared-canvas-cd87`, and
[#17](https://github.com/rupret007/webjam/pull/17)
`cursor/music-ai-song-tools-in-jam-1eca`, merged 2026-08-22. Jeff merges every
product PR through the attended button; all five are merged and done. Nothing
is left to rebase, and nothing is waiting to land.

What remains in this named round:

| Step | Action | Who | Gate before it happens |
| --- | --- | --- | --- |
| 1 | #37 and #49 stay parked | nobody | they are parked outlines, not scheduled work — do not restack, rebase, or "fix" them |
| 2 | Leave the published v0.27.1 release alone | nobody | do not retag or mutate it; the boundaries in the header hold |
| 3 | Prepare the named v0.27.2 docs-and-tests candidate from exact `dab7d803b7551e8dbec517a2e5945f0af76285c9` | Bob | no product code; the complete local suite below passes on the exact head |
| 4 | Open one draft PR for Karen | Bob | exact base, docs pass, and local evidence are recorded; no tag or GitHub release draft exists |
| 5 | Karen reviews, then Jeff merges through the attended button only after Karen PASS | Karen, then Jeff | the ten-second read and every required hosted job below pass on the exact PR head |
| 6 | Tag exact landed `master` as `v0.27.2` later | Bob | Karen PASS and Jeff's merge are complete; `origin/master`, the tag target, and `webjam_qt.__version__` agree — this PR stops before this step |

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

The v0.27.1 release published the already-merged #47 feel wrap. It did not
retag v0.27.0, invent a signed catalog, delete old unsigned test releases, or
restack #37. Its tag run `33045632613` built every desktop target, then failed
closed at the publisher because the release already existed. Preserve that red
result instead of rewriting the round as publish-green. The generic
`.github/workflows/publish-latest-release.yml` still requires a catalog that
authorizes this exact WebJam version; sealed v3 still targets 0.22.5 only
(observed v0.27.0 failure `33036413984`). Do not invent that catalog, mutate
the v0.27.1 release, or write a parallel runbook.

Jeff has named the full unsigned v0.27.2 release round. It does not wait to be
named and does not wait on #17, which is already on `master`. The round starts
from exact `dab7d803b7551e8dbec517a2e5945f0af76285c9`; green run
`33285848154` is baseline evidence for that starting commit only. Candidate
evidence must name the later exact docs-and-tests head.

The current stop is one draft pull request for Karen. GitHub **Latest** remains
v0.27.1 until Karen PASS, Jeff's attended merge, Bob's later annotated tag of
the exact landed `master`, and the existing release workflow. Tag CI may create
a reviewable unsigned GitHub release draft only after `v0.27.2` matches
`webjam_qt.__version__` and exact `origin/master`. This pull request performs
none of those later actions.

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

### Complete hosted suite second

The exact candidate head must then pass all 12 required hosted jobs in one
automatic workflow run:

- `test`
- `Integration (real Jamulus 3.12.2)` and
  `Integration (real Jamulus 3.12.3)`
- `Jamulus 3.12.3 update input (windows-x64)` and
  `Jamulus 3.12.3 update input (macos-universal)`
- `Build Desktop (windows-x64)`, `Build Desktop (macos-arm64)`,
  `Build Desktop (macos-x64)`, and `Build Desktop (linux-x64)`
- `Pocket Stage (iOS app)`
- `Transport (Go security and cross-build)`
- `Reference service (protocol and container)`

`Build Desktop` requires the integrations, update-input checks, Transport,
Reference, Pocket Stage, and hosted `test` through `needs:`, so all of them gate
the same round.

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
| `Publish GitHub Release` | a branch or pull request is not a `v0.27.2` tag, and this round does not publish |
| Two-Mac Art room video and Drawpile | two physical machines, real observation |
| Live Music AI | needs a real service credential |
| Physical and hardware checklist rows | real musician observation against an exact package |

## 5. Docs pass

One docs-only pass over `CHANGELOG.md`, `USER_GUIDE.md`, `README.md`,
`README_SIMPLE.md`, `QUICK_HELP_MAP.md`, `HELP_ROUTING_MAP.md`, `FIRST_JAM.md`,
`ARCHITECTURE.md`, and `docs/PROJECT_BRIEF.md`. This map and its executable
contract, `docs/MERGE_AND_RELEASE.md` and `tests/test_merge_and_release_map.py`,
are part of the same change:

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
5. Move #58's **Art starts with fewer choices** entry from `Unreleased` into
   the still-unreleased v0.27.2 section. `Unreleased` is for work after this
   named candidate. Never edit the released v0.27.1 section.

## 6. Who merges

Jeff presses merge, one PR at a time, and only when that step's gate in
section 2 is met. Bob prepares branches, rebases, pushes, and reports; Bob does
not merge unattended and does not tag or publish from this pull request. The
later tag is a separate step after Karen PASS and Jeff's merge. Rebases go to
the branch they belong to — no force-push over someone else's product branch.

`tests/test_merge_and_release_map.py` keeps the doors, job names, and docs list
on this page in step with `.github/workflows/ci.yml` and the repository.
