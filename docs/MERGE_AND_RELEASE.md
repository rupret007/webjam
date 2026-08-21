# Merge and release map

The short plan for landing the three open product branches and running one
honest release round after them.

`master` is the default branch and the only ship target; `main` was
fast-forwarded to it. Nothing is released until this repository's own suite is
green on the landed `master` commit. Fail closed: a red required job stops the
round, and a gate with no evidence stays **NOT RUN**. **NOT RUN** is not a
failure claim.

## 1. Land order

| Step | Action | Who | Gate before it happens |
| --- | --- | --- | --- |
| 1 | Land [#14](https://github.com/rupret007/webjam/pull/14) `codex/v027-multitrack-proof-lab` | Jeff merges | MERGEABLE and required CI green on the PR head |
| 2 | Rebase [#15](https://github.com/rupret007/webjam/pull/15) `cursor/studio-visit-reference-video-78c2` on the new `master` | Bob prepares | resolve the #14 overlap by hand, then push the branch |
| 3 | Land #15 | Jeff merges | required CI green **after** the rebase, not the pre-rebase run |
| 4 | Rebase [#17](https://github.com/rupret007/webjam/pull/17) `cursor/music-ai-song-tools-in-jam-1eca` on the new `master` | Bob prepares | it is CONFLICTING today; resolve once, against a `master` that already has #14 and #15 |
| 5 | Land #17 | Jeff merges | required CI green after the rebase |
| 6 | Open one docs-only release PR if section 4 leaves anything unfixed | Bob prepares, Jeff merges | no product code in it |

[#16](https://github.com/rupret007/webjam/pull/16) (Jamulus test clock) is
already on `master`. No action.

When this page was written, #14's required jobs were green and every manual and
signing job was skipped, so those rows stay **NOT RUN**.

## 2. Why this order

#14 is the audio core: recording recovery plus the multitrack proof lab, about
100 files, including the shared session core and the workflow that the other two
build on. #15 (Art) and #17 (Music song tools) are rooms on top of that core.
Landing the core first means each room is rebased once instead of resolving the
same core twice.

#15 goes before #17 because #15 is MERGEABLE now and #17 already conflicts with
`master`. Taking the clean branch first keeps every rebase down to one unknown,
and it puts the #15/#17 shared UI files on `master` before the branch that has
to be reworked anyway.

| Pair | Overlapping files | Resolved in |
| --- | --- | --- |
| #14 and #15 | `.github/workflows/ci.yml`, `core/session_conductor.py`, `core/session_intelligence.py`, `core/session_transfer.py`, `core/session_transfer_runtime.py` | step 2 |
| #15 and #17 | `webjam_qt/controllers/application_controller.py`, `webjam_qt/widgets/session_strip.py`, `tests/test_host_share_join_flow.py`, `tests/test_offline_invitation_gate.py` | step 4 |
| #14 and #17 | none | — |

## 3. Release round

Run after all three are on `master`, on one `master` commit. Every job below
must be green in the same round:

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
draft release notes until the cause is fixed and the round is repeated.

These stay **NOT RUN** unless real evidence exists for the exact candidate:

| Gate | Why it is NOT RUN |
| --- | --- |
| `Certify Jamulus/JACK (one hour, manual)` | manual dispatch only (`run_one_hour_certification`) |
| `Windows Release Trust (windows-x64)`, `macOS Release Trust` | signing rehearsals behind `windows_signing_rehearsal` / `macos_signing_rehearsal` |
| `Jamulus 3.12.3 HEADLESS evidence` | quarantined dispatch-only evidence build |
| Two-Mac Art room video and Drawpile | two physical machines, real observation |
| Live Music AI | needs a real service credential |
| Physical and hardware checklist rows | real musician observation against an exact package |

## 4. Docs pass

One docs-only pass over `USER_GUIDE.md`, `README.md`, `QUICK_HELP_MAP.md`,
`CHANGELOG.md`, and `HELP_ROUTING_MAP.md`:

1. Musician-visible names read **Art** and **Music**. No "Studio Visit" wording
   survives #15; its branch name and profile test still carry the old name.
2. Webex stays native, external, and optional. No add-on, no embedded-app
   promise.
3. KISS. No integration wall and no feature matrix a musician has to read
   before playing.
4. Keep the implemented / planned / automated-only / physical / **NOT RUN**
   boundary the [documentation rules](README.md#documentation-rules) already
   require.
5. `CHANGELOG.md` gets one `Unreleased` entry per landed PR. Never edit a
   released section.

A rename that stops at those five files leaves the rest inconsistent. #15 also
changes `ARCHITECTURE.md`, `CREATIVE_MODES_MVP_SPEC.md`, and
`docs/PROJECT_BRIEF.md`, and #17 brings its own decision records, so apply the
same naming and KISS rules wherever those PRs touched a musician-visible name.

## 5. Who merges

Jeff presses merge, one PR at a time, and only when that step's gate in
section 1 is met. Bob prepares branches, rebases, pushes, and reports; Bob does
not merge unattended, does not tag, and does not publish. Rebases go to the
branch they belong to — no force-push over someone else's product branch.

`tests/test_merge_and_release_map.py` keeps the job names and the docs list on
this page in step with `.github/workflows/ci.yml` and the repository.
