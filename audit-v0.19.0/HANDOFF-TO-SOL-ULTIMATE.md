# Handoff — WebJam owner-device certification

**From:** Claude Opus 5 (Cowork, computer-use session)
**To:** 5.6 Sol Ultimate
**Date:** 2026-07-26
**Machine:** Jeff's Mac mini (2023), Apple M2, 8 GB, macOS Sequoia 15.7.8
**Repo:** `/Users/jeffstory/Documents/WebJam 2` (branch at handoff: `feat/v023-seamless`, HEAD `ac0ed48`, version **0.22.2**)
**Audit branch:** `claude/webjam-v0191-owner-device-certification`
**Evidence:** `audit-v0.19.0/` (gitignored via `audit-v*/`; the two `.md` files were force-added)

---

## 1. What I was asked to do

A full owner-device certification of **WebJam v0.19.0**: install the real DMG on
this Mac (Phase 1), run a complete black-box UI audit before reading any source
(Phase 2), then fix reproducible defects on a certification branch with
regression tests (Phase 3).

## 2. What I actually completed

**Phase 1: mostly complete, and it terminated in a genuine blocker.**
Phases 2 and 3 were **not started**. Read `PHASE1-INSTALLATION.md` for the full
report; the short version:

| Step | Result |
|---|---|
| Architecture confirmed (M2 → arm64) | PASS |
| DMG downloaded, SHA-256 verified against published manifest | PASS — exact match |
| DMG inventory (all 7 items, candidate metadata, Pocket Stage Xcode project) | PASS |
| Guided installer `Install WebJam.command` | **FAIL — blocked, could not run** |
| Installed identity for 0.19.0 | NOT ESTABLISHED (install never completed) |
| Everything in Phase 2 and Phase 3 | NOT RUN |

**Findings raised:** WJ-001 (High, installer unreachable), WJ-002 (Medium,
unconfirmed version-string mismatch), WJ-003 (Low, config scattered in `~`).

## 3. The one finding that mattered — and its status

**WJ-001.** On a stock macOS 15 Mac, a browser-downloaded v0.19.0 DMG carries
quarantine. Double-clicking `Install WebJam.command` is blocked by Gatekeeper
("Not Opened… Apple could not verify", offering only *Done* / *Move to Trash*).
The recovery documented in that build's `READ ME FIRST.txt` — Control-click →
Open — hits the identical block, because macOS 15 removed that bypass for shell
scripts. No "Open Anyway" row ever appears in Privacy & Security, since that
mechanism covers app bundles only. And the advanced
`Install WebJam - Remove Quarantine.command` is itself a quarantined `.command`
on the same image, so the escape hatch is blocked by the exact condition it
exists to remove. **Net: v0.19.0 could not be installed by following its own
instructions.**

**This is already fixed at HEAD.** v0.20.0 made drag-to-Applications the primary
documented path, added an explicit warning that macOS blocks quarantined
`.command` files without the Open Anyway approval, and documented invoking the
helpers deliberately from Terminal. The maintained `READ ME FIRST.txt` and the
v0.20.0 CHANGELOG entry both confirm it. **You do not need to fix this.** It is
retained as evidence about the published v0.19.0 asset only.

## 4. Why I stopped rather than pushing through

Two reasons, and the second is the one that should shape your plan.

**(a) The install needed Jeff's decision.** The brief forbids using the
quarantine-removal helper without explicit approval, and guided installation had
failed. Legitimately his call.

**(b) I cannot see or click macOS security dialogs. This is the important one.**
Gatekeeper prompts, and TCC prompts for **Microphone** and **Local Network**, are
drawn by system agents (`CoreServicesUIAgent` and friends) that cannot be added
to the automation allowlist. They are invisible in automated screenshots and
unclickable. I spent several cycles misreading the Gatekeeper block as a "silent
no-op" and nearly filed a false blocker; Jeff sent a screenshot of the dialog and
corrected me. I then built a control script that wrote a file as proof of
execution, which is how I isolated the cause to quarantine specifically.

**Consequence for you:** WebJam is an audio + networking app. Its first-run
experience is gated behind exactly the prompts I cannot see. Any agent driving
this audit through computer use will stall at the same wall unless permissions
are pre-granted or a human clicks them.

## 5. Method notes worth inheriting

- **Verify checksums in the Linux sandbox through the mounted folder.** `sha256sum`
  over `/sessions/.../mnt/Downloads/` works and needs nothing from the user.
- **Use file side effects as ground truth when the screen lies.** A script that
  writes `proof.txt` told me what screenshots could not. Reuse this pattern for
  anything whose outcome you cannot see.
- **Always run a control experiment before blaming the product.** My trivial
  `ControlTest.command` is what separated "WebJam is broken" from "this file is
  quarantined". Without it I would have shipped a false High finding.
- **Terminal is click-only for agents** — no typing, no paste. Host shell commands
  must be run by Jeff or replaced with sandbox/GUI equivalents.
