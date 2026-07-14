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
  `Jamulus/` next to the bundled installer).
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
  - **Windows:** Jamulus only publishes an NSIS *installer* executable
    (no portable binary), so Windows packaging carries the unmodified installer
    as a distribution dependency. The current v0.12 Host/Join flow does not
    invoke the former Setup Wizard installer button; Windows packaging and
    install behavior remain a separate release-certification gate.
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
