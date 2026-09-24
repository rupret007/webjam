# PM3 baseline audit

PM3 began from OPEN DRAFT #148 `844ea1080500fc937101b19b21168b5a5525600c`.
Remote master remains `a51154e533ce5662ca4b541a866dd9b1954452b1`.
The tracked tree was clean; six existing `codex-*.png` user images were left alone.

| Held draft | Exact audited tip | Boundary |
| --- | --- | --- |
| #145 | ec6fac77bd9ec477bfefd64bb07b8ca1d6cb478a | Separate Art local-file/YouTube Paint along source and specific lesson/retry copy; not part of this stack. |
| #146 | 023c6f7716aa8bdbcffd95a896a6fe7f5e541774 | Recording safety, retained Notes, ownership and recovery. |
| #147 | 78a24d75bd1dc046c59452de59e9702e907851c4 | Recording and local-save next actions, compact recovery. |
| #148 | 844ea1080500fc937101b19b21168b5a5525600c | Readable Notes and optional Session details; explicitly ranks embedded Studio layout first among leftovers. |
| #37 | 5689da764127100593b9486d1be3eba9e54b9838 | Parked; untouched. |
| #49 | e6ece405012b4bc167702ea62986e1c22f31a0a8 | Parked; untouched. |

All six were OPEN DRAFT. Their current hosted status checks contained no failure or pending conclusion at audit. PM3 uses a new branch above #148; prior tips are not updated.

## Proven new gap: compact embedded Studio

Fresh actual full-app renders at 760×600 show Studio receiving about 407px of height. Existing fixed chrome and simultaneous Arrange/mixer minima consume that height: only the Arrange ruler fits, with overlapping controls and clipped mixer. Existing standalone 760×600 tests did not reproduce this smaller embedded area. The issue also exists at 1000×740 and grows with enlarged text. Failed-save guidance shares the same crowded surface. This is the explicit leftover from #148, not a regression introduced by #148.

## Art audit boundary

Existing Make together and connected-room guidance already accept own-space art: paper, clay, model, printer, or usual app. This is already asserted by room-overview and journey tests. Exactly two launch doors are already correct. #145 separately fixes Conversation's configured/unconfigured next-click label and source-specific Open lesson / local-copy guidance. PM3 must preserve those choices and avoid duplicating their implementation. Review the PM3 change evidence for the proven remaining in-room/optional-canvas gap.

## Release and coordination boundary

BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5785729494
Single Astra lease; no Cloud lease. Latest is release 393030220, tag v0.28.1, with 8 immutable assets. Their identities and SHA256 digests were captured for final comparison. No merge/tag/release/deploy/spend/signing/notarization or physical acceptance is authorized or claimed.