- **Do not copy `.webjam_jsonrpc_secret`** into the repo working tree. I backed up
  six WebJam preference files to `audit-v0.19.0/config-backup/` and deliberately
  excluded that credential and the ~4.5 MB of rotated logs.

## 6. What I think you should do next

Ordered by value. My honest view: **the v0.19.0 certification is no longer the
right target.** HEAD is 0.22.2, three minor versions on, and the one blocker I
found is already fixed. Re-running Phase 1 against v0.19.0 would certify a build
nobody should install.

1. **Re-scope to the current candidate.** Certify what Jeff will actually ship —
   0.22.2 or the in-flight `feat/v023-seamless` — not 0.19.0. Confirm this with
   him first; it is a change to the original brief.
2. **Settle the permissions question before touching the UI.** Either have Jeff
   pre-grant Microphone and Local Network to WebJam and accept that the first-run
   permission experience is marked NOT RUN, or have him on hand to click prompts
   so those gates can be genuinely observed. Decide up front; it determines what
   your coverage matrix can legitimately claim.
3. **Then run Phase 2 properly** — the full black-box inventory, before reading
   source. Nothing of Phase 2 exists yet; you are starting clean, which is
   actually the ideal condition for an unbiased audit. Resist reading
   `webjam_qt/` until the inventory is done.
4. **Confirm or kill WJ-002.** Does the session window title report the real
   `__version__`? I saw a window titled *v0.15.0* while `/Applications/WebJam.app`
   reported *0.18.1*. That is either two copies of the app on disk or a stale
   title string. One launch of a known build settles it. Cheap, and a wrong
   version in the title bar would undermine every version claim in the audit.
5. **Consider WJ-003** (13 dotfiles in `~`, including a credential and rotated
   logs, while the app already uses `~/Library/Application Support/WebJam/` for
   takes and the RPC secret). Low severity, real inconsistency, and a migration
   is a breaking change for existing users — worth a deliberate decision rather
   than a drive-by fix.
6. **Optional, still open:** ship the installer helpers as ad-hoc-signed `.app`
   bundles so they reach the Open Anyway flow instead of being unreachable by
   double-click. v0.20.0 routed around the problem with documentation; this would
   remove it.

## 7. Physical gates neither of us can close

These must be labelled USER CONFIRMATION REQUIRED and never marked PASS from
automation:

- Real audio in/out, device selection, latency, monitoring, subjective sound quality
- Real multi-user networking and a real Jamulus server session
- **Jamulus is not installed on this Mac** (`/Applications/Jamulus.app` absent),
  and the config still points at `127.0.0.1`
- **Xcode is not installed**, so the entire Pocket Stage iOS Simulator flow is
  unreachable here — the shipped `.xcodeproj` was verified as *present and
  internally consistent*, nothing more
- Gatekeeper approval, Microphone and Local Network grants, sleep/wake
- Physical iPhone pairing

## 8. Questions for Jeff that the brief did not cover

Worth raising before you invest in a long run:

1. **Which build is the certification target now** — 0.19.0 as written, 0.22.2, or
   `feat/v023-seamless`? This changes everything downstream.
2. **Is `/Applications/WebJam.app` still 0.18.1, or did you install something since?**
   I recorded 0.18.1 on 2026-07-21; two days of work have landed since.
3. **Do you want v0.19.0's published assets marked "do not install"** on the GitHub
   release, given they cannot be installed by their own instructions? A one-line
   note on the release page would save a band member an unpleasant evening.
4. **How much of your own time are you willing to spend in the loop?** A genuine
   certification of an audio app needs a human for the permission prompts and every
   audible judgement. If the answer is "very little", the honest deliverable is a
   coverage matrix with a large NOT RUN column, and that should be agreed up front
   rather than discovered at the end.
5. **Is `audit-v*/` meant to stay gitignored?** I force-added the two markdown
   reports so the findings survive; the config backup and binaries stayed ignored.
   Say if you would rather these live somewhere tracked, e.g. `docs/audits/`.
6. **The `.webjam_jsonrpc_secret` question** — it sits in `~` in plaintext. Not
   exploited, not copied, but worth a deliberate decision about permissions and
   location.

## 9. State of the machine at handoff

- `/Applications/WebJam.app` — **untouched**, still 0.18.1. Nothing was installed,
  replaced, or removed.
- WebJam preferences — **unchanged**, with a backup in `audit-v0.19.0/config-backup/`.
- `WebJam 0.19.0` DMG — may still be mounted at `/Volumes/WebJam 0.19.0`; eject when convenient.
- `~/Downloads` — contains the verified v0.19.0 DMG and a stray copied
  `Install WebJam.command` from my permission test. Safe to delete.
- `audit-v0.19.0/quarantine-test/` — throwaway control-experiment artifacts. Safe to delete.
- Nothing was pushed. No PR was opened. No release was touched.
