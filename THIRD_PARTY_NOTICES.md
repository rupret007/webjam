# Third-Party Notices

WebJam's own code is MIT-licensed (see `LICENSE`). This file covers
third-party software that WebJam **bundles** (ships inside its own
installers/artifacts) rather than merely depending on at build time.

## Inter typeface

WebJam bundles the [Inter](https://rsms.me/inter/) typeface — source at
[rsms/inter](https://github.com/rsms/inter) — as the app's UI font on
every platform.

- **Version bundled:** `4.1` ([release](https://github.com/rsms/inter/releases/tag/v4.1))
- **Files:** `Inter-Regular.ttf`, `Inter-Medium.ttf`, `Inter-SemiBold.ttf`,
  `Inter-Bold.ttf`, shipped unmodified at `webjam_qt/theme/fonts/` inside
  every WebJam build.
- **License:** SIL Open Font License 1.1, which permits bundling and
  redistribution with software. The full license text is included verbatim
  at [`licenses/INTER_OFL.txt`](licenses/INTER_OFL.txt). The font is not
  modified and keeps its Reserved Font Name "Inter".

## Jamulus

WebJam bundles the official [Jamulus](https://jamulus.io)
client — source at [jamulussoftware/jamulus](https://github.com/jamulussoftware/jamulus)
— to remove the "go install Jamulus yourself" step for most users.

- **Version bundled:** `3.12.2` (tag [`r3_12_2`](https://github.com/jamulussoftware/jamulus/releases/tag/r3_12_2))
- **License:** GNU General Public License v2 (core), with Jamulus's own
  new contributions moving toward AGPL v3.0. Bundled components (OPUS,
  STK) carry their own permissive licenses. The full license text as
  shipped by the Jamulus project is included verbatim at
  [`licenses/JAMULUS_COPYING.txt`](licenses/JAMULUS_COPYING.txt) and is
  also placed alongside the bundled copy in every WebJam build (macOS:
  `WebJam.app/Contents/Resources/THIRD_PARTY_LICENSES/`; Windows:
  `_internal/Jamulus/` next to the bundled installer; Linux: the visible
  archive-root `Jamulus/` directory next to the bundled `.deb`).
- **How it's bundled, per platform:**
  - **macOS:** `Jamulus.app` from Jamulus's official release `.dmg` is
    nested inside `WebJam.app/Contents/Resources/Jamulus.app`. WebJam's build
    replaces the nested app's code signature with an ad-hoc signature that
    omits App Sandbox; this lets WebJam provision private, loopback-only
    JSON-RPC credentials without asking musicians for Full Disk Access. The
    upstream executable and framework contents are otherwise unchanged.
    WebJam launches it as a separate OS process and talks to it only over
    its public JSON-RPC/UDP interfaces — it is never linked against or
    merged into WebJam's own code.
    `JamulusServer.app` from the same release is prepared the same way and
    nested beside the client so a designated host needs no separate install.
    WebJam launches each as an independent process.
    The resulting nested apps, and the private WebJam artifact that contains
    them, are ad-hoc signed and are **not notarized**; do not describe the
    prepared copies as upstream-notarized nested apps.
  - **Windows:** Jamulus only publishes an NSIS *installer* executable
    (no portable binary), so Windows packaging carries the unmodified installer
    as a distribution dependency. On a clean Windows system, the Host/Join
    dialog offers an Install Jamulus action only after WebJam verifies the
    exact pinned filename and SHA-256; it verifies the file again immediately
    before launch. The upstream installer itself is unsigned, so its UAC
    publisher warning remains a separate release-certification disclosure.
  - **Linux:** the Ubuntu x86-64 build carries the official unmodified `.deb`
    as a distribution dependency plus a visible install helper. It is a
    join-only build certified only for Ubuntu 22.04 x64; other Ubuntu versions
    and Linux distributions are not certified. The helper installs Jamulus as
    `/usr/bin/jamulus`, which WebJam discovers without a custom path.
- **Why this doesn't require relicensing WebJam:** Redistributing a
  third-party binary that is invoked as a separate process
  (never statically or dynamically linked into WebJam's own executable)
  is "mere aggregation" under GPL §2 — it does not bring WebJam's own
  code under the GPL. WebJam communicates with the Jamulus process
  exclusively via `subprocess.Popen` plus its public JSON-RPC/UDP
  protocols, the same way it would talk to a separately-installed copy.
- **Escape hatch:** source runs can use an installed/custom Jamulus path.
  Frozen macOS builds deliberately prefer their prepared, pinned bundled copy
  so an incompatible installed version cannot silently replace it.
- **Staying current:** because the bundled copy's version is pinned to
  WebJam's own release cadence, a security or bug fix released upstream
  by the Jamulus project won't reach bundled-copy users until the next
  WebJam release. The manual-path override above remains available if
  you need a newer (or different) Jamulus version sooner.

## Pocket Stage Python runtime dependency inventory

The Pocket Stage source and frozen-application specification use the following
Python runtime packages. Versions are pinned in
`requirements-lock/release-constraints.txt` and the target-specific hash locks:

- [`cryptography` 48.0.1](https://github.com/pyca/cryptography) — ephemeral
  certificate/key creation and certificate serialization. License:
  [Apache License 2.0 OR BSD 3-Clause License](https://github.com/pyca/cryptography/blob/main/LICENSE);
  WebJam redistributes it under the verified BSD option copied from the pinned
  distribution at [`licenses/CRYPTOGRAPHY_LICENSE.txt`](licenses/CRYPTOGRAPHY_LICENSE.txt).
- [`websockets` 16.1.1](https://github.com/python-websockets/websockets) —
  secure WebSocket runtime support used by the Pocket Stage gateway stack.
  License: [BSD 3-Clause License](https://github.com/python-websockets/websockets/blob/main/LICENSE),
  with the verified pinned-distribution text at
  [`licenses/WEBSOCKETS_LICENSE.txt`](licenses/WEBSOCKETS_LICENSE.txt).
- [`Segno` 1.6.6](https://github.com/heuer/segno) — QR-code rendering in the
  desktop pairing dialog. License:
  [BSD 3-Clause License](https://github.com/heuer/segno/blob/master/LICENSE),
  with the verified pinned-distribution text at
  [`licenses/SEGNO_LICENSE.txt`](licenses/SEGNO_LICENSE.txt).

This section is a dependency and license inventory, not an assertion that the
Pocket Stage developer preview is present in the already-published v0.18.1
packages. The links above identify the upstream projects and authoritative
license text. The three local copies were taken from the exact installed pinned
distributions and the frozen-application specification is configured to place
them in `THIRD_PARTY_LICENSES`; this does not claim that an already-published
artifact contains them. Any future promoted package must inventory its exact
dependency graph and verify the staged license files during package inspection.

## WebJam fabric transport

WebJam bundles `webjam-fabric`, its statically compiled native transport
sidecar, as a separate executable beside the desktop application. The binary
links pinned Pion ICE/STUN/TURN/transport modules, quic-go, and their permissive
transitive dependencies.

- **Exact versions and linked inventory:**
  [`transport/DEPENDENCIES.md`](transport/DEPENDENCIES.md)
- **Attribution and license mapping:**
  [`transport/NOTICE.md`](transport/NOTICE.md)
- **Full license texts:** [`transport/licenses/`](transport/licenses/)

The notice, inventory, and license texts are included in every desktop bundle's
`THIRD_PARTY_LICENSES` data. `go.sum`, `go mod verify`, `go mod tidy -diff`, the
race suite, the vulnerability audit, and a linked-binary dependency inventory
are release gates.

## VB-CABLE (Windows audio routing)

WebJam bundles the VB-CABLE installers (see `VB/readme.txt` for VB-Audio
Software's own license terms) under the terms of that EULA, which
explicitly permits redistribution alongside another application's
installer.

## Webex

WebJam opens Cisco Webex externally for native speech/video. Webex itself is
not bundled. The deprecated Guest Issuer flow is not used or configured by
the current application; legacy source remains only for compatibility review.
