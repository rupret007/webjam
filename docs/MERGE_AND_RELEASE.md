# Merge and release map

> **Current source:** post-release v0.27.1 (`webjam_qt.__version__`). This
> checkout is three commits ahead of the released tag and is not a package.
>
> **Latest download:** unsigned/ad-hoc v0.27.1 release `377614785`, exact tag
> commit `1fc25f87c3386b1cd94303ecb407cdaff6509d1f`, with seven packages plus
> `WebJam-v0.27.1-SHA256SUMS.txt`. Tag run `33045632613` is red because its
> publisher refused to mutate an already-existing release; do not call the
> round publish-green. Do not retag or mutate v0.27.1. Do not restack #37.
> Do not invent a signed catalog.
> Do not add a version-specific publisher with invented pins.

The short plan for the remaining product land and one honest release round
after it. #14 (audio core) and #19 (Art) are already on `master`. #17 (Music
song tools) is the open product branch.

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
| Art | **Art** and **Music** as equal first choices; then **Talk & make**, **Paint together**, **Paint along**, then **Host** / **Join** |
| Music | **Art** and **Music** as equal first choices; then **Host** / **Join**, nothing else |

Banned on the first screen: Studio Visit, Drawpile, Jamulus, host-clocked,
Moises, BYOK, Preview caveats, API. Tool and vendor names belong inside the
room, never in the door.

Where a test can hold a door it does —
[#19](https://github.com/rupret007/webjam/pull/19) landed
`tests/test_art_start_ux.py` on `master` — but a green suite is not a claim that
the first screen makes sense, so the human read happens before the merge.

Art on `master` now has those three start cards from #19. #15 is not the Art
door. #15 landed a Studio Visit Preview ahead of #14; #19 replaced that door.
Art and Music are equal first choices. Music still has to keep **Host** /
**Join** and nothing else after that choice.

## 2. Land order

Already on `master`: [#14](https://github.com/rupret007/webjam/pull/14)
`codex/v027-multitrack-proof-lab`, [#15](https://github.com/rupret007/webjam/pull/15),
[#16](https://github.com/rupret007/webjam/pull/16), and
[#19](https://github.com/rupret007/webjam/pull/19)
`cursor/art-drawpile-shared-canvas-cd87`. Jeff merges those. They are done.

| Step | Action | Who | Gate before it happens |
| --- | --- | --- | --- |
| 1 | Rebase [#17](https://github.com/rupret007/webjam/pull/17) `cursor/music-ai-song-tools-in-jam-1eca` on current `master` | Bob prepares | resolve the leftover #19 overlap; do not rewrite Music door chrome another worker owns |
| 2 | Land #17 | Jeff merges | required CI green **after** the rebase, plus the section 1 Music doors |
| 3 | Open one docs-only release PR if section 5 leaves anything unfixed | Bob prepares, Jeff merges | no product code in it |

#17 stays last because it is the remaining room. It is still a draft. Leave the
door-chrome work on that branch to the worker already on it.

## 3. Why this order

#14 was the audio core: recording recovery plus the multitrack proof lab, about
100 files, including the shared session core the rooms build on. #19 (Art) and
#17 (Music song tools) are rooms on top of that core. The core landed first, so
each room was rebased once instead of resolving the same core twice.

#19 landed before #17 because Art was the room that failed the section 1 gate
on `master`, and because landing it first put the shared UI files both rooms
touch on `master` before the branch that has to be reworked around them.

| Pair | Overlapping files | Resolved in |
| --- | --- | --- |
| #14 and #19 | `core/session_conductor.py`, `core/session_intelligence.py`, `core/session_transfer.py`, `core/session_transfer_runtime.py` | done on `master` |
| #19 and #17 | `core/settings.py`, `webjam_qt/controllers/application_controller.py`, `webjam_qt/widgets/session_strip.py`, `tests/test_host_share_join_flow.py`, `tests/test_offline_invitation_gate.py` | step 1 |
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

Run after #17 is on `master`, on one `master` commit. Every job below must be
green in the same round:

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
section 1 gate counts here too: a green matrix behind a first screen that fails
the Art or Music doors is not a release.

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

1. Musician-visible names read **Art** and **Music**. #19 already renamed the
   Art door in those guides plus `ARCHITECTURE.md` and `docs/PROJECT_BRIEF.md`.
   `CREATIVE_MODES_MVP_SPEC.md` keeps one historical line that Art shipped its
   Preview under the name Studio Visit; that is history, not leftover door copy.
   The pass finishes any Music names #17 still owns.
2. Webex stays native, external, and optional. No add-on, no embedded-app
   promise.
3. KISS. No integration wall and no feature matrix a musician has to read
   before playing.
4. Keep the implemented / planned / automated-only / physical / **NOT RUN**
   boundary the [documentation rules](README.md#documentation-rules) already
   require.
5. `CHANGELOG.md` gets one `Unreleased` entry per landed PR. Never edit a
   released section. #19 already wrote the Art Unreleased block.

## 6. Who merges

Jeff presses merge, one PR at a time, and only when that step's gate in
section 2 is met. Bob prepares branches, rebases, pushes, and reports; Bob does
not merge unattended, does not tag, and does not publish. Rebases go to the
branch they belong to — no force-push over someone else's product branch.

`tests/test_merge_and_release_map.py` keeps the doors, job names, and docs list
on this page in step with `.github/workflows/ci.yml` and the repository.
