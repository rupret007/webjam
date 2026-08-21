# Merge and release map

The short plan for landing the three open product branches and running one
honest release round after them.

`master` is the default branch and the only ship target; `main` was
fast-forwarded to it. Nothing is released until this repository's own suite is
green on the landed `master` commit. Fail closed: a red required job stops the
round, and a gate with no evidence stays **NOT RUN**. **NOT RUN** is not a
failure claim.

## 1. Ten-second UX gate

Design ships with the code. A PR is not ready to land, and `master` is not ready
to be called released, if a person who opens the app fails the ten-second test:
they see what to do, and do it, without being told.

| Room | Doors on the first screen |
| --- | --- |
| Art | **Talk & make**, **Paint together**, **Paint along**, then **Host** / **Join** |
| Music | **Host** / **Join**, nothing else |

Banned on the first screen: Studio Visit, Drawpile, Jamulus, host-clocked,
Moises, BYOK, Preview caveats, API. Tool and vendor names belong inside the
room, never in the door.

Where a test can hold a door it does —
[#19](https://github.com/rupret007/webjam/pull/19) ships
`tests/test_art_start_ux.py` — but a green suite is not a claim that the first
screen makes sense, so the human read happens before the merge.

`master` fails this gate today: #15 landed and still shows **Host Studio
Visit** / **Join Studio Visit** with a Preview caveat. That is a known hole,
#19 is the fix, and #15 is not the Art door.

## 2. Land order

| Step | Action | Who | Gate before it happens |
| --- | --- | --- | --- |
| 1 | Land [#14](https://github.com/rupret007/webjam/pull/14) `codex/v027-multitrack-proof-lab` | Jeff merges | MERGEABLE and required CI green on the PR head |
| 2 | Rebase [#19](https://github.com/rupret007/webjam/pull/19) `cursor/art-drawpile-shared-canvas-cd87` on the new `master` | Bob prepares | resolve the #14 overlap by hand, then push the branch |
| 3 | Land #19 | Jeff merges | required CI green **after** the rebase, plus the section 1 doors |
| 4 | Rebase [#17](https://github.com/rupret007/webjam/pull/17) `cursor/music-ai-song-tools-in-jam-1eca` on the new `master` | Bob prepares | resolve the #19 overlap; this branch lands last |
| 5 | Land #17 | Jeff merges | required CI green after the rebase, plus the section 1 doors |
| 6 | Open one docs-only release PR if section 5 leaves anything unfixed | Bob prepares, Jeff merges | no product code in it |

[#15](https://github.com/rupret007/webjam/pull/15) and
[#16](https://github.com/rupret007/webjam/pull/16) are already on `master`. #15
landed ahead of #14, so its Art Preview is the hole #19 closes, not a finished
door.

## 3. Why this order

#14 is the audio core: recording recovery plus the multitrack proof lab, about
100 files, including the shared session core the other two build on. #19 (Art)
and #17 (Music song tools) are rooms on top of that core. Landing the core first
means each room is rebased once instead of resolving the same core twice.

#19 goes before #17 because Art is the room that fails the section 1 gate on
`master` today, and because landing it first puts the shared UI files both rooms
touch on `master` before the branch that has to be reworked around them.

| Pair | Overlapping files | Resolved in |
| --- | --- | --- |
| #14 and #19 | `core/session_conductor.py`, `core/session_intelligence.py`, `core/session_transfer.py`, `core/session_transfer_runtime.py` | step 2 |
| #19 and #17 | `core/settings.py`, `webjam_qt/controllers/application_controller.py`, `webjam_qt/widgets/session_strip.py`, `tests/test_host_share_join_flow.py`, `tests/test_offline_invitation_gate.py` | step 4 |
| #14 and #17 | none | — |

## 4. Release round

Run after every branch above is on `master`, on one `master` commit. Every job
below must be green in the same round:

- `test` — ruff, dependency audits, `compileall`, the UX smoke gate, and every
  `tests/test_*.py` module
- `Build Desktop (windows-x64)`, `(macos-arm64)`, `(macos-x64)`, `(linux-x64)`
- `Pocket Stage (iOS app)`
- `Transport (Go security and cross-build)`
- `Reference service (protocol and container)`

`Build Desktop` already requires `Integration (real Jamulus 3.12.2 / 3.12.3)`
and `Jamulus 3.12.3 update input (windows-x64 / macos-universal)` through
`needs:`, so those run in the same round and gate it.

Red means stop. Do not re-run a job to change its result, do not tag, and do not
draft release notes until the cause is fixed and the round is repeated. The
section 1 gate counts here too: a green matrix behind a first screen that says
Studio Visit is not a release.

These stay **NOT RUN** unless real evidence exists for the exact candidate:

| Gate | Why it is NOT RUN |
| --- | --- |
| `Certify Jamulus/JACK (one hour, manual)` | manual dispatch only (`run_one_hour_certification`) |
| `Windows Release Trust (windows-x64)`, `macOS Release Trust` | signing rehearsals behind `windows_signing_rehearsal` / `macos_signing_rehearsal` |
| `Jamulus 3.12.3 HEADLESS evidence` | quarantined dispatch-only evidence build |
| Two-Mac Art room video and Drawpile | two physical machines, real observation |
| Live Music AI | needs a real service credential |
| Physical and hardware checklist rows | real musician observation against an exact package |

## 5. Docs pass

One docs-only pass over `USER_GUIDE.md`, `README.md`, `QUICK_HELP_MAP.md`,
`CHANGELOG.md`, and `HELP_ROUTING_MAP.md`:

1. Musician-visible names read **Art** and **Music**. `master` still says Studio
   Visit in `USER_GUIDE.md`, `QUICK_HELP_MAP.md`, `HELP_ROUTING_MAP.md`,
   `CHANGELOG.md`, `ARCHITECTURE.md`, `CREATIVE_MODES_MVP_SPEC.md`, and
   `docs/PROJECT_BRIEF.md`. #19 renames what it touches; the pass finishes the
   rest, because a rename that stops at five files leaves the others wrong.
2. Webex stays native, external, and optional. No add-on, no embedded-app
   promise.
3. KISS. No integration wall and no feature matrix a musician has to read
   before playing.
4. Keep the implemented / planned / automated-only / physical / **NOT RUN**
   boundary the [documentation rules](README.md#documentation-rules) already
   require.
5. `CHANGELOG.md` gets one `Unreleased` entry per landed PR. Never edit a
   released section.

## 6. Who merges

Jeff presses merge, one PR at a time, and only when that step's gate in
section 2 is met. Bob prepares branches, rebases, pushes, and reports; Bob does
not merge unattended, does not tag, and does not publish. Rebases go to the
branch they belong to — no force-push over someone else's product branch.

`tests/test_merge_and_release_map.py` keeps the doors, job names, and docs list
on this page in step with `.github/workflows/ci.yml` and the repository.
