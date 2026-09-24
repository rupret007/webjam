# Show-wave integration review

Marker: `WEBJAM_SHOW_WAVE_FINAL_BUILD_20260924`.

Jeff authorized integration, fixes, merges, and an unsigned test candidate in
the existing checkout. This replaces the earlier per-tip Bob/Karen stop; the
older PR review packets remain historical evidence, not current approvals.
No human QA or Karen approval is claimed here. Publishing, tags, Latest,
Developer ID signing, notarization, and Pages remain outside this pass.

## Integrated inputs

- Master: `da16749abc9f87cde127e01ea10a7712e5ce7800`.
- #149 Art/Studio polish: `3af81ddfe84bd501491c641cda0e069bc344d112`.
- #152 show docs: `05d46dd6f934828e65736950fe55176fe5295a84`.
- #153 profile-specific Help: `6cceb1f0bfc7bbbfe85895888c259136c8a4e50b`.

All three heads are retained through merge ancestry. The only merge conflict
was the Unreleased changelog; both entries were retained. SHOW_ONEPAGER.md and
DEMO_SCRIPT.md remain byte-identical to #152.

## Integration correction and review

Help now uses a parentless application-modal dialog with a read-only scrolling
body and a fixed OK footer. It fits the selected screen's available geometry,
accounts for native frame margins, and returns focus to the originating
control. The canonical trefoil and profile-specific text remain unchanged.
Links are disabled; the body is static application text. Opening Help does
not launch services, open URLs, change projects, or change recording ownership.
About retains its existing implementation.

The combined review checked Art's optional external-tool boundaries and
Studio's responsive layout, keyboard traversal, and revision-guarded output
reveal. The changes do not introduce a new audio, network, persistence, or
recording owner. Two imported pytest fixtures now use explicit re-exports,
clearing the existing eight full-test-tree Ruff findings without changing
fixture behavior.

Package review also found that the macOS install guide still called v0.28.1
Latest and ambiguously applied Review & Rehearsal's project restrictions to
Music. The guide now directs installers to the package's exact build metadata
and checksum receipt, distinguishes the Music and Preview workflows, and keeps
the old release facts under an explicitly historical heading. The earlier
integration gate was superseded for this copy correction, not reported green.

## Pre-freeze evidence

- Focused Help, widget, brand, dispatcher, and affected fixture tests:
  **181 passed, 52 subtests passed**.
- Native Cocoa Help regression: **9 passed**. Music and Art, F1 and More,
  normal and 22px text, 760×600 available-screen geometry, scrolling to both
  ends, keyboard dismissal, focus return, and primary-display fallback.
- Full CONTRIBUTING Ruff scope and diff whitespace checks passed.
- Native renders were inspected at enlarged text: all text is reachable by
  scrolling and OK stays visible. These are synthetic app windows, not a
  physical audio or human usability sign-off.

Initial failures are retained in ignored `out/show-wave/final-build/` logs:
an inherited C++ method mock needed an explicit Python capture callable;
macOS scrolling required the advertised Page Up/Down keys; native activation
needed to settle before F1. Native keyboard testing also found a real product
issue: Cocoa skipped the default TabFocus OK button. Explicit StrongFocus
fixed this, and the complete native suite passed without relaxing geometry,
scrolling, state-preservation, or focus assertions. Fresh-process renders
avoided a renderer artifact after repeatedly closing the last Qt window.

## Qualification and remaining checks

The integration PR body records the frozen tip's full local and hosted gate
results. The final build receipt must separately qualify the resulting master
SHA, verify the downloaded macOS arm64 artifact and packaged smokes, and give
Jeff installation instructions and an under-ten-minute acceptance checklist.
Pre-freeze checks and historical PR greens cannot substitute for those gates.

Physical audio, a real remote band, external meeting behavior, physical mobile
devices, Logic import, and human QA must retain their actual test status.
The packaged candidate remains unsigned/ad-hoc test-only and unnotarized;
published Latest remains v0.28.3.
