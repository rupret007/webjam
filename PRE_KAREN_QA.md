# Pre-Karen QA — Paint along timeline input

Exact master base `12920ebe35b96298e2c2b97fdcf78efc1d1720f7`; branch
`codex/webjam-finish-product-paint-along-seeking`. Canonical checkout:
`/Users/jeffstory/Documents/WebJam`. BEFORE:
https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5563756244.
Marker `OVERNIGHT_NEXT_ART_DOOR_20260906_2020`.

## Product and leftover review

Host keyboard and wheel timeline changes now seek the existing silent player
and publish its real position. Mouse drag updates remain local until release.
Snapshot rendering blocks both range and value signals. Host source identity
and duration changes disarm an old gesture before rendering the new truth;
failed/unshared/zero-duration states cannot seek. Hiding the view cancels a
held gesture without leaving the timeline stuck on return.

Native macOS changes value before Qt's sliderPressed signal in some styles.
The small slider subclass brackets the actual pointer event, ensuring that
native click/drag behavior also commits once. The dialog remains a renderer;
role, source, seek validation and publication stay with existing owners.

## Privacy and scope

No new log sink, payload field or exception text. The private source identity
stays inside the view; diagnostics/public projections are unchanged. No
second player, video upload/download, browser/meeting launch, network gate,
room lifecycle, Art door copy or asset change. Guests stay unable to seek.
Local host control remains available during retained recovery ownership.

## Verification

Real Qt fixture baseline: 10 failed, 5 passed before the fix. Additional native
Mac testing caught and verified the early mouse-press issue. Final input
coverage has 21 cases, including actual synchronous coordinator/player/peer
callbacks in READY, PLAYING and PAUSED states, passive render, keyboard,
wheel, mouse, source replacement, failure/recovery and hide/return.

Final focused/full suite results, native count, independent review, exact
head/tree and hosted tests/integrations/four desktop artifacts are recorded
in the OPEN DRAFT and coord AFTER. No pending test is called green. Jeff's
Apple Silicon test artifact must carry the exact draft commit. A separate
app folder still uses the user's ordinary WebJam settings and notes when
launched; it is not an isolated installation.

## Holds and preserved work

#85/#84 remain merged and untouched. Parked #37/#49 unchanged. One OPEN DRAFT
for Karen; no merge/tag/sign/Pages/Release Trust/publish/GitHub Latest. The
existing unsigned/ad-hoc 0.27.2 private-test boundary stays Jeff-only. No
short-code/public rendezvous/live Cisco/second video stack/other-repo task.
Physical, two-device, installed-app and live-provider checks remain NOT RUN.

The separate logging/support improvement is saved locally at
`94c621e36ebe3ff7e2748b91d8a968021c30da92` on
`codex/webjam-finish-product-test-readiness`. Its 23 new regressions and 2,271
focused tests passed; full pytest was intentionally interrupted when Jeff
steered this task to Art. That checkpoint has no PR and is excluded here.
