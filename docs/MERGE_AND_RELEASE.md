# Merge and release map

> **Published testing boundary:** GitHub **Latest** is immutable unsigned/ad-hoc
> v0.27.2 release `379360694`, published at `2026-08-30T18:06:14Z` from exact
> commit `9c6ca3de96aa7eb261c65b7dee768ab48144169c`. Lightweight tag `v0.27.2`
> resolves directly to that commit. The release has seven packages plus
> `WebJam-v0.27.2-SHA256SUMS.txt`. Merged source-only review
> [#60](https://github.com/rupret007/webjam/pull/60) is on `master` at
> `a3079a0040ce52b5834e3a3959819c09fc3f0e6f`; that review and later source
> changes are not among those checksum-bound packages.
> The existing exact Jamulus 3.12.2 and 3.12.3 records are explicitly approved
> through v0.27.2, so Host/Join and required component-input CI are
> source-eligible. The signed public catalog remains sealed at exact WebJam
> v0.22.5.
>
> **Red tag-run boundary:** workflow `33327104322` passed tests, integrations,
> update-input checks, and all four desktop builds. Its publisher then failed
> closed because the tag is lightweight rather than annotated. The overall run
> is red and is not publish-green. Preserve it; do not rerun the job, move or
> replace `v0.27.2`, or mutate release `379360694`. v0.27.1 remains immutable
> historical evidence. Do not restack #37 or #49. Do not invent a signed catalog.
> Do not add a version-specific publisher with invented pins.

The record of the finished product land and the honest boundaries that remain.
#14 (audio core), #15, #16, #19 (Art), and #17 (Music song tools) are all
already on `master`; #17 merged 2026-08-22 as `5ca6ba5`. There is no open
product branch.

`master` is the default branch and the only ship target. `main` is a stale,
non-authoritative mirror and must not be described as synchronized. Master run
`33317581250` is green on exact release commit `9c6ca3d`; tag run `33327104322`
is red only at the annotated-tag publisher boundary described above. Neither
result may be rewritten. A new pull request still must pass this repository's
own complete suite on its exact head. A gate with no evidence stays **NOT RUN**;
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

Standing procedure after the completed product land:

| Step | Action | Who | Gate before it happens |
| --- | --- | --- | --- |
| 1 | #37 and #49 stay parked | nobody | they are parked outlines, not scheduled work — do not restack, rebase, or "fix" them |
| 2 | Leave v0.27.1, tag `v0.27.2`, and release `379360694` alone | nobody | do not retag, replace, or mutate immutable evidence |
| 3 | For any later source-only correction, start from current `master` | Codex | no product code and no release mutation |
| 4 | Run the complete local suite once on the exact correction head | Codex | red stops; no retry to change a result |
| 5 | Open one draft PR for Karen | Codex | exact base, files, and verification are recorded |
| 6 | Stop without merging, tagging, publishing, or altering releases | Codex | Karen and Jeff retain the attended review/merge decision |

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

Jeff named and published the full unsigned v0.27.2 test round from exact
`9c6ca3de96aa7eb261c65b7dee768ab48144169c`. Release `379360694` is immutable
GitHub Latest with seven packages plus its checksum manifest. The lightweight
tag is a real immutable ref, but it does not satisfy the workflow's annotated
tag policy. Tag run `33327104322` therefore remains red at its publisher even
though all ordinary test, integration, and package-build jobs passed.

A post-release source-truth draft does not repair the historical tag run,
replace the lightweight tag, mutate a release, or turn later source into
released package evidence.

### Observed v0.27.2 inventory

The immutable release contains exactly these seven packages:

- `WebJam-linux-x64.zip` — `d57577851072dc5548f4ee6da1c6d829c469cd258096db6a179551418081034c`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip` — `4c9d737029b9f9fb9d938474ec0ab07f930625221f2ce3b6af3d932799a46a25`
- `WebJam-v0.27.2-macos-arm64-ADHOC-TEST-ONLY.dmg` — `84ed1a8b5e9f53d27ee78724c5952e0e69ede2cfba6fa32f312136211660cea1`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip` — `3c3c7c4b7ae87873463ab483eb6135a1387ef36d35dcff4c011b1357a5164217`
- `WebJam-v0.27.2-macos-x64-ADHOC-TEST-ONLY.dmg` — `6033e58f6963bf3569cde6d7c5ffee683f38e2f64ad53a3774f72aab17b8af22`
- `WebJam-v0.27.2-windows-x64-UNSIGNED-TEST-ONLY-setup.exe` — `ca62d1f3c5cd5647548d86d0195e26383b0c0aba08a78a13742d6e3aa799c80c`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip` — `c40448371bc56f66211c5479f21753cef9a5e5d7e55dc3d8b4e62893d8cfee2c`

`WebJam-v0.27.2-SHA256SUMS.txt` contains exactly those seven lines and has
SHA-256 `05da698d6d5c2b13387620a7c2ff2d5611782f08769a15bc9e96327fa08588ab`.

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
| `Publish GitHub Release` | existing tag run `33327104322` failed its annotated-tag check; a docs PR does not rerun or publish |
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
5. Keep #58's **Art starts with fewer choices** entry in the released v0.27.2
   section. `Unreleased` is for work after that exact tag. Never rewrite the
   released v0.27.1 feature history.

## 6. Who merges

Jeff presses merge, one PR at a time, and only when that step's gate in
section 2 is met. For any source-only correction, Codex prepares an isolated
branch, pushes, and reports; Codex does not merge unattended and does not tag,
publish, or alter a release from that pull request. Rebases go to the branch
they belong to — no force-push over someone else's product branch.

`tests/test_merge_and_release_map.py` keeps the doors, job names, and docs list
on this page in step with `.github/workflows/ci.yml` and the repository.
