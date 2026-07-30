# ADR 0008: macOS Jamulus updates require an integrated execution contract

Status: Accepted for the v0.22.3 safety patch; integrated updater asset deferred

## Context

Jamulus 3.12.3's official macOS DMG is pinned by exact size and SHA-256 and its
client and server apps are Developer-ID signed and notarized. Those upstream
apps also carry App Sandbox. WebJam launches Jamulus with private profile,
JSON-RPC secret, and recording paths owned by WebJam. An untouched sandboxed
app cannot be assumed to read or write those paths.

A signed WebJam catalog proves which upstream bytes were approved. It does not
prove that those bytes satisfy WebJam's different runtime-file contract, and a
catalog capability string cannot replace a live entitlement/signature check.

## Decision

- Upstream macOS `PLATFORM_APPROVAL` entries remain exact source/download
  evidence. They advertise audio, JSON-RPC protocol, and native-GUI facts only;
  they do not advertise `webjam-route-profile` or `recording`.
- An upstream Mac app is never returned by the managed-runtime provider and
  never preempts the release-integrated Jamulus 3.12.2 fallback.
- Runtime verification reads the exact signed entitlements at point of use and
  records a typed, path-free source execution contract. `official-source`
  contracts are never activatable, whether App Sandbox is present or absent.
- Older signed catalogs that differ from baked policy only by the two retired
  Mac capability overclaims are narrowed to baked policy. Any other identity
  conflict still fails closed.
- Updater selection and Bridge execution use the same integrated-runtime
  predicate and feature gate. A future `webjam-integrated` handoff must also
  carry a freshly proven execution-contract fact; neither the variant name nor
  catalog capabilities can activate a runtime by themselves.
- The updater reports `macos-integrated-runtime-required` with no Download,
  Install, Activate, or Rollback action. It does not call a source-only pointer
  active or up to date.

## Required follow-up for an activatable macOS update

The release pipeline must produce separate `webjam-integrated` client and
server assets for both `macos-arm64` and `macos-x64`. For each role/target it
must:

1. Start from the catalog-pinned upstream artifact and verify its SHA-256,
   Developer ID team, bundle ID, notarization, version, architecture, and
   complete source tree before normalization.
2. Normalize in CI, never on an end-user machine. Sign every Mach-O leaf and
   nested bundle inside-out with the reviewed Jamulus entitlement policy
   (audio input only), no App Sandbox, no debug entitlement, and hardened
   runtime. Use the release's declared signing mode: exact ad-hoc identity for
   an explicitly labeled test candidate, or Developer ID for a trusted
   release. Never use `--deep` as the signing method.
3. Emit a canonical complete runtime inventory (path, type, mode, size,
   SHA-256, symlink target), its digest, source-artifact digest, source-tree
   digest, role, target, version, bundle ID, entitlement digest, signing
   identity, and derivation-tool revision. Sign this derivation metadata as
   part of the component catalog/release evidence.
4. Package client and server as distinct immutable managed artifacts. The
   catalog must use variant `webjam-integrated`, activation mode `managed`, an
   exact runtime inventory, and capability `webjam-integrated-runtime` plus the
   role's required WebJam capabilities.
5. Add a store/verifier dedicated to that schema. It must verify every runtime
   file, reject special/escaping/symlinked paths, verify deep strict code
   signing, exact entitlements, identity, architecture, and version, and repeat
   those proofs immediately before each launch.
6. Install under a new versioned store directory, update current/previous
   pointers atomically while holding the existing operation/runtime locks, and
   retain the release-integrated fallback. Prove rollback after copy, signing,
   receipt, pointer-write, and crash-injection failures.
7. Launch every client/server/headless role with dynamic-loader and Qt/QML
   plugin injection variables removed. Test wrong publisher, wrong target,
   altered inventory, escaping symlink, partial signing, entitlement drift,
   stale catalog, concurrent session/update, rollback, and both Mac
   architectures before enabling the runtime verifier feature gate.

Until every item above is implemented and physically verified, the macOS
integrated-runtime verifier feature gate remains off.
